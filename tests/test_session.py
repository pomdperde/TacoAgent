import json
import tempfile
import unittest
from pathlib import Path

from tacoagent.core.session import (
    MAX_SESSION_CONTEXT,
    MAX_STORED_TURNS,
    MAX_TURN_CONTENT,
    SESSION_FILE,
    SESSION_ROOT,
    SESSION_SUMMARY_FILE,
    SessionManager,
    append_assistant_turn,
    append_user_turn,
    build_session_context,
    load_or_create_session,
    save_session,
)


class SessionTests(unittest.TestCase):
    def test_create_append_save_and_reload_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            session = load_or_create_session(workspace)
            turn = append_user_turn(session, "你好")
            append_assistant_turn(
                session,
                turn=turn,
                route="chat",
                content="你好！",
                summary="打招呼",
            )
            save_session(workspace, session)
            loaded = load_or_create_session(workspace)

            self.assertEqual(turn, 1)
            self.assertEqual(loaded["session_id"], session["session_id"])
            self.assertEqual(loaded["turn_index"], 1)
            self.assertEqual(len(loaded["recent_turns"]), 2)
            root = workspace / SESSION_ROOT
            self.assertTrue((root / SESSION_FILE).is_file())
            self.assertIn("打招呼", (root / SESSION_SUMMARY_FILE).read_text(encoding="utf-8"))

    def test_turn_content_and_context_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            session = load_or_create_session(workspace)
            for index in range(12):
                turn = append_user_turn(session, f"{index}:" + "x" * 5000)
                append_assistant_turn(
                    session,
                    turn=turn,
                    route="workflow",
                    content="y" * 5000,
                )
            context = build_session_context(workspace, session)

            self.assertLessEqual(len(session["recent_turns"][0]["content"]), MAX_TURN_CONTENT)
            self.assertLessEqual(len(context), MAX_SESSION_CONTEXT)
            self.assertIn("Session ID:", context)
            self.assertIn("Turn index: 12", context)

    def test_context_lists_files_but_not_internal_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("print('taco')", encoding="utf-8")
            session = load_or_create_session(workspace)
            context = build_session_context(workspace, session)

            self.assertIn("app.py", context)
            self.assertNotIn("session.json", context)

    def test_corrupt_json_is_replaced_with_valid_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            root = workspace / SESSION_ROOT
            root.mkdir(parents=True)
            (root / SESSION_FILE).write_text("{broken", encoding="utf-8")
            session = load_or_create_session(workspace)

            self.assertTrue(session["session_id"])
            self.assertEqual(session["turn_index"], 0)
            payload = json.loads((root / SESSION_FILE).read_text(encoding="utf-8"))
            self.assertEqual(payload["session_id"], session["session_id"])

    def test_save_trims_history_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            session = load_or_create_session(workspace)
            for index in range(MAX_STORED_TURNS + 10):
                append_user_turn(session, str(index))
            save_session(workspace, session)

            self.assertEqual(len(session["recent_turns"]), MAX_STORED_TURNS)
            self.assertFalse((workspace / SESSION_ROOT / f"{SESSION_FILE}.tmp").exists())

    def test_manager_context_excludes_current_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = SessionManager(directory)
            first = manager.start_turn("first unique input")
            manager.complete_turn(first, route="chat", content="first answer")
            second = manager.start_turn("second unique input")

            self.assertIn("first answer", second.context)
            self.assertNotIn("second unique input", second.context)


if __name__ == "__main__":
    unittest.main()
