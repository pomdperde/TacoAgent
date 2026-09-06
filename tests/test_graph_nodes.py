from __future__ import annotations

import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage

from tacoagent.core.runtime import RuntimeState
from tacoagent.graph.actor_node import (
    _blocked_tool_reason,
    _compact_reprompt_messages,
    _phase_tool_rejection,
    _restrict_first_edit_read,
    _should_defer_edit,
    _response_stop_reason,
    _tool_calls_are_truncated,
    actor_node,
)
from tacoagent.graph.verifier_node import verifier_node, verifier_route
from tacoagent.graph.verifier_node import _verification_status
from tacoagent.graph.verifier_node import _run_docker_commands
from tacoagent.graph.verification_feedback import build_verification_feedback
from tacoagent.graph.multi_agent_supervisor_node import multi_agent_supervisor_node


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
    def test_verifier_runs_commands_in_swebench_docker_image(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="1 passed", stderr="")
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.verifier_node.subprocess.run", return_value=completed
        ) as run:
            result = _run_docker_commands(
                "swebench/example:latest", Path(directory), ["python -m pytest -q"], timeout=120
            )
        invocations = [item.args[0] for item in run.call_args_list]
        invocation = next(item for item in invocations if "conda activate testbed" in str(item[-1]))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["environment"], "swebench-docker")
        self.assertIn("conda activate testbed", invocation[-1])
        self.assertIn("setuptools<70", invocation[-1])
        self.assertIn("Cython<3", invocation[-1])
        self.assertIn("pip install -e . --no-build-isolation", invocation[-1])

    def test_actor_hard_phase_tool_permissions(self) -> None:
        self.assertIn("read-only", _phase_tool_rejection(
            "diagnosis", "file_edit", first_edits=0, verification_calls=0, correction_edits=0
        ))
        self.assertEqual(_phase_tool_rejection(
            "first_edit", "file_edit", first_edits=0, verification_calls=0, correction_edits=0
        ), "")
        self.assertEqual(_phase_tool_rejection(
            "first_edit", "file_read", first_edits=0, verification_calls=0,
            correction_edits=0, first_edit_read_calls=0
        ), "")
        self.assertIn("already", _phase_tool_rejection(
            "first_edit", "regex_search", first_edits=0, verification_calls=0,
            correction_edits=0, first_edit_read_calls=1
        ))
        self.assertIn("exactly one", _phase_tool_rejection(
            "minimal_verification", "command_run", first_edits=1, verification_calls=1, correction_edits=0
        ))
        self.assertIn("already", _phase_tool_rejection(
            "correction", "file_edit", first_edits=1, verification_calls=1, correction_edits=1
        ))

    def test_first_edit_read_is_bounded(self) -> None:
        self.assertEqual(
            _restrict_first_edit_read("file_read", {"file_path": "module.py", "max_chars": 99_000})["max_chars"],
            12_000,
        )
        self.assertEqual(
            _restrict_first_edit_read("regex_search", {"pattern": "value", "max_results": 500})["max_results"],
            40,
        )

    def test_minimal_verification_edit_is_deferred_to_correction(self) -> None:
        self.assertTrue(_should_defer_edit(
            "minimal_verification", "file_edit", deferred_exists=False, correction_edits=0
        ))
        self.assertFalse(_should_defer_edit(
            "final_verification", "file_edit", deferred_exists=False, correction_edits=0
        ))
        self.assertFalse(_should_defer_edit(
            "minimal_verification", "file_edit", deferred_exists=True, correction_edits=0
        ))

    def test_policy_reprompt_uses_compact_context(self) -> None:
        messages = _compact_reprompt_messages(
            {
                "task": "fix it",
                "plan_summary": "edit module.py",
                "acceptance_criteria": ["works"],
                "verification_commands": ["python -m pytest"],
                "verification_feedback": {"status": "failed"},
                "layered_memory": {"large": "x" * 100_000},
            },
            "first_edit",
            first_edit_read_calls=1,
        )
        self.assertEqual(len(messages), 3)
        self.assertNotIn("layered_memory", messages[1].content)
        self.assertIn("file_edit", messages[2].content)

    def test_actor_detects_truncated_tool_calls(self) -> None:
        response = AIMessage(
            content="",
            tool_calls=[{
                "id": "read-1",
                "name": "file_read",
                "args": {"path": "partial.py"},
            }],
            response_metadata={"finish_reason": "length"},
        )
        self.assertEqual(_response_stop_reason(response), "length")
        self.assertTrue(_tool_calls_are_truncated(response))

    def test_actor_model_failure_becomes_loop_result(self) -> None:
        class FailingModel(FakeBoundModel):
            def invoke(self, _messages):
                raise TimeoutError("provider stalled")

        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.graph.actor_node.create_model",
            return_value=FailingModel([]),
        ):
            result = actor_node({
                "task": "build it",
                "runtime": RuntimeState(directory),
                "todos": [],
            })

        self.assertIn("TimeoutError", result["last_actor_summary"])
        self.assertEqual(result["messages"], [])

    def test_actor_loop_policy_forces_edit_after_investigation_budget(self) -> None:
        reason = _blocked_tool_reason(
            "regex_search",
            has_production_patch=False,
            read_only_calls=6,
            investigation_limit=6,
            evidence_calls=0,
            convergence_limit=4,
            finalizing=False,
        )
        self.assertIn("Investigation budget", reason)
        self.assertEqual(
            _blocked_tool_reason(
                "file_edit",
                has_production_patch=False,
                read_only_calls=6,
                investigation_limit=6,
                evidence_calls=0,
                convergence_limit=4,
                finalizing=False,
            ),
            "",
        )

    def test_actor_loop_policy_stops_post_edit_evidence_sprawl(self) -> None:
        reason = _blocked_tool_reason(
            "command_run",
            has_production_patch=True,
            read_only_calls=9,
            investigation_limit=6,
            evidence_calls=4,
            convergence_limit=4,
            finalizing=False,
        )
        self.assertIn("Post-edit evidence budget", reason)

    def test_actor_loop_policy_disables_broad_discovery_while_finalizing(self) -> None:
        reason = _blocked_tool_reason(
            "list_workspace_files",
            has_production_patch=True,
            read_only_calls=1,
            investigation_limit=6,
            evidence_calls=1,
            convergence_limit=4,
            finalizing=True,
        )
        self.assertIn("finalizing", reason)

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
            "tacoagent.graph.multi_agent_supervisor_node.create_model", return_value=model
        ):
            result = multi_agent_supervisor_node(
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
            with patch("tacoagent.graph.actor_node.create_model", return_value=FakeBoundModel(responses)):
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
                "tacoagent.graph.multi_agent_supervisor_node.create_model",
                return_value=FakeBoundModel(responses),
            ),
            patch(
                "tacoagent.graph.multi_agent_supervisor_node.run_search_agent",
                return_value=search_result,
            ),
            patch(
                "tacoagent.graph.multi_agent_supervisor_node.run_code_agent",
                return_value=code_result,
            ),
        ):
            result = multi_agent_supervisor_node(
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
        self.assertEqual(result["supervisor_summary"], "Specialists completed the planned work.")

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
            with patch("tacoagent.graph.verifier_node.create_model", return_value=FakeBoundModel([review])):
                result = verifier_node(state)

        self.assertTrue(result["passed"])
        self.assertTrue(result["verification_results"][0]["ok"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["todos"][0]["status"], "completed")

    def test_verifier_route(self) -> None:
        self.assertEqual(verifier_route({"passed": True}), "final")
        self.assertEqual(verifier_route({"attempts": 3, "max_attempts": 3}), "final")
        self.assertEqual(verifier_route({"attempts": 1, "max_attempts": 3}), "actor")

    def test_verification_status_distinguishes_environment_blocker(self) -> None:
        status, category = _verification_status({
            "ok": False,
            "stderr": "ModuleNotFoundError: No module named 'hypothesis'",
        })
        self.assertEqual(status, "blocked")
        self.assertEqual(category, "environment_unavailable")

    def test_verification_status_preserves_real_test_failure(self) -> None:
        status, category = _verification_status({
            "ok": False,
            "stderr": "FAILED test_widget.py::test_value - assert 1 == 2",
        })
        self.assertEqual(status, "failed")
        self.assertEqual(category, "command_failed")

    def test_verification_feedback_is_structured_and_concise(self) -> None:
        feedback = build_verification_feedback(
            [{
                "command": "python -m unittest",
                "status": "failed",
                "category": "command_failed",
                "exit_code": 1,
                "stdout": "noise\nFAILED test_widget - assert 1 == 2\n" + "x" * 4000,
                "stderr": "",
            }],
            [{"name": "behavior", "passed": False, "detail": "wrong value"}],
            recommended_instruction="Fix only the failing behavior",
        )
        self.assertEqual(feedback["failed_commands"][0]["status"], "failed")
        self.assertIn("FAILED test_widget", feedback["failed_commands"][0]["evidence"])
        self.assertLessEqual(len(feedback["failed_commands"][0]["evidence"]), 1800)
        self.assertEqual(feedback["failed_checks"][0]["name"], "behavior")


if __name__ == "__main__":
    unittest.main()
