from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.core.file_journal import FileJournal, version


class NonUniqueTextError(ValueError):
    """Raised when an edit cannot be located safely, with retry context."""

    def __init__(self, message: str, *, recovery: dict[str, Any] | None = None):
        super().__init__(message)
        self.recovery = recovery or {}


def _excerpt(content: str, old_text: str = "", line_hint: int = 1) -> dict[str, Any]:
    lines = content.splitlines(keepends=True)
    needle = next((line.strip() for line in old_text.splitlines() if line.strip()), "")
    candidates = [i + 1 for i, line in enumerate(lines) if needle and needle in line][:8]
    center = candidates[0] if candidates else max(1, min(line_hint, len(lines) or 1))
    first, last = max(1, center - 4), min(len(lines), center + 4)
    return {
        "retry_modes": ["line", "anchor", "patch"],
        "candidate_lines": candidates,
        "excerpt": {
            "start_line": first,
            "end_line": last,
            "content": "".join(f"{n}: {lines[n - 1]}" for n in range(first, last + 1)),
        },
        "instruction": "Retry file_edit using current line numbers, stable anchors, or a unified hunk.",
    }


def _apply_hunks(content: str, patch: str, file_path: str) -> str:
    source = content.splitlines(keepends=True)
    output: list[str] = []
    cursor = index = applied = 0
    patch_lines = patch.splitlines(keepends=True)
    while index < len(patch_lines):
        header = re.match(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", patch_lines[index])
        if not header:
            index += 1
            continue
        old_start = int(header.group(1))
        target = old_start - 1
        if target < cursor or target > len(source):
            raise NonUniqueTextError(
                f"Patch has an invalid old line in {file_path}: {old_start}",
                recovery=_excerpt(content, line_hint=old_start),
            )
        output.extend(source[cursor:target])
        cursor = target
        index += 1
        while index < len(patch_lines) and not patch_lines[index].startswith("@@ "):
            raw = patch_lines[index]
            if raw.startswith(("--- ", "+++ ", "\\ No newline")):
                index += 1
                continue
            marker, text = raw[:1], raw[1:]
            if marker in {" ", "-"}:
                actual = source[cursor] if cursor < len(source) else None
                if actual != text:
                    raise NonUniqueTextError(
                        f"Patch context does not match {file_path} at line {cursor + 1}",
                        recovery=_excerpt(content, text, cursor + 1),
                    )
                cursor += 1
            if marker in {" ", "+"}:
                output.append(text)
            if marker not in {" ", "+", "-"}:
                raise ValueError("patch must contain unified diff hunk lines")
            index += 1
        applied += 1
    if not applied:
        raise ValueError("patch must contain at least one @@ unified diff hunk")
    output.extend(source[cursor:])
    return "".join(output)


@dataclass(slots=True)
class FileEditTool:
    """Safely edit one existing workspace file using text, lines, anchors, or hunks."""

    workspace: WorkspaceSandbox
    name: str = "file_edit"
    description: str = "Edit a workspace file by exact text, line range, anchors, or unified patch hunks."

    def invoke(self, **kwargs) -> dict[str, object]:
        file_path = str(kwargs.get("file_path", "")).strip()
        mode = str(kwargs.get("mode", "exact") or "exact").strip().lower()
        old_text, new_text = str(kwargs.get("old_text", "")), str(kwargs.get("new_text", ""))
        if not file_path:
            raise ValueError("file_path must not be empty")
        if mode not in {"exact", "line", "anchor", "patch"}:
            raise ValueError("mode must be exact, line, anchor, or patch")
        path = self.workspace.resolve(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File does not exist: {file_path}")
        if not path.is_file():
            raise IsADirectoryError(f"Path is not a file: {file_path}")
        journal = FileJournal(self.workspace)
        raw_bytes, read_version = version(journal.path(file_path))
        raw_content = raw_bytes.decode("utf-8")
        newline = "\r\n" if "\r\n" in raw_content else "\n"
        content = raw_content.replace("\r\n", "\n")
        old_text = old_text.replace("\r\n", "\n")
        new_text = new_text.replace("\r\n", "\n")

        if mode == "exact":
            if not old_text:
                raise ValueError("old_text must not be empty in exact mode")
            count = content.count(old_text)
            if count != 1:
                detail = "not found" if count == 0 else f"found {count} times"
                raise NonUniqueTextError(
                    f"Edit target must occur exactly once in {file_path}; {detail}",
                    recovery=_excerpt(content, old_text),
                )
            updated = content.replace(old_text, new_text, 1)
        elif mode == "line":
            lines = content.splitlines(keepends=True)
            start = int(kwargs.get("start_line", 0) or 0)
            end = int(kwargs.get("end_line", start) or start)
            if start < 1 or end < start or end > len(lines):
                raise NonUniqueTextError(
                    f"Invalid line range for {file_path}: {start}-{end} (file has {len(lines)} lines)",
                    recovery=_excerpt(content, line_hint=start or 1),
                )
            selected = "".join(lines[start - 1:end])
            expected = str(kwargs.get("expected_text", "")).replace("\r\n", "\n")
            if expected and selected != expected:
                raise NonUniqueTextError(
                    f"Line range changed in {file_path}: {start}-{end}",
                    recovery=_excerpt(content, expected, start),
                )
            updated = "".join([*lines[: start - 1], new_text, *lines[end:]])
        elif mode == "anchor":
            before = str(kwargs.get("anchor_before", "")).replace("\r\n", "\n")
            after = str(kwargs.get("anchor_after", "")).replace("\r\n", "\n")
            if not before or not after:
                raise ValueError("anchor_before and anchor_after are required in anchor mode")
            ranges = [(m.end(), content.find(after, m.end())) for m in re.finditer(re.escape(before), content)]
            ranges = [(start, end) for start, end in ranges if end >= 0]
            if len(ranges) != 1:
                raise NonUniqueTextError(
                    f"Anchors must identify exactly one range in {file_path}; found {len(ranges)}",
                    recovery=_excerpt(content, before),
                )
            start, end = ranges[0]
            updated = content[:start] + new_text + content[end:]
        else:
            patch = str(kwargs.get("patch", "")).replace("\r\n", "\n")
            updated = _apply_hunks(content, patch, file_path)

        path = self.workspace.resolve(file_path)
        serialized = updated.replace("\n", "\r\n") if newline == "\r\n" else updated
        change_id = journal.change(file_path, raw_bytes, serialized.encode("utf-8"),
                                   expected_version=read_version)
        return {"ok": True, "change_id": change_id, "path": self.workspace.relative(path), "mode": mode,
                "replacements": 1, "characters_before": len(raw_content), "characters_after": len(serialized)}
