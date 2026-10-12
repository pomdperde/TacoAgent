from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import messages_from_dict, messages_to_dict
from langgraph.graph.message import add_messages

VALID_CHECKPOINT_MODES = {"light", "strict", "off"}


def normalize_checkpoint_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    return value if value in VALID_CHECKPOINT_MODES else "light"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def workspace_manifest(workspace: Path) -> list[dict[str, Any]]:
    manifest = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if relative.parts[:1] in {(".git",), (".tacoagent",)}:
            continue
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError:
            continue
        manifest.append(
            {
                "path": relative.as_posix(),
                "size": stat.st_size,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return manifest


def _git(args: list[str], *, git_dir: Path, workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={workspace}", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def snapshot_workspace_git(workspace: Path, root: Path) -> str | None:
    """Commit a workspace snapshot to an isolated checkpoint Git directory."""
    git_dir = root / "workspace.git"
    if not git_dir.exists():
        try:
            initialized = subprocess.run(
                ["git", "init", "--bare", str(git_dir)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if initialized.returncode != 0:
            return None
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(".git/\n.tacoagent/\n", encoding="utf-8")
    try:
        if _git(["add", "-A", "."], git_dir=git_dir, workspace=workspace).returncode != 0:
            return None
        changed = _git(["diff", "--cached", "--quiet"], git_dir=git_dir, workspace=workspace)
        head = _git(["rev-parse", "HEAD"], git_dir=git_dir, workspace=workspace)
    except (OSError, subprocess.SubprocessError):
        return None
    if changed.returncode == 0 and head.returncode == 0:
        return head.stdout.strip()
    try:
        commit = _git(
            [
                "-c", "user.name=TacoAgent Checkpoint",
                "-c", "user.email=checkpoint@tacoagent.local",
                "commit", "-m", f"checkpoint {_now()}",
            ],
            git_dir=git_dir,
            workspace=workspace,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if commit.returncode != 0:
        return None
    try:
        result = _git(["rev-parse", "HEAD"], git_dir=git_dir, workspace=workspace)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def restore_workspace_git(workspace: Path, root: Path, commit: str) -> bool:
    """Legacy snapshots lack write provenance; never force-checkout user files.

    Use FileJournal.rollback(change_id) for attributable, version-checked undo.
    State resume can still report workspace_restored=False to its caller.
    """
    return False


def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    for key, value in state.items():
        if key == "runtime":
            continue
        if key == "messages":
            payload[key] = messages_to_dict(value)
        else:
            payload[key] = json_safe(value)
    return payload


def _deserialize_state(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    if isinstance(result.get("messages"), list):
        result["messages"] = messages_from_dict(result["messages"])
    return result


def merge_checkpoint_update(
    state: dict[str, Any], update: dict[str, Any]
) -> dict[str, Any]:
    """Apply a LangGraph node update to the state kept for checkpoints.

    Streamed ``updates`` are deltas rather than complete graph states.  Most
    TacoGraphState fields use replacement semantics; ``messages`` is the one
    reducer-backed field and must use LangGraph's ``add_messages`` reducer.
    """
    merged = dict(state)
    for key, value in update.items():
        if key == "messages":
            merged[key] = add_messages(merged.get(key, []), value or [])
        else:
            merged[key] = value
    return merged


def resume_command(workspace: Path) -> str:
    return f"tacoagent --resume {shlex.quote(str(workspace.resolve()))}"


def build_recovery_markdown(payload: dict[str, Any]) -> str:
    files = payload.get("workspace_manifest", [])
    file_lines = "\n".join(f"- `{item.get('path', '')}`" for item in files) or "- None"
    return (
        "# TacoAgent Recovery\n\n"
        f"- Task: {payload.get('task', '')}\n"
        f"- Status: {payload.get('status', '')}\n"
        f"- Latest node: {payload.get('latest_node') or 'unknown'}\n"
        f"- Git commit: {payload.get('git_commit') or 'unavailable'}\n"
        f"- Saved at: {payload.get('saved_at', '')}\n\n"
        "## Workspace files\n\n"
        f"{file_lines}\n\n"
        "## Resume command\n\n"
        f"```bash\n{payload.get('resume_command', '')}\n```\n"
    )


class CheckpointManager:
    def __init__(self, runtime, task: str = ""):
        self.runtime = runtime
        self.workspace = runtime.workspace
        self.mode = normalize_checkpoint_mode(runtime.checkpoint_mode)
        self.task = task
        self.root = self.workspace / ".tacoagent" / "checkpoints"

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def save(
        self,
        state: dict[str, Any],
        *,
        status: str = "running",
        latest_node: str | None = None,
        event: Any = None,
        snapshot_workspace: bool = True,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        serialized = _serialize_state(state)
        if self.mode == "strict":
            (self.root / "state.json").write_text(
                json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if event is not None:
                with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {"timestamp": _now(), "event": json_safe(event)},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        previous: dict[str, Any] = {}
        checkpoint_path = self.root / "checkpoint.json"
        if not snapshot_workspace and checkpoint_path.is_file():
            try:
                previous = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
        manifest = (
            workspace_manifest(self.workspace)
            if snapshot_workspace
            else previous.get("workspace_manifest", [])
        )
        commit = (
            snapshot_workspace_git(self.workspace, self.root)
            if snapshot_workspace
            else previous.get("git_commit")
        )
        payload = {
            "version": 1,
            "task": state.get("task") or self.task,
            "status": status,
            "latest_node": latest_node,
            "saved_at": _now(),
            "mode": self.mode,
            "git_commit": commit,
            "workspace_manifest": manifest,
            "resume_command": resume_command(self.workspace),
            "resume_state": serialized,
        }
        checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.root / "RECOVERY.md").write_text(
            build_recovery_markdown(payload), encoding="utf-8"
        )
        return {
            "type": "checkpoint_saved",
            "status": status,
            "latest_node": latest_node,
            "path": str(self.root / "checkpoint.json"),
            "git_commit": commit,
            "mode": self.mode,
        }

    @classmethod
    def load_resume_inputs(cls, runtime, task: str | None = None, max_attempts: int = 3):
        manager = cls(runtime, task=task or "")
        checkpoint_path = manager.root / "checkpoint.json"
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        commit = str(payload.get("git_commit") or "")
        restored = restore_workspace_git(manager.workspace, manager.root, commit)
        inputs = _deserialize_state(payload.get("resume_state", {}))
        inputs["runtime"] = runtime
        inputs["task"] = task or inputs.get("task") or payload.get("task", "")
        inputs["max_attempts"] = int(inputs.get("max_attempts", max_attempts))
        inputs["attempts"] = int(inputs.get("attempts", 0))
        event = {
            "type": "checkpoint_resumed",
            "path": str(checkpoint_path),
            "git_commit": commit or None,
            "workspace_restored": restored,
            "latest_node": payload.get("latest_node"),
            "status": payload.get("status"),
        }
        return inputs, event
