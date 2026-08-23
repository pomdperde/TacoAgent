import json
import tempfile
import unittest
from pathlib import Path

from tacoagent import TacoAgent
from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox
from tacoagent.tools import FileCreateTool


class FileCreateToolTests(unittest.TestCase):
    def test_creates_nested_file_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = FileCreateTool(WorkspaceSandbox(root)).invoke(
                file_path="notes/taco.txt", content="hello"
            )
            self.assertEqual(result["path"], "notes/taco.txt")
            self.assertEqual((root / "notes/taco.txt").read_text(encoding="utf-8"), "hello")

    def test_rejects_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = FileCreateTool(WorkspaceSandbox(directory))
            with self.assertRaises(WorkspaceBoundaryError):
                tool.invoke(file_path="../outside.txt", content="unsafe")
            with self.assertRaises(WorkspaceBoundaryError):
                tool.invoke(
                    file_path=Path(directory).resolve().parent / "outside.txt",
                    content="unsafe",
                )

    def test_does_not_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "existing.txt"
            path.write_text("original", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                FileCreateTool(WorkspaceSandbox(root)).invoke(
                    file_path="existing.txt", content="replacement"
                )
            self.assertEqual(path.read_text(encoding="utf-8"), "original")

    def test_agent_create_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            response = TacoAgent(root).run("/create hello.txt taco content")
            self.assertTrue((root / "hello.txt").is_file())
            self.assertIn("Created hello.txt", response.content)
            records = [
                json.loads(line)
                for line in (root / ".tacoagent/tool_calls.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(records[0]["tool_name"], "file_create")
            self.assertEqual(records[-1]["event"], "tool_succeeded")


if __name__ == "__main__":
    unittest.main()
