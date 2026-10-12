from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tacoagent.workflow.single_agent_workflow import build_workflow, stream_workflow_events


class GraphWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router_patch = patch(
            "tacoagent.workflow.single_agent_workflow.intent_router_node",
            lambda _state: {"intent_route": "workflow", "intent_confidence": 1.0},
        )
        self.router_patch.start()

    def tearDown(self) -> None:
        self.router_patch.stop()

    def test_pass_path_runs_planner_actor_verifier_and_final(self) -> None:
        visits: list[str] = []

        def planner(_state):
            visits.append("planner")
            return {"plan_summary": "plan", "todos": [], "context_next_node": "actor"}

        def verifier(state):
            visits.append("verifier")
            return {"passed": True, "attempts": int(state.get("attempts", 0)) + 1}

        def actor(_state):
            visits.append("actor")
            return {"last_actor_summary": "implemented"}

        with (
            patch("tacoagent.workflow.single_agent_workflow.single_agent_planner_node", planner),
            patch("tacoagent.workflow.single_agent_workflow.actor_node", actor),
            patch(
                "tacoagent.workflow.single_agent_workflow.context_monitor_node",
                lambda state: {
                    "context_should_compress": False,
                    "context_next_node": state.get("context_next_node", "actor"),
                },
            ),
            patch("tacoagent.workflow.single_agent_workflow.verifier_node", verifier),
        ):
            result = build_workflow().invoke({"task": "test", "attempts": 0, "max_attempts": 3})

        self.assertEqual(visits, ["planner", "actor", "verifier"])
        self.assertTrue(result["passed"])
        self.assertIn("completed and verified", result["final_answer"])

    def test_failed_verification_retries_actor_directly(self) -> None:
        visits: list[str] = []

        def planner(_state):
            visits.append("planner")
            return {"plan_summary": "revised plan", "todos": [], "context_next_node": "actor"}

        def verifier(state):
            visits.append("verifier")
            attempts = int(state.get("attempts", 0)) + 1
            return {
                "passed": attempts >= 2,
                "attempts": attempts,
                "last_error": "fix the failing check" if attempts < 2 else "",
            }

        def actor(_state):
            visits.append("actor")
            return {"last_actor_summary": "implementation"}

        with (
            patch("tacoagent.workflow.single_agent_workflow.single_agent_planner_node", planner),
            patch("tacoagent.workflow.single_agent_workflow.actor_node", actor),
            patch(
                "tacoagent.workflow.single_agent_workflow.context_monitor_node",
                lambda state: {
                    "context_should_compress": False,
                    "context_next_node": state.get("context_next_node", "actor"),
                },
            ),
            patch("tacoagent.workflow.single_agent_workflow.verifier_node", verifier),
        ):
            result = build_workflow().invoke({"task": "test", "attempts": 0, "max_attempts": 3})

        self.assertEqual(
            visits,
            ["planner", "actor", "verifier", "actor", "verifier"],
        )
        self.assertEqual(result["attempts"], 2)
        self.assertTrue(result["passed"])

    def test_failed_workflow_stops_at_max_attempts(self) -> None:
        def verifier(state):
            return {
                "passed": False,
                "attempts": int(state.get("attempts", 0)) + 1,
                "last_error": "still failing",
            }

        with (
            patch("tacoagent.workflow.single_agent_workflow.single_agent_planner_node", lambda _state: {"todos": [], "context_next_node": "actor"}),
            patch("tacoagent.workflow.single_agent_workflow.actor_node", lambda _state: {}),
            patch(
                "tacoagent.workflow.single_agent_workflow.context_monitor_node",
                lambda state: {
                    "context_should_compress": False,
                    "context_next_node": state.get("context_next_node", "actor"),
                },
            ),
            patch("tacoagent.workflow.single_agent_workflow.verifier_node", verifier),
        ):
            result = build_workflow().invoke({"task": "test", "attempts": 0, "max_attempts": 2})

        self.assertFalse(result["passed"])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("did not pass verification", result["final_answer"])

    def test_stream_records_every_node_output_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, (
            patch(
                "tacoagent.workflow.single_agent_workflow.single_agent_planner_node",
                lambda _state: {"plan_summary": "plan", "todos": [], "context_next_node": "actor"},
            )
        ), patch(
            "tacoagent.workflow.single_agent_workflow.actor_node",
            lambda _state: {"last_actor_summary": "done", "todos": []},
        ), patch(
            "tacoagent.workflow.single_agent_workflow.context_monitor_node",
            lambda state: {
                "context_should_compress": False,
                "context_next_node": state.get("context_next_node", "actor"),
            },
        ), patch(
            "tacoagent.workflow.single_agent_workflow.verifier_node",
            lambda _state: {"passed": True, "attempts": 1},
        ):
            events = list(
                stream_workflow_events("task", workspace=directory, max_attempts=2)
            )
            trace_root = next((Path(directory) / ".tacoagent" / "traces").glob("trace-*"))
            log_text = (trace_root / "events.jsonl").read_text(encoding="utf-8")
            checkpoint = json.loads(
                (Path(directory) / ".tacoagent" / "checkpoints" / "checkpoint.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(
                (Path(directory) / ".tacoagent" / "workflow_events.jsonl").exists()
            )

        starts = [event["node"] for event in events if event["type"] == "node_start"]
        outputs = [event["node"] for event in events if event["type"] == "node_output"]
        self.assertEqual(
            starts, ["intent_router", "single_agent_planner", "context_monitor", "actor", "verifier", "final"]
        )
        self.assertEqual(outputs, starts)
        checkpoint_events = [event for event in events if event["type"] == "checkpoint_saved"]
        self.assertEqual(
            [event["latest_node"] for event in checkpoint_events],
            starts,
        )
        self.assertEqual(checkpoint["status"], "completed")
        self.assertEqual(checkpoint["latest_node"], "final")
        self.assertEqual(checkpoint["resume_state"]["attempts"], 1)
        self.assertTrue(checkpoint["resume_state"]["passed"])
        records = [json.loads(line) for line in log_text.splitlines()]
        self.assertEqual(records[0]["workflow"], "single-agent")
        node_records = [record for record in records if record.get("type") == "node_output"]
        self.assertTrue(all(record["workflow"] == "graph" for record in node_records))
        self.assertEqual(
            [record["node"] for record in records if record["type"] == "node_output"],
            starts,
        )


if __name__ == "__main__":
    unittest.main()
