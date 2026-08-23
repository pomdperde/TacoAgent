from __future__ import annotations

from dataclasses import dataclass

from tacoagent.core.workspace import WorkspaceSandbox


@dataclass(slots=True)
class FileCreateTool:
    """Create a new UTF-8 file without allowing workspace escapes or overwrite."""

    workspace: WorkspaceSandbox
    name: str = "file_create"
    description: str = "Create a new text file inside the TacoAgent workspace."

    def invoke(self, **kwargs) -> dict[str, object]:
        file_path = str(kwargs.get("file_path", "")).strip()
        if not file_path:
            raise ValueError("file_path must not be empty")
        content = str(kwargs.get("content", ""))

        # This rejects absolute paths, traversal, and existing symlink chains
        # whose resolved destination is outside the workspace.
        path = self.workspace.resolve(file_path)
        if path.exists():
            raise FileExistsError(f"File already exists: {file_path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve once more after creating parents, so a symlinked parent cannot
        # redirect the actual write outside the workspace.
        path = self.workspace.resolve(file_path)
        if path.exists():
            raise FileExistsError(f"File already exists: {file_path}")
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
        return {
            "ok": True,
            "path": self.workspace.relative(path),
            "characters": len(content),
            "created": True,
        }
