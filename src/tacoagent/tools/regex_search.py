from __future__ import annotations

import re
from dataclasses import dataclass

from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox


@dataclass(slots=True)
class RegexSearchTool:
    """Search UTF-8 workspace files using a Python regular expression."""

    workspace: WorkspaceSandbox
    name: str = "regex_search"
    description: str = "Run a regular-expression search over workspace text files."

    def invoke(self, **kwargs) -> dict[str, object]:
        pattern = str(kwargs.get("pattern", ""))
        search_path = str(kwargs.get("path", ".")).strip() or "."
        case_sensitive = bool(kwargs.get("case_sensitive", True))
        try:
            max_results = max(1, min(int(kwargs.get("max_results", 200)), 2000))
        except (TypeError, ValueError) as exc:
            raise ValueError("max_results must be an integer") from exc
        if not pattern:
            raise ValueError("pattern must not be empty")
        if len(pattern) > 2000:
            raise ValueError("pattern must not exceed 2000 characters")
        try:
            expression = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc

        root = self.workspace.resolve(search_path)
        if not root.exists():
            raise FileNotFoundError(f"Search path does not exist: {search_path}")
        candidates = [root] if root.is_file() else root.rglob("*")
        matches: list[dict[str, object]] = []
        searched_files = 0
        skipped_files = 0

        for candidate in candidates:
            if len(matches) >= max_results:
                break
            if not candidate.is_file():
                continue
            try:
                relative = candidate.relative_to(self.workspace.root)
            except ValueError:
                skipped_files += 1
                continue
            if relative.parts[:1] == (".tacoagent",):
                continue
            try:
                safe_candidate = self.workspace.resolve(relative)
            except WorkspaceBoundaryError:
                skipped_files += 1
                continue
            try:
                with safe_candidate.open("r", encoding="utf-8", newline="") as stream:
                    content = stream.read()
            except (OSError, UnicodeError):
                skipped_files += 1
                continue
            searched_files += 1
            for line_number, line in enumerate(content.splitlines(), start=1):
                for match in expression.finditer(line):
                    matches.append(
                        {
                            "path": relative.as_posix(),
                            "line": line_number,
                            "column": match.start() + 1,
                            "match": match.group(0),
                            "text": line,
                        }
                    )
                    if len(matches) >= max_results:
                        break
                if len(matches) >= max_results:
                    break

        return {
            "ok": True,
            "pattern": pattern,
            "path": search_path,
            "matches": matches,
            "match_count": len(matches),
            "searched_files": searched_files,
            "skipped_files": skipped_files,
            "truncated": len(matches) >= max_results,
        }
