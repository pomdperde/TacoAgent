from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceBoundaryError(ValueError):
    """Raised when an operation attempts to leave the configured workspace."""


@dataclass(frozen=True, slots=True)
class WorkspaceSandbox:
    root: Path

    def __init__(self, root: str | Path) -> None:
        resolved = Path(root).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        if not resolved.is_dir():
            raise ValueError(f"Workspace must be a directory: {resolved}")
        object.__setattr__(self, "root", resolved)

    def resolve(self, path: str | Path = ".") -> Path:
        """Resolve a relative path and reject anything outside the sandbox."""
        requested = Path(path)
        if requested.is_absolute():
            raise WorkspaceBoundaryError("Workspace paths must be relative")
        resolved = (self.root / requested).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceBoundaryError(
                f"Path leaves the TacoAgent workspace: {path}"
            ) from exc
        return resolved

    def relative(self, path: str | Path) -> str:
        resolved = Path(path).expanduser().resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkspaceBoundaryError(
                f"Path is outside the TacoAgent workspace: {path}"
            ) from exc

    def files(self, *, limit: int = 50) -> list[str]:
        maximum = max(1, min(int(limit), 500))
        files = []
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if path.is_file() and relative.parts[:1] != (".tacoagent",):
                files.append(relative.as_posix())
        return sorted(files)[:maximum]
