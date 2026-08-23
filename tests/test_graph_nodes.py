from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from tacoagent.core.state import RuntimeState
from tacoagent.graph.nodes import actor_node, verifier_node, verifier_route
from tacoagent.graph.planner_node import planner_node


class FakeBoundModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.bound_tools = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, _messages):
        return next(self.responses)


class GraphNodeTests(unittest.TestCase):
    def test_planner_reads_todo_write_tool_call(self) -> None:
        response = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "plan-1",
                    "name": "TodoWriteTool",
                    "args": {
                        "plan_summary": "Build and test",
                        "todos": [
                            {"id": "todo-1", "content": "Build", "status": "pending", "note": ""}
                        ],
                        "acceptance_criteria": ["Works"],
                        "verification_commands": ["python -m unittest"],
                    },
                }
            ],
        )
        model = FakeBoundModel([response, AIMessage(content="Plan published.")])
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.planner_node.create_model", return_value=model
        ):
            result = planner_node(
                {"task": "build it", "runtime": RuntimeState(directory)}
            )

        self.assertEqual(result["plan_summary"], "Build and test")
        self.assertEqual(result["todos"][0]["id"], "todo-1")
        self.assertEqual(
            {tool.name for tool in model.bound_tools},
            {"TodoWriteTool", "CallSearchAgentTool", "CallCodeAgentTool"},
        )

    def test_actor_executes_todo_update_react_call(self) -> None:
        responses = [
            AIMessage(
                content="working",
                tool_calls=[
                    {
                        "id": "update-1",
                        "name": "TodoUpdateTool",
                        "args": {"id": "todo-1", "status": "completed", "note": "done"},
                    }
                ],
            ),
            AIMessage(content="Implemented and checked."),
        ]
        with tempfile.TemporaryDirectory() as directory:
            state = {
                "task": "build it",
                "runtime": RuntimeState(directory),
                "todos": [{"id": "todo-1", "content": "Build", "status": "pending", "note": ""}],
            }
            with patch("tacoagent.graph.nodes.create_model", return_value=FakeBoundModel(responses)):
                result = actor_node(state)

        self.assertEqual(result["todos"][0]["status"], "completed")
        self.assertEqual(result["last_actor_summary"], "Implemented and checked.")
        self.assertEqual(len(result["messages"]), 3)

    def test_supervisor_planner_delegates_search_then_code(self) -> None:
        responses = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "plan",
                        "name": "TodoWriteTool",
                        "args": {
                            "plan_summary": "Research then implement",
                            "todos": [
                                {"id": "todo-1", "content": "Build", "status": "pending", "note": ""}
                            ],
                            "acceptance_criteria": ["verified"],
                            "verification_commands": ["python -m unittest"],
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "search",
                        "name": "CallSearchAgentTool",
                        "args": {"instruction": "find official facts"},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "code",
                        "name": "CallCodeAgentTool",
                        "args": {"instruction": "implement using the research"},
                    }
                ],
            ),
            AIMessage(content="Specialists completed the planned work."),
        ]
        search_result = {
            "ok": True,
            "summary": "Official research",
            "sources": [{"title": "Docs", "url": "https://example.com/docs"}],
            "messages": [],
            "tool_events": [],
        }
        code_message = AIMessage(content="code specialist output")
        code_result = {
            "ok": True,
            "summary": "Implemented",
            "todos": [
                {"id": "todo-1", "content": "Build", "status": "completed", "note": "done"}
            ],
            "messages": [code_message],
            "tool_events": [],
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "tacoagent.graph.planner_node.create_model",
                return_value=FakeBoundModel(responses),
            ),
            patch(
                "tacoagent.graph.planner_node.run_search_agent",
                return_value=search_result,
            ),
            patch(
                "tacoagent.graph.planner_node.run_code_agent",
                return_value=code_result,
            ),
        ):
            result = planner_node(
                {"task": "research and build", "runtime": RuntimeState(directory)}
            )

        self.assertEqual(result["research_notes"], "Official research")
        self.assertEqual(result["sources"][0]["url"], "https://example.com/docs")
        self.assertEqual(result["todos"][0]["status"], "completed")
        self.assertEqual(result["code_agent_summary"], "Implemented")
        self.assertEqual(
            [handoff["to_agent"] for handoff in result["agent_handoffs"]],
            ["searchAgent", "codeAgent"],
        )
        self.assertIn(code_message, result["messages"])
        self.assertEqual(result["planner_summary"], "Specialists completed the planned work.")

    def test_verifier_combines_review_and_command_results(self) -> None:
        review = AIMessage(
            content='{"passed":true,"reason":"ok","checks":[],"recommended_next_instruction":""}'
        )
        with tempfile.TemporaryDirectory() as directory:
            state = {
                "runtime": RuntimeState(directory),
                "verification_commands": ["python -c \"print('ok')\""],
                "todos": [{"id": "todo-1", "content": "Build", "status": "in_progress", "note": ""}],
                "attempts": 0,
            }
            with patch("tacoagent.graph.nodes.create_model", return_value=FakeBoundModel([review])):
                result = verifier_node(state)

        self.assertTrue(result["passed"])
        self.assertTrue(result["verification_results"][0]["ok"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["todos"][0]["status"], "completed")

    def test_verifier_route(self) -> None:
        self.assertEqual(verifier_route({"passed": True}), "final")
        self.assertEqual(verifier_route({"attempts": 3, "max_attempts": 3}), "final")
        self.assertEqual(verifier_route({"attempts": 1, "max_attempts": 3}), "planner")


if __name__ == "__main__":
    unittest.main()
