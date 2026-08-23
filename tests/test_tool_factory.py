import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.tools import StructuredTool

from tacoagent.tools import build_structured_tools, create_tools


class StructuredToolFactoryTests(unittest.TestCase):
    def test_returns_all_model_bindable_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tools = build_structured_tools(directory)
            self.assertTrue(all(isinstance(tool, StructuredTool) for tool in tools))
            self.assertEqual(
                {tool.name for tool in tools},
                {
                    "list_workspace_files",
                    "file_read",
                    "file_create",
                    "file_edit",
                    "regex_search",
                    "command_run",
                },
            )

    def test_structured_calls_keep_safety_and_logging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = {tool.name: tool for tool in create_tools(root, task_name="model task")}
            created = tools["file_create"].invoke(
                {"file_path": "note.txt", "content": "hello taco"}
            )
            read = tools["file_read"].invoke({"file_path": "note.txt"})
            self.assertTrue(created["ok"])
            self.assertEqual(read["content"], "hello taco")
            records = [
                json.loads(line)
                for line in (root / ".tacoagent/tool_calls.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(all(record["task_name"] == "model task" for record in records))


if __name__ == "__main__":
    unittest.main()
