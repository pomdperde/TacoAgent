from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import HumanMessage

from tacoagent.core.checkpoint import (
    CheckpointManager,
    build_recovery_markdown,
    normalize_checkpoint_mode,
    resume_command,
)
from tacoagent.core.runtime import RuntimeState


class CheckpointTests(unittest.TestCase):
    def test_normalizes_checkpoint_modes(self) -> None:
        self.assertEqual(normalize_checkpoint_mode(None), "light")
        self.assertEqual(normalize_checkpoint_mode("STRICT"), "strict")
        self.assertEqual(normalize_checkpoint_mode("bad"), "light")

    def test_off_mode_does_not_create_checkpoint_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = CheckpointManager(RuntimeState(directory, checkpoint_mode="off"))
            result = manager.save({"task": "test"})
            self.assertIsNone(result)
            self.assertFalse(manager.root.exists())

    def test_light_mode_writes_metadata_recovery_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("print('ok')", encoding="utf-8")
            runtime = RuntimeState(root, checkpoint_mode="light")
            manager = CheckpointManager(runtime, task="fallback task")
            with patch(
                "tacoagent.core.checkpoint.snapshot_workspace_git",
                return_value="abc123",
            ):
                event = manager.save(
                    {
                        "task": "build app",
                        "runtime": runtime,
                        "messages": [HumanMessage(content="hello")],
                        "attempts": 1,
                    },
                    status="running",
                    latest_node="planner",
                )
            payload = json.loads(
                (manager.root / "checkpoint.json").read_text(encoding="utf-8")
            )
            recovery = (manager.root / "RECOVERY.md").read_text(encoding="utf-8")

        self.assertEqual(event["type"], "checkpoint_saved")
        self.assertEqual(payload["git_commit"], "abc123")
        self.assertEqual(payload["workspace_manifest"][0]["path"], "app.py")
        self.assertIn("build app", recovery)
        self.assertIn("abc123", recovery)
        self.assertFalse((manager.root / "state.json").exists())

    def test_strict_mode_saves_full_state_events_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = RuntimeState(root, checkpoint_mode="strict")
            manager = CheckpointManager(runtime)
            state = {
                "task": "resume task",
                "runtime": runtime,
                "messages": [HumanMessage(content="continue")],
                "attempts": 2,
                "max_attempts": 5,
                "todos": [],
            }
            with patch(
                "tacoagent.core.checkpoint.snapshot_workspace_git",
                return_value="commit-1",
            ):
                manager.save(
                    state,
                    latest_node="verifier",
                    event={"type": "node_output"},
                )
            with patch(
                "tacoagent.core.checkpoint.restore_workspace_git",
                return_value=True,
            ):
                inputs, event = CheckpointManager.load_resume_inputs(runtime)

            self.assertTrue((manager.root / "state.json").is_file())
            self.assertTrue((manager.root / "events.jsonl").is_file())
            self.assertEqual(inputs["task"], "resume task")
            self.assertEqual(inputs["attempts"], 2)
            self.assertEqual(inputs["messages"][0].content, "continue")
            self.assertIs(inputs["runtime"], runtime)
            self.assertTrue(event["workspace_restored"])

    def test_recovery_helpers_include_resume_information(self) -> None:
        workspace = Path("some workspace")
        command = resume_command(workspace)
        self.assertIn("tacoagent --resume", command)
        markdown = build_recovery_markdown(
            {
                "task": "task",
                "status": "interrupted",
                "git_commit": "commit",
                "workspace_manifest": [{"path": "app.py"}],
                "resume_command": command,
            }
        )
        self.assertIn("app.py", markdown)
        self.assertIn("interrupted", markdown)


if __name__ == "__main__":
    unittest.main()
