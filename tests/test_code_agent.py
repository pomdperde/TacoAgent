from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tacoagent.agents.code_agent import run_code_agent
from tacoagent.core.state import RuntimeState


class FakeModel:
    def __init__(self):
        self.seen_messages = []
        self.responses = iter(
            [
                AIMessage(
                    content="starting",
                    tool_calls=[
                        {
                            "id": "todo-start",
                            "name": "TodoUpdateTool",
                            "args": {
                                "id": "todo-1",
                                "status": "in_progress",
                                "note": "working",
                            },
                        }
                    ],
                ),
                AIMessage(
                    content="finishing",
                    tool_calls=[
                        {
                            "id": "todo-done",
                            "name": "TodoUpdateTool",
                            "args": {
                                "id": "todo-1",
                                "status": "completed",
                                "note": "implemented and checked",
                            },
                        }
                    ],
                ),
                AIMessage(content="Changed app.py and ran tests."),
            ]
        )

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.seen_messages = list(messages)
        return next(self.responses)


class CodeAgentTests(unittest.TestCase):
    def test_updates_and_persists_todos_with_memory_context(self) -> None:
        model = FakeModel()
        written = []
        with tempfile.TemporaryDirectory() as directory:
            state = {
                "task": "build app",
                "runtime": RuntimeState(directory),
                "session_context": "user requested tests",
                "research_notes": "official recommendation",
                "sources": [{"title": "Docs", "url": "https://example.com"}],
                "todos": [
                    {
                        "id": "todo-1",
                        "content": "Implement app",
                        "status": "pending",
                        "note": "",
                    }
                ],
            }
            with (
                patch("tacoagent.agents.code_agent.create_model", return_value=model),
                patch("tacoagent.agents.code_agent.build_tools", return_value=[]),
            ):
                result = run_code_agent(
                    state,
                    "implement the planned feature",
                    writer=written.append,
                )
            todo_text = (Path(directory) / "TODO.md").read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertEqual(result["todos"][0]["status"], "completed")
        self.assertEqual(result["summary"], "Changed app.py and ran tests.")
        self.assertIn("[x] todo-1", todo_text)
        self.assertEqual(
            [event["type"] for event in written],
            ["memory_snapshot", "tool_call", "tool_result", "tool_call", "tool_result"],
        )
        self.assertTrue(any(isinstance(message, ToolMessage) for message in result["messages"]))
        human = next(message for message in result["messages"] if isinstance(message, HumanMessage))
        context = json.loads(human.content)
        self.assertEqual(context["session_context"], "user requested tests")
        memory = json.loads(context["memory"])
        self.assertEqual(
            memory["working_memory"]["research_notes"], "official recommendation"
        )
        self.assertIn("TodoUpdateTool", {tool.name for tool in model.bound_tools})


if __name__ == "__main__":
    unittest.main()
