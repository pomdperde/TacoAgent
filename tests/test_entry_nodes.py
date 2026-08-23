import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from tacoagent.workflow.entry_nodes import (
    chat_responder_node,
    intent_route_fn,
    intent_router_node,
)


class EntryNodeTests(unittest.TestCase):
    def test_router_selects_workflow_for_confident_task(self) -> None:
        model = Mock()
        model.invoke.return_value = AIMessage(
            content='{"route":"workflow","reason":"needs files","confidence":0.9}'
        )
        with patch("tacoagent.workflow.entry_nodes.create_model", return_value=model):
            result = intent_router_node({"task": "create app.py"})
        self.assertEqual(result["intent_route"], "workflow")
        self.assertEqual(intent_route_fn(result), "workflow")

    def test_router_defaults_to_chat_when_output_is_ambiguous(self) -> None:
        model = Mock()
        model.invoke.return_value = AIMessage(
            content='{"route":"workflow","reason":"uncertain","confidence":0.4}'
        )
        with patch("tacoagent.workflow.entry_nodes.create_model", return_value=model):
            result = intent_router_node({"task": "继续"})
        self.assertEqual(result["intent_route"], "chat")
        self.assertEqual(intent_route_fn(result), "chat")

    def test_router_defaults_to_chat_on_model_failure(self) -> None:
        with patch(
            "tacoagent.workflow.entry_nodes.create_model", side_effect=ValueError("no key")
        ):
            result = intent_router_node({"task": "hello"})
        self.assertEqual(result["intent_route"], "chat")

    def test_chat_responder_returns_final_answer(self) -> None:
        model = Mock()
        model.invoke.return_value = AIMessage(content="你好，我是 TacoAgent。")
        with patch("tacoagent.workflow.entry_nodes.create_model", return_value=model):
            result = chat_responder_node({"task": "你好"})
        self.assertEqual(result["chat_response"], result["final_answer"])


if __name__ == "__main__":
    unittest.main()
