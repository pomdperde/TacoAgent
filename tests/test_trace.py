from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tacoagent.core.state import RuntimeState
from tacoagent.core.trace import TraceRecorder, normalize_trace_mode
from tacoagent.workflow.multi_agent_workflow import stream_multi_agent_workflow_events
from tacoagent.workflow.react_workflow import stream_react_workflow_events
from tacoagent.workflow.single_agent_workflow import stream_workflow_events


def _monitor(state):
    return {
        "context_should_compress": False,
        "context_next_node": state.get("context_next_node", "verifier"),
    }


class TraceTests(unittest.TestCase):
    def test_recorder_collects_statistics_and_writes_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeState(directory, trace_id="trace-fixed")
            trace = TraceRecorder(runtime, task="stats task")
            trace.start({"task": "stats task", "workflow": "test"})
            trace.record_custom_event({"type": "tool_call", "name": "BashTool"})
            trace.record_custom_event(
                {
                    "type": "tool_result",
                    "result": {"ok": False, "requires_approval": True},
                }
            )
            trace.record_custom_event({"type": "handoff"})
            trace.record_custom_event({"type": "checkpoint_saved"})
            trace.record_graph_update({"planner": {}})
            trace.record_graph_update({"planner": {}, "verifier": {}})
            payload = trace.end(
                status="completed", latest_node="verifier", final_state={"passed": True}
            )

            self.assertEqual(payload["node_visits"], {"planner": 2, "verifier": 1})
            self.assertEqual(payload["tool_calls"], 1)
            self.assertEqual(payload["failed_tool_calls"], 1)
            self.assertEqual(payload["approval_count"], 1)
            self.assertEqual(payload["checkpoint_count"], 1)
            self.assertEqual(payload["handoff_count"], 1)
            self.assertIn("timeline_head", payload)
            self.assertIn("timeline_tail", payload)
            self.assertTrue((trace.root / "events.jsonl").is_file())
            self.assertTrue((trace.root / "trace.json").is_file())
            self.assertIn("## Timeline", (trace.root / "timeline.md").read_text(encoding="utf-8"))

    def test_trace_off_mode_writes_nothing(self) -> None:
        self.assertEqual(normalize_trace_mode("invalid"), "on")
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeState(directory, trace_mode="off")
            trace = TraceRecorder(runtime, task="off")
            self.assertIsNone(trace.start({"task": "off"}))
            self.assertIsNone(
                trace.end(status="completed", latest_node=None, final_state={})
            )
            self.assertFalse(trace.root.exists())

    def _assert_trace(self, workspace: Path, workflow: str) -> None:
        roots = list((workspace / ".tacoagent" / "traces").glob("trace-*"))
        self.assertEqual(len(roots), 1)
        summary = json.loads((roots[0] / "trace.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["workflow"], workflow)
        self.assertEqual(summary["task_status"], "completed")
        events = [
            json.loads(line)
            for line in (roots[0] / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        node_outputs = [event for event in events if event.get("type") == "node_output"]
        self.assertTrue(node_outputs)
        for event in node_outputs:
            for key in (
                "started_at",
                "ended_at",
                "duration_ms",
                "task_goal",
                "input_instruction",
                "return_result",
                "task_status",
            ):
                self.assertIn(key, event)
            self.assertEqual(event["task_status"], "completed")

    def test_single_agent_workflow_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, (
            patch(
                "tacoagent.workflow.single_agent_workflow.single_agent_planner_node",
                lambda _state: {"todos": [], "context_next_node": "actor"},
            )
        ), patch(
            "tacoagent.workflow.single_agent_workflow.intent_router_node",
            lambda _state: {"intent_route": "workflow", "intent_confidence": 1.0},
        ), patch(
            "tacoagent.workflow.single_agent_workflow.context_monitor_node", _monitor
        ), patch(
            "tacoagent.workflow.single_agent_workflow.actor_node",
            lambda _state: {"last_actor_summary": "done"},
        ), patch(
            "tacoagent.workflow.single_agent_workflow.verifier_node",
            lambda _state: {"passed": True, "attempts": 1},
        ):
            list(stream_workflow_events("trace task", workspace=directory))
            self._assert_trace(Path(directory), "single-agent")

    def test_multi_agent_workflow_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.workflow.multi_agent_workflow.planner_node",
            lambda _state: {"todos": [], "context_next_node": "verifier"},
        ), patch(
            "tacoagent.workflow.multi_agent_workflow.intent_router_node",
            lambda _state: {"intent_route": "workflow", "intent_confidence": 1.0},
        ), patch(
            "tacoagent.workflow.multi_agent_workflow.context_monitor_node", _monitor
        ), patch(
            "tacoagent.workflow.multi_agent_workflow.verifier_node",
            lambda _state: {"passed": True, "attempts": 1},
        ):
            list(stream_multi_agent_workflow_events("trace task", workspace=directory))
            self._assert_trace(Path(directory), "multi-agent")

    def test_react_workflow_trace(self) -> None:
        react_events = iter(
            [{"type": "final_answer", "content": "done", "token_usage": {}}]
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.workflow.react_workflow.stream_agent_events", return_value=react_events
        ), patch(
            "tacoagent.workflow.react_workflow.intent_router_node",
            lambda _state: {"intent_route": "workflow", "intent_confidence": 1.0},
        ):
            list(stream_react_workflow_events("trace task", workspace=directory))
            self._assert_trace(Path(directory), "react")


if __name__ == "__main__":
    unittest.main()
