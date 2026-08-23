from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from tacoagent.core.state import RuntimeState
from tacoagent.graph.nodes import context_monitor_node, context_monitor_route


class CountingModel:
    def __init__(self, count: int) -> None:
        self.count = count
        self.messages = []

    def get_num_tokens_from_messages(self, messages):
        self.messages = list(messages)
        return self.count


class BrokenCountingModel:
    def get_num_tokens_from_messages(self, _messages):
        raise RuntimeError("tokenizer unavailable")


class ContextMonitorTests(unittest.TestCase):
    def test_uses_model_token_counter_with_layered_memory_message(self) -> None:
        model = CountingModel(125)
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.nodes.create_model", return_value=model
        ):
            result = context_monitor_node(
                {
                    "runtime": RuntimeState(directory),
                    "messages": [HumanMessage(content="task")],
                    "context_token_limit": 100,
                    "context_next_node": "planner",
                }
            )

        self.assertEqual(result["context_token_count"], 125)
        self.assertTrue(result["context_should_compress"])
        self.assertEqual(result["context_next_node"], "planner")
        self.assertEqual(len(model.messages), 2)
        self.assertIn("Layered memory snapshot", model.messages[-1].content)

    def test_falls_back_to_character_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.nodes.create_model", return_value=BrokenCountingModel()
        ):
            result = context_monitor_node(
                {
                    "runtime": RuntimeState(directory),
                    "messages": [HumanMessage(content="x" * 80)],
                    "context_token_limit": 400_000,
                }
            )

        self.assertGreaterEqual(result["context_token_count"], 20)
        self.assertFalse(result["context_should_compress"])
        self.assertEqual(result["context_next_node"], "verifier")

    def test_monitor_route_prioritizes_pass_then_compression(self) -> None:
        self.assertEqual(
            context_monitor_route({"passed": True, "context_should_compress": True}),
            "final",
        )
        self.assertEqual(
            context_monitor_route(
                {"passed": False, "context_should_compress": True, "context_next_node": "planner"}
            ),
            "context_compressor",
        )
        self.assertEqual(
            context_monitor_route({"context_next_node": "planner"}), "planner"
        )
        self.assertEqual(context_monitor_route({}), "verifier")


if __name__ == "__main__":
    unittest.main()
