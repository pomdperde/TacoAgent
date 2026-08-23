import json
import os
import tempfile
import unittest
from pathlib import Path

from tacoagent import TacoAgent
from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox
from tacoagent.tools import FileReadTool, build_tool_registry


class FileReadToolTests(unittest.TestCase):
    def test_reads_relative_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hello.txt").write_text("hello taco", encoding="utf-8")
            result = FileReadTool(WorkspaceSandbox(root)).invoke(file_path="hello.txt")
            self.assertEqual(result["content"], "hello taco")
            self.assertEqual(result["path"], "hello.txt")

    def test_rejects_absolute_and_traversal_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool = FileReadTool(WorkspaceSandbox(directory))
            with self.assertRaises(WorkspaceBoundaryError):
                tool.invoke(file_path="../secret.txt")
            with self.assertRaises(WorkspaceBoundaryError):
                tool.invoke(file_path=Path(directory).resolve() / "file.txt")

    def test_rejects_symlink_that_leaves_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            secret = Path(outside, "secret.txt")
            secret.write_text("secret", encoding="utf-8")
            link = root / "link.txt"
            try:
                os.symlink(secret, link)
            except (OSError, NotImplementedError):
                self.skipTest("Symbolic links are unavailable")
            with self.assertRaises(WorkspaceBoundaryError):
                FileReadTool(WorkspaceSandbox(root)).invoke(file_path="link.txt")

    def test_agent_read_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("safe content", encoding="utf-8")
            response = TacoAgent(root).run("/read note.txt")
            self.assertEqual(response.content, "safe content")
            log_path = root / ".tacoagent" / "tool_calls.jsonl"
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["tool_name"], "file_read")
            self.assertEqual(records[-1]["event"], "tool_succeeded")


if __name__ == "__main__":
    unittest.main()
