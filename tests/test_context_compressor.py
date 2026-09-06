from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from tacoagent.core.runtime import RuntimeState
from tacoagent.graph.context_compressor_node import (
    _select_compaction_segments,
    context_compressor_node,
)


class CompressionModel:
    def invoke(self, _messages):
        return AIMessage(
            content=json.dumps(
                {
                    "summary": "Resume from the verified implementation state.",
                    "active_goal": "finish task",
                    "completed_work": ["implementation"],
                    "open_todos": [],
                    "important_files": ["app.py"],
                    "tool_findings": ["tests pass"],
                    "sources": [],
                    "next_steps": ["verify"],
                    "risks": [],
                }
            )
        )

    def get_num_tokens_from_messages(self, _messages):
        return 12


class BrokenTokenCompressionModel(CompressionModel):
    def get_num_tokens_from_messages(self, _messages):
        raise RuntimeError("no tokenizer")


class FailingCompressionModel:
    def invoke(self, _messages):
        raise TimeoutError("summary provider stalled")


class ContextCompressorTests(unittest.TestCase):
    def test_compression_failure_preserves_messages_by_returning_no_removals(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.context_compressor_node.create_model",
            return_value=FailingCompressionModel(),
        ):
            result = context_compressor_node({
                "runtime": RuntimeState(directory),
                "messages": [HumanMessage(content="keep me")],
                "context_token_count": 500,
                "context_summary": "previous",
            })

        self.assertNotIn("messages", result)
        self.assertFalse(result["context_should_compress"])
        self.assertEqual(result["context_summary"], "previous")
        self.assertEqual(result["compression_events"][-1]["reason"], "compression_failed")

    def test_cut_keeps_tool_call_and_result_together(self) -> None:
        messages = [
            HumanMessage(content="fix it"),
            AIMessage(content="reading", tool_calls=[{
                "id": "call-1", "name": "file_read", "args": {"path": "app.py"}
            }]),
            ToolMessage(content="x" * 80, tool_call_id="call-1", name="file_read"),
            AIMessage(content="next"),
        ]
        main, turn_prefix, retained, cut_index, split_turn = _select_compaction_segments(
            messages, keep_recent_tokens=5
        )

        self.assertEqual(main, [])
        self.assertEqual(turn_prefix, messages[:3])
        self.assertEqual(cut_index, 3)
        self.assertTrue(split_turn)
        self.assertIsInstance(retained[0], AIMessage)
        self.assertFalse(any(isinstance(message, ToolMessage) for message in retained))

    def test_replaces_messages_persists_summary_and_truncates_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.context_compressor_node.create_model", return_value=CompressionModel()
        ):
            state = {
                "runtime": RuntimeState(directory),
                "messages": [HumanMessage(content="large transcript")],
                "context_token_count": 500_000,
                "research_notes": "r" * 2000,
                "plan_summary": "p" * 2000,
                "agent_handoffs": [
                    {"instruction": str(index), "result": "x" * 1000}
                    for index in range(8)
                ],
                "verification_results": [
                    {
                        "command": "test",
                        "ok": False,
                        "exit_code": 1,
                        "stdout": "o" * 2000,
                        "stderr": "e" * 2000,
                    }
                ],
                "compression_events": [{"summary": "older"}],
            }
            result = context_compressor_node(state)
            history_text = (Path(directory) / "HISTORY_SUMMARY.md").read_text(
                encoding="utf-8"
            )
            compaction_ledger_exists = (
                Path(directory) / ".tacoagent/memory/compactions.jsonl"
            ).is_file()

        self.assertIsInstance(result["messages"][0], RemoveMessage)
        self.assertEqual(result["messages"][0].id, REMOVE_ALL_MESSAGES)
        self.assertIn("Resume from the verified implementation state.", result["messages"][1].content)
        self.assertEqual(result["context_token_count"], 12)
        self.assertFalse(result["context_should_compress"])
        self.assertEqual(len(result["research_notes"]), 1600)
        self.assertEqual(len(result["plan_summary"]), 1400)
        self.assertEqual(len(result["agent_handoffs"]), 6)
        self.assertEqual(len(result["agent_handoffs"][0]["result"]), 800)
        self.assertEqual(len(result["verification_results"][0]["stdout"]), 1200)
        self.assertIn(result["history_summary"], history_text)
        self.assertTrue(compaction_ledger_exists)
        self.assertEqual(len(result["compression_events"]), 2)
        event = result["compression_events"][-1]
        self.assertEqual(event["before_tokens"], 500_000)
        self.assertEqual(event["after_tokens"], 12)

    def test_new_token_count_falls_back_to_summary_length(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.context_compressor_node.create_model",
            return_value=BrokenTokenCompressionModel(),
        ):
            result = context_compressor_node(
                {"runtime": RuntimeState(directory), "messages": []}
            )

        self.assertGreaterEqual(result["context_token_count"], len(result["context_summary"]) // 4)


if __name__ == "__main__":
    unittest.main()
