from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from tacoagent.core.runtime import RuntimeState
from tacoagent.graph.context_compressor_node import context_compressor_node


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


class ContextCompressorTests(unittest.TestCase):
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

        self.assertIsInstance(result["messages"][0], RemoveMessage)
        self.assertEqual(result["messages"][0].id, REMOVE_ALL_MESSAGES)
        self.assertEqual(
            result["messages"][1].content,
            "Resume from the verified implementation state.",
        )
        self.assertEqual(result["context_token_count"], 12)
        self.assertFalse(result["context_should_compress"])
        self.assertEqual(len(result["research_notes"]), 1600)
        self.assertEqual(len(result["plan_summary"]), 1400)
        self.assertEqual(len(result["agent_handoffs"]), 6)
        self.assertEqual(len(result["agent_handoffs"][0]["result"]), 800)
        self.assertEqual(len(result["verification_results"][0]["stdout"]), 1200)
        self.assertIn(result["history_summary"], history_text)
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

        self.assertEqual(
            result["context_token_count"], len(result["context_summary"]) // 4
        )


if __name__ == "__main__":
    unittest.main()
