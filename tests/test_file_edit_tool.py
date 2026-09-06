import json
import tempfile
import unittest
from pathlib import Path

from tacoagent import TacoAgent
from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox
from tacoagent.tools import FileEditTool, NonUniqueTextError
from tacoagent.tools.factory import build_structured_tools


class FileEditToolTests(unittest.TestCase):
    def test_replaces_exactly_one_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "app.txt")
            path.write_text("hello old world", encoding="utf-8")
            result = FileEditTool(WorkspaceSandbox(directory)).invoke(
                file_path="app.txt", old_text="old", new_text="new"
            )
            self.assertEqual(result["replacements"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), "hello new world")

    def test_missing_fragment_returns_error_without_modifying_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "app.txt")
            path.write_text("unchanged", encoding="utf-8")
            with self.assertRaises(NonUniqueTextError):
                FileEditTool(WorkspaceSandbox(directory)).invoke(
                    file_path="app.txt", old_text="missing", new_text="new"
                )
            self.assertEqual(path.read_text(encoding="utf-8"), "unchanged")

    def test_duplicate_fragment_returns_error_without_modifying_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "app.txt")
            path.write_text("same and same", encoding="utf-8")
            with self.assertRaisesRegex(NonUniqueTextError, "found 2 times"):
                FileEditTool(WorkspaceSandbox(directory)).invoke(
                    file_path="app.txt", old_text="same", new_text="different"
                )
            self.assertEqual(path.read_text(encoding="utf-8"), "same and same")

    def test_line_mode_replaces_range_with_stale_write_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "app.txt")
            path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            result = FileEditTool(WorkspaceSandbox(directory)).invoke(
                file_path="app.txt", mode="line", start_line=2, end_line=2,
                expected_text="two\n", new_text="changed\n",
            )
            self.assertEqual(result["mode"], "line")
            self.assertEqual(path.read_text(encoding="utf-8"), "one\nchanged\nthree\n")

    def test_anchor_mode_replaces_only_content_between_unique_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "app.txt")
            path.write_text("before\nold\nafter\n", encoding="utf-8")
            FileEditTool(WorkspaceSandbox(directory)).invoke(
                file_path="app.txt", mode="anchor", anchor_before="before\n",
                anchor_after="after\n", new_text="new\n",
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "before\nnew\nafter\n")

    def test_patch_mode_applies_verified_unified_hunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "app.txt")
            path.write_text("one\ntwo\nthree\n", encoding="utf-8")
            FileEditTool(WorkspaceSandbox(directory)).invoke(
                file_path="app.txt", mode="patch",
                patch="@@ -1,3 +1,3 @@\n one\n-two\n+changed\n three\n",
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "one\nchanged\nthree\n")

    def test_failed_structured_edit_returns_numbered_recovery_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            edit = next(tool for tool in build_structured_tools(directory) if tool.name == "file_edit")
            with self.assertRaises(NonUniqueTextError) as raised:
                edit.invoke({"file_path": "app.txt", "old_text": "missing", "new_text": "new"})
            recovery = raised.exception.recovery
            self.assertIn("1: alpha", recovery["excerpt"]["content"])
            self.assertIn("line", recovery["retry_modes"])

    def test_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(WorkspaceBoundaryError):
                FileEditTool(WorkspaceSandbox(directory)).invoke(
                    file_path="../outside.txt", old_text="old", new_text="new"
                )

    def test_agent_edit_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "note.txt"
            path.write_text("hello old", encoding="utf-8")
            response = TacoAgent(root).run("/edit note.txt old => new")
            self.assertIn("Edited note.txt", response.content)
            self.assertEqual(path.read_text(encoding="utf-8"), "hello new")
            records = [
                json.loads(line)
                for line in (root / ".tacoagent/tool_calls.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(records[0]["tool_name"], "file_edit")
            self.assertEqual(records[-1]["event"], "tool_succeeded")


if __name__ == "__main__":
    unittest.main()
