from __future__ import annotations

from dataclasses import dataclass

from tacoagent.core.workspace import WorkspaceSandbox


@dataclass(slots=True)
class FileReadTool:
    """Read a UTF-8 text file after enforcing the workspace boundary."""

    workspace: WorkspaceSandbox
    name: str = "file_read"
    description: str = "Read a UTF-8 text file inside the TacoAgent workspace."

    def invoke(self, **kwargs) -> dict[str, object]:
        file_path = str(kwargs.get("file_path", "")).strip()
        if not file_path:
            raise ValueError("file_path must not be empty")
        try:
            max_chars = int(kwargs.get("max_chars", 100_000))
        except (TypeError, ValueError) as exc:
            raise ValueError("max_chars must be an integer") from exc
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than zero")

        # resolve() rejects absolute paths, traversal, and symlinks whose final
        # target is outside the configured workspace.
        path = self.workspace.resolve(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {file_path}")

        content = path.read_text(encoding="utf-8")
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        return {
            "ok": True,
            "path": self.workspace.relative(path),
            "content": content,
            "truncated": truncated,
            "characters": len(content),
        }
