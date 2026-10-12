import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tacoagent.core.session import SessionManager
from tacoagent.core.session_space import SessionSpace, SessionBusyError, workspace_lease
from tacoagent.workflow.session_workflow import run_session_event_stream, stream_session_events


class SessionSpaceTests(unittest.TestCase):
    def test_public_entry_routes_to_owned_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            session_id, workspace = SessionSpace(directory, "alice").create()
            def fake(task, **kwargs):
                self.assertEqual(kwargs["workspace"], workspace)
                self.assertEqual(kwargs["session_id"], session_id)
                yield {"type": "final_answer", "content": "done"}
            with patch("tacoagent.workflow.single_agent_workflow.stream_workflow_events", fake):
                list(stream_session_events("hello", session_workspace=directory,
                                           user_id="alice", session_id=session_id))
            self.assertEqual(SessionManager(workspace).session["turn_index"], 1)
            with self.assertRaises(PermissionError):
                stream_session_events("hello", session_workspace=directory,
                                      user_id="bob", session_id=session_id)
            with self.assertRaises(ValueError):
                stream_session_events("hello", session_workspace=directory, user_id="alice")

    def test_users_and_sessions_have_independent_files_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "code.py").write_text("original")
            alice = SessionSpace(root, "alice")
            first, a = alice.create()
            second, b = alice.create()
            self.assertEqual(a, root / ".tacoagent" / "sessions" / first / "workspace")
            self.assertEqual(b, root / ".tacoagent" / "sessions" / second / "workspace")
            other, c = SessionSpace(root, "bob").create()
            (a / "code.py").write_text("alice edit")
            SessionManager(a).start_turn("private question")
            for path in (root, b, c):
                self.assertEqual((path / "code.py").read_text(), "original")
            self.assertNotIn("private question", SessionManager(b).context())
            self.assertEqual(SessionManager(a).session_id, first)
            self.assertEqual(alice.list(), sorted([first, second]))
            with self.assertRaises(PermissionError):
                alice.open(other)
            with self.assertRaises(ValueError):
                alice.open("../../bob")

    def test_legacy_user_directory_remains_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            space = SessionSpace(directory, "alice")
            session_id, workspace = space.create()
            space.legacy_root.mkdir(parents=True)
            (space.root / session_id).rename(space.legacy_root / session_id)
            self.assertEqual(space.open(session_id), space.legacy_root / session_id / "workspace")
            self.assertEqual(space.list(), [session_id])

    def test_process_lease_blocks_another_process_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                "from pathlib import Path; "
                "from tacoagent.core.session_space import workspace_lease; "
                "import sys; "
                "lease=workspace_lease(Path(sys.argv[1])); lease.__enter__(); lease.__exit__(None,None,None)"
            )
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
            with workspace_lease(Path(directory)):
                result = subprocess.run([sys.executable, "-c", script, directory],
                                        capture_output=True, env=env, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"SessionBusyError", result.stderr)
            with workspace_lease(Path(directory)):
                pass

    def test_stream_close_releases_lock_and_other_workspace_can_run(self):
        def stream(task, **kwargs):
            yield {"type": "final_answer", "content": task}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = run_session_event_stream("one", session_workspace=root, stream=stream)
            next(active)
            with self.assertRaises(SessionBusyError):
                next(run_session_event_stream("two", session_workspace=root, stream=stream))
            list(run_session_event_stream("other", session_workspace=root / "other", stream=stream))
            active.close()
            list(run_session_event_stream("three", session_workspace=root, stream=stream))
