import json
import tempfile
import unittest
from pathlib import Path

from tacoagent import TacoAgent
from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox
from tacoagent.tools import FileEditTool, NonUniqueTextError


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
