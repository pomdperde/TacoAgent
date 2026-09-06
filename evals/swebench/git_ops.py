from __future__ import annotations

import subprocess
import os
from pathlib import Path

from .models import SWEInstance


INTERNAL_PATHS = {
    "TODO.md",
    "NOTEPAD.md",
    "HISTORY_SUMMARY.md",
    "SESSION_SUMMARY.md",
    "RECOVERY.md",
}
INTERNAL_PREFIXES = (".tacoagent/", ".mokioclaw/")
TEST_DIRECTORY_NAMES = {"test", "tests", "testing"}


def _git(*args: str, cwd: Path | None = None, timeout: int = 600) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def prepare_repository(instance: SWEInstance, workspace: Path) -> None:
    """Fetch only the benchmark base commit and detach the workspace at it."""
    if workspace.exists():
        raise FileExistsError(f"Instance workspace already exists: {workspace}")
    cache_roots = [item for item in os.getenv("TACO_SWE_WORKSPACE_CACHE_ROOT", "").split(os.pathsep) if item]
    cached = next(
        (
            Path(root).expanduser() / instance.instance_id
            for root in cache_roots
            if (Path(root).expanduser() / instance.instance_id / ".git").is_dir()
            and subprocess.run(
                ["git", "cat-file", "-e", f"{instance.base_commit}^{{tree}}"],
                cwd=Path(root).expanduser() / instance.instance_id,
                capture_output=True,
                timeout=30,
            ).returncode == 0
        ),
        None,
    )
    if cached is not None:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        _git(
            "clone",
            "--no-local",
            "--no-checkout",
            str(cached.resolve()),
            str(workspace.resolve()),
            timeout=600,
        )
        _git("checkout", "--detach", instance.base_commit, cwd=workspace, timeout=600)
        _git(
            "remote",
            "set-url",
            "origin",
            f"https://github.com/{instance.repo}.git",
            cwd=workspace,
        )
        return
    workspace.mkdir(parents=True)
    _git("init", "-q", cwd=workspace)
    _git(
        "remote",
        "add",
        "origin",
        f"https://github.com/{instance.repo}.git",
        cwd=workspace,
    )
    _git(
        "fetch",
        "--depth=1",
        "--filter=blob:none",
        "origin",
        instance.base_commit,
        cwd=workspace,
        timeout=1800,
    )
    _git("checkout", "--detach", "FETCH_HEAD", cwd=workspace, timeout=600)


def _is_internal(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized in INTERNAL_PATHS or normalized.startswith(INTERNAL_PREFIXES)


def is_test_artifact(path: str) -> bool:
    """Return whether a path is likely to collide with SWE-bench's test patch."""
    normalized = path.replace("\\", "/").lstrip("./")
    parts = normalized.split("/")
    name = parts[-1].lower()
    return (
        any(part.lower() in TEST_DIRECTORY_NAMES for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name in {"conftest.py", "pytest.ini", "tox.ini"}
        or normalized.startswith(("test_", "tests_"))
    )


def changed_test_artifacts(workspace: Path) -> list[str]:
    """List changed test-side files that must not enter an official prediction."""
    changed = _git("diff", "--name-only", "HEAD", cwd=workspace).splitlines()
    return sorted(path for path in changed if is_test_artifact(path))


def export_model_patch(workspace: Path, *, production_only: bool = False) -> str:
    """Export changes while excluding runtime state and, optionally, tests."""
    raw = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=workspace,
        capture_output=True,
        check=True,
    ).stdout
    untracked = [
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item and not _is_internal(item.decode("utf-8", errors="surrogateescape"))
    ]
    if untracked:
        _git("add", "-N", "--", *untracked, cwd=workspace)
    exclusions = [f":(exclude){path}" for path in sorted(INTERNAL_PATHS)]
    if production_only:
        exclusions.extend(
            f":(exclude){path}" for path in changed_test_artifacts(workspace)
        )
    return _git(
        "diff",
        "--binary",
        "--full-index",
        "HEAD",
        "--",
        ".",
        ":(exclude).tacoagent/**",
        ":(exclude).mokioclaw/**",
        *exclusions,
        cwd=workspace,
    )
