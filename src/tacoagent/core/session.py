"""Persistent, workspace-scoped conversation sessions for TacoAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SESSION_ROOT = ".tacoagent/session"
SESSION_FILE = "session.json"
SESSION_SUMMARY_FILE = "SESSION_SUMMARY.md"
MAX_SESSION_CONTEXT = 7000
MAX_TURN_CONTENT = 4000
MAX_STORED_TURNS = 200
MAX_ASSISTANT_SUMMARY = 800


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_text(value: object, limit: int = MAX_TURN_CONTENT) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _session_root(workspace: str | Path) -> Path:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / SESSION_ROOT


def _new_session() -> dict[str, Any]:
    timestamp = _now()
    return {
        "session_id": str(uuid4()),
        "turn_index": 0,
        "recent_turns": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _normalize_session(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or not str(payload.get("session_id", "")).strip():
        return _new_session()
    session = dict(payload)
    session["session_id"] = str(session["session_id"])
    try:
        session["turn_index"] = max(0, int(session.get("turn_index", 0)))
    except (TypeError, ValueError):
        session["turn_index"] = 0
    turns = session.get("recent_turns", [])
    session["recent_turns"] = [dict(item) for item in turns if isinstance(item, dict)]
    session["created_at"] = str(session.get("created_at") or _now())
    session["updated_at"] = str(session.get("updated_at") or session["created_at"])
    return session


def load_or_create_session(workspace: Path) -> dict[str, Any]:
    """Load the workspace session, or create and persist a fresh one."""
    root = _session_root(workspace)
    path = root / SESSION_FILE
    if path.is_file():
        try:
            return _normalize_session(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    session = _new_session()
    save_session(workspace, session)
    return session


def append_user_turn(session: dict[str, Any], content: str) -> int:
    """Record user input and return its monotonically increasing turn number."""
    turn = max(0, int(session.get("turn_index", 0))) + 1
    session["turn_index"] = turn
    session.setdefault("recent_turns", []).append(
        {
            "turn": turn,
            "role": "user",
            "content": _short_text(content),
            "timestamp": _now(),
        }
    )
    session["updated_at"] = _now()
    return turn


def append_assistant_turn(
    session: dict[str, Any],
    *,
    turn: int,
    route: str,
    content: str,
    summary: str = "",
    status: str = "completed",
) -> None:
    """Record an assistant response associated with an existing user turn."""
    normalized_route = route if route in {"chat", "workflow"} else "workflow"
    session.setdefault("recent_turns", []).append(
        {
            "turn": max(0, int(turn)),
            "role": "assistant",
            "route": normalized_route,
            "content": _short_text(content),
            "summary": _short_text(summary),
            "status": status,
            "timestamp": _now(),
        }
    )
    session["turn_index"] = max(
        max(0, int(session.get("turn_index", 0))), max(0, int(turn))
    )
    session["updated_at"] = _now()


def _summary_markdown(session: dict[str, Any]) -> str:
    lines = [
        "# TacoAgent Session Summary",
        "",
        f"- Session ID: `{session['session_id']}`",
        f"- Turn index: {session['turn_index']}",
        f"- Updated at: {session['updated_at']}",
        "",
        "## Recent conversation",
        "",
    ]
    for item in session.get("recent_turns", [])[-10:]:
        role = str(item.get("role", "unknown"))
        route = f"/{item.get('route')}" if item.get("route") else ""
        content = item.get("summary") or item.get("content") or ""
        lines.append(
            f"- Turn {item.get('turn', '?')} · {role}{route}: "
            f"{_short_text(content, 800).replace(chr(10), ' ')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def save_session(workspace: str | Path, session: dict[str, Any]) -> dict[str, Any]:
    """Persist session JSON and its human-readable Markdown summary."""
    root = _session_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_session(session)
    normalized["recent_turns"] = normalized["recent_turns"][-MAX_STORED_TURNS:]
    normalized["updated_at"] = _now()
    # Keep the in-memory object synchronized while preserving its identity.
    session.clear()
    session.update(normalized)
    _atomic_write(root / SESSION_FILE, json.dumps(session, ensure_ascii=False, indent=2))
    _atomic_write(root / SESSION_SUMMARY_FILE, _summary_markdown(session))
    return session


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _recent_workspace_files(workspace: Path) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if relative.parts[:1] in {(".git",), (".tacoagent",)}:
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((modified, relative.as_posix()))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in candidates[:30]]


def build_session_context(
    workspace: str | Path,
    session: dict[str, Any] | None = None,
    *,
    before_turn: int | None = None,
) -> str:
    """Build a bounded context containing identity, files and recent dialogue."""
    workspace_path = Path(workspace).expanduser().resolve()
    current = _normalize_session(session) if session is not None else load_or_create_session(workspace_path)
    files = _recent_workspace_files(workspace_path)
    header = (
        f"Session ID: {current['session_id']}\n"
        f"Turn index: {current['turn_index']}\n\n"
        "Recent workspace files (up to 30):\n"
        + ("\n".join(f"- {name}" for name in files) if files else "- (none)")
        + "\n\nRecent conversation (up to 10 entries):"
    )
    remaining = max(0, MAX_SESSION_CONTEXT - len(header) - 1)
    selected: list[str] = []
    turns = current.get("recent_turns", [])
    if before_turn is not None:
        turns = [item for item in turns if int(item.get("turn", 0)) < before_turn]
    for item in reversed(turns[-10:]):
        content = item.get("summary") or item.get("content") or ""
        route = f"/{item.get('route')}" if item.get("route") else ""
        line = (
            f"Turn {item.get('turn', '?')} {item.get('role', 'unknown')}{route}: "
            f"{_short_text(content).replace(chr(10), ' ')}"
        )
        cost = len(line) + 1
        if cost > remaining:
            if not selected and remaining > 3:
                selected.append(_short_text(line, remaining))
            break
        selected.append(line)
        remaining -= cost
    selected.reverse()
    context = header + "\n" + ("\n".join(selected) if selected else "(none)")
    return _short_text(context, MAX_SESSION_CONTEXT)


@dataclass(frozen=True, slots=True)
class SessionTurn:
    number: int
    session_id: str
    context: str


class SessionManager:
    """Own one workspace session and provide atomic turn lifecycle operations."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.session = load_or_create_session(self.workspace)

    @property
    def session_id(self) -> str:
        return str(self.session["session_id"])

    def context(self, *, before_turn: int | None = None) -> str:
        return build_session_context(
            self.workspace, self.session, before_turn=before_turn
        )

    def start_turn(self, content: str) -> SessionTurn:
        number = append_user_turn(self.session, content)
        # Exclude the latest input: entry_nodes already places it in the prompt.
        context = self.context(before_turn=number)
        save_session(self.workspace, self.session)
        return SessionTurn(number=number, session_id=self.session_id, context=context)

    def complete_turn(
        self,
        turn: SessionTurn | int,
        *,
        route: str,
        content: str,
        summary: str = "",
        status: str = "completed",
    ) -> None:
        number = turn.number if isinstance(turn, SessionTurn) else int(turn)
        compact_summary = summary or _short_text(content, MAX_ASSISTANT_SUMMARY)
        append_assistant_turn(
            self.session,
            turn=number,
            route=route,
            content=content,
            summary=compact_summary,
            status=status,
        )
        save_session(self.workspace, self.session)
