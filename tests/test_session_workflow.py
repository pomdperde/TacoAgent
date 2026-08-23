import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tacoagent.core.session import load_or_create_session
from tacoagent.workflow.session_workflow import run_session_event_stream, stream_session_events


class SessionEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mem0_patch = patch(
            "tacoagent.memory.memory.add_mem0_task_memory",
            return_value={"ok": False, "stored": False, "error": "disabled in unit test"},
        )
        self.mem0_patch.start()

    def tearDown(self) -> None:
        self.mem0_patch.stop()

    def test_session_driver_injects_context_and_records_both_roles(self) -> None:
        captured = {}

        def fake_stream(task, **kwargs):
            captured["task"] = task
            captured.update(kwargs)
            yield {
                "type": "intent_route",
                "intent_route": "chat",
                "intent_confidence": 0.9,
            }
            yield {"type": "final_answer", "content": "你好！"}

        with tempfile.TemporaryDirectory() as directory:
            events = list(
                run_session_event_stream(
                    "你好",
                    session_workspace=directory,
                    stream=fake_stream,
                    stream_kwargs={"max_attempts": 3},
                )
            )
            session = load_or_create_session(Path(directory))

        self.assertEqual(events[0]["type"], "session_turn")
        self.assertEqual(events[-1]["type"], "session_saved")
        self.assertEqual(events[-1]["route"], "chat")
        self.assertNotIn("你好", captured["session_context"])
        self.assertEqual(captured["session_turn"], 1)
        self.assertEqual(captured["max_attempts"], 3)
        self.assertEqual(
            [item["role"] for item in session["recent_turns"]],
            ["user", "assistant"],
        )
        self.assertEqual(session["recent_turns"][-1]["route"], "chat")

    def test_follow_up_turn_receives_previous_summary(self) -> None:
        contexts = []

        def fake_stream(_task, **kwargs):
            contexts.append(kwargs["session_context"])
            yield {"type": "intent_route", "intent_route": "workflow"}
            yield {"type": "final_answer", "content": "第一轮完成"}

        with tempfile.TemporaryDirectory() as directory:
            list(run_session_event_stream("创建文件", session_workspace=directory, stream=fake_stream))
            list(run_session_event_stream("继续", session_workspace=directory, stream=fake_stream))

        self.assertEqual(len(contexts), 2)
        self.assertIn("第一轮完成", contexts[1])
        self.assertNotIn("继续", contexts[1])

    def test_failed_turn_is_persisted_before_exception_is_reraised(self) -> None:
        def failing_stream(_task, **_kwargs):
            yield {"type": "intent_route", "intent_route": "workflow"}
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                list(
                    run_session_event_stream(
                        "fail", session_workspace=directory, stream=failing_stream
                    )
                )
            session = load_or_create_session(Path(directory))

        assistant = session["recent_turns"][-1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["status"], "failed")
        self.assertIn("RuntimeError", assistant["content"])

    def test_workflow_turn_creates_external_task_memory(self) -> None:
        def fake_stream(_task, **_kwargs):
            yield {"type": "intent_route", "intent_route": "workflow"}
            yield {"type": "tool_call", "name": "file_create", "args": {"file_path": "a.py"}}
            yield {"type": "final_answer", "content": "created"}

        with tempfile.TemporaryDirectory() as directory:
            events = list(
                run_session_event_stream(
                    "create a.py", session_workspace=directory, stream=fake_stream
                )
            )
            memory_path = (
                Path(directory) / ".tacoagent" / "memory" / "task_summaries.jsonl"
            )

            self.assertTrue(memory_path.is_file())
            saved = next(event for event in events if event["type"] == "task_memory_saved")
            self.assertEqual(saved["status"], "completed")

    def test_closed_stream_records_cancelled_turn(self) -> None:
        def long_stream(_task, **_kwargs):
            yield {"type": "intent_route", "intent_route": "workflow"}
            yield {"type": "tool_call", "name": "slow"}

        with tempfile.TemporaryDirectory() as directory:
            events = run_session_event_stream(
                "cancel", session_workspace=directory, stream=long_stream
            )
            next(events)
            next(events)
            events.close()
            session = load_or_create_session(Path(directory))

        self.assertEqual(session["recent_turns"][-1]["status"], "cancelled")

    def test_unified_dispatcher_selects_react_workflow(self) -> None:
        workflow_events = iter(
            [
                {"type": "intent_route", "intent_route": "chat"},
                {"type": "final_answer", "content": "done"},
            ]
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.workflow.react_workflow.stream_react_workflow_events",
            return_value=workflow_events,
        ) as selected:
            list(
                stream_session_events(
                    "hello",
                    session_workspace=directory,
                    workflow_mode="react",
                    max_attempts=7,
                )
            )

        self.assertEqual(selected.call_args.kwargs["max_loops"], 7)
        self.assertIn("session_context", selected.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
