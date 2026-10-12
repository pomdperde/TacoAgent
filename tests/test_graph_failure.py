from contextlib import ExitStack
import os
import tempfile
import unittest
from unittest.mock import patch

from tacoagent.workflow.graph_failure import WorkflowExecutionError
from tacoagent.workflow.single_agent_workflow import build_workflow, stream_workflow_events
from tacoagent.workflow.multi_agent_workflow import (
    build_multi_agent_workflow, stream_multi_agent_workflow_events,
)


class GraphFailureTests(unittest.TestCase):
    def setUp(self):
        tracing = patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
        })
        tracing.start()
        self.addCleanup(tracing.stop)

    def configure(self, stack, module, failed_node, visits):
        outputs = {
            "intent_router": {"intent_route": "workflow"},
            "chat_responder": {"final_answer": "hello"},
            "single_agent_planner": {"context_next_node": "actor"},
            "multi_agent_supervisor": {"context_next_node": "verifier"},
            "context_monitor": {"context_should_compress": False},
            "context_compressor": {},
            "actor": {"last_actor_summary": "done"},
            "verifier": {"passed": True, "attempts": 1},
            "final": {"final_answer": "ok"},
        }
        if failed_node == "chat_responder":
            outputs["intent_router"] = {"intent_route": "chat"}
        if failed_node == "context_compressor":
            outputs["context_monitor"] = {"context_should_compress": True}
        for node, output in outputs.items():
            if module.endswith("multi_agent_workflow") and node in {"actor", "single_agent_planner"}:
                continue
            if module.endswith("single_agent_workflow") and node == "multi_agent_supervisor":
                continue
            def run(state, name=node, result=output):
                visits.append(name)
                if name == failed_node:
                    raise RuntimeError("injected failure")
                return result
            stack.enter_context(patch(f"{module}.{node}_node", run))

    def test_every_node_failure_stops_normal_successors_without_retry(self):
        for module, builder, nodes in (
            ("single_agent_workflow", build_workflow,
             ["intent_router", "chat_responder", "single_agent_planner", "actor",
              "context_monitor", "context_compressor", "verifier", "final"]),
            ("multi_agent_workflow", build_multi_agent_workflow,
             ["intent_router", "chat_responder", "multi_agent_supervisor",
              "context_monitor", "context_compressor", "verifier", "final"]),
        ):
            for failed_node in nodes:
                with self.subTest(module=module, node=failed_node), ExitStack() as stack:
                    visits = []
                    self.configure(stack, f"tacoagent.workflow.{module}", failed_node, visits)
                    result = builder().invoke({"task": "test", "max_attempts": 2})
                    self.assertFalse(result["passed"])
                    self.assertEqual(result["workflow_error"]["node"], failed_node)
                    self.assertEqual(visits[-1], failed_node)
                    self.assertEqual(visits.count(failed_node), 1)
                    self.assertIn("injected failure", result["final_answer"])

    def test_stream_emits_failure_and_raises_for_session_failure_tracking(self):
        for module, stream in (
            ("single_agent_workflow", stream_workflow_events),
            ("multi_agent_workflow", stream_multi_agent_workflow_events),
        ):
            with self.subTest(module=module), tempfile.TemporaryDirectory() as workspace, ExitStack() as stack:
                self.configure(stack, f"tacoagent.workflow.{module}", "verifier", [])
                events = []
                with self.assertRaises(WorkflowExecutionError):
                    for event in stream("test", workspace=workspace):
                        events.append(event)
                self.assertEqual(events[-1]["type"], "workflow_error")
                self.assertEqual(events[-1]["node"], "verifier")
                self.assertFalse(any(e["type"] == "final_answer" for e in events))
