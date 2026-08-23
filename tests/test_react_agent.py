import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from tacoagent.core import react as react_module
from tacoagent.workflow import react_workflow


class FakeBoundModel:
    def __init__(self):
        self.responses = iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "echo", "args": {"text": "taco"}, "id": "call-1"}
                    ],
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 3,
                        "total_tokens": 13,
                    },
                ),
                AIMessage(
                    content="Finished.",
                    usage_metadata={
                        "input_tokens": 15,
                        "output_tokens": 2,
                        "total_tokens": 17,
                    },
                ),
            ]
        )
        self.seen_messages = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.seen_messages = list(messages)
        return next(self.responses)


class ReactAgentTests(unittest.TestCase):
    def test_streams_tool_events_timing_and_accumulated_tokens(self) -> None:
        def echo(text: str):
            return {"ok": True, "text": text}

        tool = StructuredTool.from_function(
            func=echo, name="echo", description="Echo text."
        )
        model = FakeBoundModel()
        with tempfile.TemporaryDirectory() as directory, patch.object(
            react_module, "create_model", return_value=model
        ), patch.object(react_module, "build_structured_tools", return_value=[tool]):
            events = list(
                react_module.stream_agent_events("do work", workspace=directory)
            )

        self.assertEqual(
            [event["type"] for event in events],
            ["ai_message", "tool_call", "tool_result", "ai_message", "final_answer"],
        )
        self.assertEqual(events[2]["result"]["text"], "taco")
        self.assertGreaterEqual(events[2]["duration_ms"], 0)
        self.assertEqual(events[-1]["token_usage"]["total_tokens"], 30)
        self.assertTrue(any(isinstance(message, ToolMessage) for message in model.seen_messages))

    def test_react_logic_is_available_as_a_streaming_workflow(self) -> None:
        events = [
            {"type": "tool_call", "name": "echo", "args": {"text": "hi"}},
            {"type": "tool_result", "name": "echo", "result": {"ok": True}},
            {
                "type": "final_answer",
                "content": "finished",
                "token_usage": {"total_tokens": 7},
            },
        ]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            react_workflow, "stream_agent_events", return_value=iter(events)
        ), patch.object(
            react_workflow,
            "intent_router_node",
            return_value={"intent_route": "workflow", "intent_confidence": 1.0},
        ):
            result = list(
                react_workflow.stream_react_workflow_events(
                    "do work", workspace=directory, max_loops=4
                )
            )
            trace_root = next((Path(directory) / ".tacoagent" / "traces").glob("trace-*"))
            log_text = (trace_root / "events.jsonl").read_text(encoding="utf-8")
            self.assertFalse(
                (Path(directory) / ".tacoagent" / "workflow_events.jsonl").exists()
            )

        self.assertEqual(
            [event["type"] for event in result],
            [
                "node_start",
                "node_output",
                "intent_route",
                "memory_snapshot",
                "node_start",
                "tool_call",
                "tool_result",
                "node_output",
                "final_answer",
            ],
        )
        self.assertEqual(result[-1]["content"], "finished")
        self.assertEqual(result[-1]["token_usage"]["total_tokens"], 7)
        records = [
            json.loads(line)
            for line in log_text.splitlines()
        ]
        self.assertEqual(records[0]["workflow"], "react")
        self.assertEqual(records[0]["type"], "run_start")
        self.assertTrue(any(record["type"] == "memory_snapshot" for record in records))
        self.assertTrue(all(record.get("timestamp") for record in records))


if __name__ == "__main__":
    unittest.main()
