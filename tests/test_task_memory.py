import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tacoagent.core.runtime import RuntimeState
from tacoagent.memory import (
    LATEST_TASK_SUMMARY_FILE,
    TASK_MEMORY_FILE,
    TASK_MEMORY_ROOT,
    build_layered_memory,
    generate_task_summary,
    get_external_memory,
    load_external_memory_index,
    load_task_summaries,
    save_task_summary,
)


class TaskMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem0_patch = patch(
            "tacoagent.memory.memory.add_mem0_task_memory",
            return_value={"ok": False, "stored": False, "error": "disabled in unit test"},
        )
        self.mem0_patch.start()
        self.mem0_search_patch = patch(
            "tacoagent.memory.memory.search_mem0_task_memories", return_value=[]
        )
        self.mem0_search_patch.start()

    def tearDown(self) -> None:
        self.mem0_search_patch.stop()
        self.mem0_patch.stop()

    def test_generates_and_persists_structured_task_summary(self) -> None:
        events = [
            {
                "type": "plan_snapshot",
                "plan_summary": "Create and test app",
                "todos": [{"id": "1", "content": "Create app.py", "status": "completed"}],
            },
            {"type": "tool_call", "name": "file_create", "args": {"file_path": "app.py"}},
            {"type": "tool_call", "name": "command_run", "args": {"command": "pytest"}},
            {"type": "verifier_result", "passed": True, "checks": [{"name": "tests", "passed": True}]},
        ]
        summary = generate_task_summary(
            "build app", events, final_answer="App created and tests passed."
        )
        with tempfile.TemporaryDirectory() as directory:
            save_task_summary(directory, summary)
            loaded = load_task_summaries(directory)
            root = Path(directory) / TASK_MEMORY_ROOT

            self.assertEqual(loaded[-1]["memory_id"], summary["memory_id"])
            self.assertEqual(loaded[-1]["important_files"], ["app.py"])
            self.assertEqual(loaded[-1]["tools_used"], ["file_create", "command_run"])
            self.assertTrue((root / TASK_MEMORY_FILE).is_file())
            self.assertTrue((root / "external_memory.sqlite3").is_file())
            self.assertIn("App created", (root / LATEST_TASK_SUMMARY_FILE).read_text(encoding="utf-8"))
            detail = get_external_memory(directory, summary["memory_id"])
            self.assertTrue(detail["ok"])
            self.assertEqual(detail["detail"]["important_files"], ["app.py"])

    def test_external_memory_index_is_lightweight_and_id_addressable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = generate_task_summary("large task", [], final_answer="detailed result")
            summary["research_blob"] = "x" * 20_000
            save_task_summary(directory, summary)
            index = load_external_memory_index(directory)
            detail = get_external_memory(directory, index[0]["memory_id"])

        self.assertNotIn("research_blob", index[0])
        self.assertEqual(index[0]["detail_tool"], "memory_get")
        self.assertEqual(detail["detail"]["research_blob"], "x" * 20_000)

    def test_unknown_external_memory_id_returns_value_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = get_external_memory(directory, "memory-missing")
        self.assertFalse(result["ok"])
        self.assertIn("Unknown memory_id", result["error"])

    def test_layered_memory_reads_external_task_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = generate_task_summary("prior task", [], final_answer="prior result")
            save_task_summary(directory, summary)
            memory = build_layered_memory(
                {"task": "next task", "runtime": RuntimeState(directory)}, node="planner"
            )

        records = memory["history_summary_store"]["recent_task_memories"]
        self.assertEqual(records[-1]["task"], "prior task")
        self.assertEqual(records[-1]["summary_hint"], "prior result")
        self.assertEqual(records[-1]["detail_tool"], "memory_get")

    def test_loader_skips_corrupt_external_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / TASK_MEMORY_ROOT
            root.mkdir(parents=True)
            (root / TASK_MEMORY_FILE).write_text(
                "not-json\n" + json.dumps({"memory_id": "ok"}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(load_task_summaries(directory), [{"memory_id": "ok"}])


if __name__ == "__main__":
    unittest.main()
