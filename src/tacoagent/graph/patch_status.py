from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


_NON_PRODUCTION_PREFIXES = ("tests/", "test/", "docs/", ".github/")
_NON_PRODUCTION_NAMES = {
    "todo.md", "notepad.md", "history_summary.md", "session_summary.md", "recovery.md"
}
_AUXILIARY_STEMS = ("_fix_", "_patch_", "debug_", "tmp_", "temp_")


def _is_auxiliary_file(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith(_AUXILIARY_STEMS) or name.endswith((".log", ".tmp", ".bak"))


def inspect_workspace_patch(workspace: Path) -> dict[str, Any]:
    """Return a cheap, model-facing summary of the current Git patch."""
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if completed.returncode:
        return {
            "has_patch": False,
            "production_files_changed": False,
            "changed_files": [],
            "error": completed.stderr.strip() or "git status failed",
        }
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        path = line[3:].strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and not path.startswith((".mokioclaw/", ".tacoagent/")):
            paths.append(path)
    auxiliary = [path for path in paths if _is_auxiliary_file(path)]
    production = [
        path for path in paths
        if not path.lower().startswith(_NON_PRODUCTION_PREFIXES)
        and path.lower() not in _NON_PRODUCTION_NAMES
        and not path.lower().endswith((".md", ".rst"))
        and not _is_auxiliary_file(path)
    ]
    return {
        "has_patch": bool(paths),
        "production_files_changed": bool(production),
        "changed_files": paths,
        "production_files": production,
        "auxiliary_files": auxiliary,
        "auxiliary_only": bool(paths) and len(auxiliary) == len(paths),
    }
