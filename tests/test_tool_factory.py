import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.tools import StructuredTool

from tacoagent.memory import generate_task_summary, save_task_summary
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
                    "file_delete",
                    "file_edit",
                    "regex_search",
                    "command_run",
                    "memory_get",
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

    def test_memory_get_fetches_detail_by_index_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = generate_task_summary("remember", [], final_answer="exact detail")
            with patch(
                "tacoagent.memory.memory.add_mem0_task_memory",
                return_value={"ok": False, "stored": False, "error": "disabled"},
            ):
                save_task_summary(directory, summary)
            tools = {tool.name: tool for tool in build_structured_tools(directory)}
            result = tools["memory_get"].invoke({"memory_id": summary["memory_id"]})

        self.assertTrue(result["ok"])
        self.assertEqual(result["detail"]["summary"], "exact detail")


if __name__ == "__main__":
    unittest.main()
