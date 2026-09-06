from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tacoagent.core.runtime import RuntimeState
from tacoagent.memory import (
    RULES_LAYER,
    _short_text,
    _trim_handoffs,
    build_layered_memory,
    format_layered_memory_for_prompt,
)


class LayeredMemoryTests(unittest.TestCase):
    def test_short_text_is_bounded_and_marks_truncation(self) -> None:
        self.assertEqual(_short_text("short", 10), "short")
        self.assertEqual(_short_text("abcdefghij", 7), "abcd...")
        self.assertEqual(len(_short_text("abcdefghij", 7)), 7)

    def test_trim_handoffs_keeps_latest_six(self) -> None:
        handoffs = [{"instruction": str(index)} for index in range(8)]
        result = _trim_handoffs(handoffs)
        self.assertEqual([item["instruction"] for item in result], ["2", "3", "4", "5", "6", "7"])

    def test_builds_three_layers_from_state_and_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "NOTEPAD.md").write_text("durable notes", encoding="utf-8")
            (root / "HISTORY_SUMMARY.md").write_text("compressed history", encoding="utf-8")
            state = {
                "runtime": RuntimeState(root),
                "task": "build memory",
                "session_id": "session-1",
                "session_turn": 4,
                "research_notes": "r" * 2000,
                "sources": [
                    {
                        "title": "Docs",
                        "url": "https://example.com",
                        "content": "must be removed",
                    }
                ],
                "agent_handoffs": [
                    {"instruction": str(index)} for index in range(8)
                ],
                "compression_events": [
                    {"summary": str(index)} for index in range(5)
                ],
            }
            memory = build_layered_memory(state, node="single_agent_planner")

        self.assertEqual(memory["rules"]["rules"], RULES_LAYER["rules"])
        working = memory["working_memory"]
        self.assertEqual(working["node"], "single_agent_planner")
        self.assertEqual(working["session_id"], "session-1")
        self.assertNotIn("task", working)
        self.assertNotIn("research_notes", working)
        self.assertNotIn("sources", working)
        self.assertNotIn("agent_handoffs", working)
        history = memory["history_summary_store"]
        self.assertTrue(history["history_exists"])
        self.assertTrue(history["notepad_exists"])
        self.assertEqual(history["history_summary"], "compressed history")
        self.assertEqual(history["notepad"], "durable notes")
        self.assertEqual(
            [event["summary"] for event in history["compression_events"]],
            ["4"],
        )
        formatted = format_layered_memory_for_prompt(memory)
        self.assertNotIn("task", json.loads(formatted)["working_memory"])

    def test_search_agent_profile_does_not_load_long_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "NOTEPAD.md").write_text("n" * 5000, encoding="utf-8")
            (root / "HISTORY_SUMMARY.md").write_text("h" * 5000, encoding="utf-8")
            memory = build_layered_memory(
                {"runtime": RuntimeState(root), "task": "research", "session_id": "s1"},
                node="searchAgent",
            )

        self.assertEqual(memory["working_memory"], {"node": "searchAgent", "session_id": "s1"})
        history = memory["history_summary_store"]
        self.assertEqual(history["history_summary"], "")
        self.assertEqual(history["notepad"], "")
        self.assertEqual(history["recent_task_memories"], [])

    def test_missing_memory_files_are_reported_without_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = build_layered_memory({"runtime": RuntimeState(root)})
            self.assertFalse(memory["history_summary_store"]["history_exists"])
            self.assertFalse(memory["history_summary_store"]["notepad_exists"])
            self.assertFalse((root / "NOTEPAD.md").exists())


if __name__ == "__main__":
    unittest.main()
