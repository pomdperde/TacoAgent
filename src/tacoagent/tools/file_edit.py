from __future__ import annotations

from dataclasses import dataclass

from tacoagent.core.workspace import WorkspaceSandbox


class NonUniqueTextError(ValueError):
    """Raised when the requested edit target does not occur exactly once."""


@dataclass(slots=True)
class FileEditTool:
    """Replace one unique text fragment in an existing workspace file."""

    workspace: WorkspaceSandbox
    name: str = "file_edit"
    description: str = "Replace a text fragment that occurs exactly once in a workspace file."

    def invoke(self, **kwargs) -> dict[str, object]:
        file_path = str(kwargs.get("file_path", "")).strip()
        old_text = str(kwargs.get("old_text", ""))
        new_text = str(kwargs.get("new_text", ""))
        if not file_path:
            raise ValueError("file_path must not be empty")
        if not old_text:
            raise ValueError("old_text must not be empty")

        path = self.workspace.resolve(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {file_path}")

        with path.open("r", encoding="utf-8", newline="") as stream:
            content = stream.read()
        occurrences = content.count(old_text)
        if occurrences != 1:
            detail = "not found" if occurrences == 0 else f"found {occurrences} times"
            raise NonUniqueTextError(
                f"Edit target must occur exactly once in {file_path}; {detail}"
            )

        updated = content.replace(old_text, new_text, 1)
        # Recheck immediately before writing in case the path traverses a
        # symlink that changed between reading and modification.
        path = self.workspace.resolve(file_path)
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
        return {
            "ok": True,
            "path": self.workspace.relative(path),
            "replacements": 1,
            "characters_before": len(content),
            "characters_after": len(updated),
        }
