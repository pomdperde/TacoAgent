import tempfile
import unittest
from pathlib import Path

from tacoagent.core.agent import TacoAgent
from tacoagent.core.workspace import WorkspaceBoundaryError, WorkspaceSandbox


class TacoAgentTests(unittest.TestCase):
    def test_run_tracks_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = TacoAgent(directory)
            self.assertEqual(agent.run("/help").turn, 1)
            self.assertEqual(agent.run("help").turn, 2)

    def test_files_stay_scoped_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "taco.txt").write_text("hello", encoding="utf-8")
            response = TacoAgent(directory).run("/files")
            self.assertIn("taco.txt", response.content)

    def test_workspace_rejects_escape_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = WorkspaceSandbox(directory)
            with self.assertRaises(WorkspaceBoundaryError):
                sandbox.resolve("../outside.txt")
            with self.assertRaises(WorkspaceBoundaryError):
                sandbox.resolve(Path(directory).resolve().parent / "outside.txt")


if __name__ == "__main__":
    unittest.main()
