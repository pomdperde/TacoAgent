import unittest
import importlib
from unittest.mock import patch

from typer.testing import CliRunner

from tacoagent.cli import app

cli_module = importlib.import_module("tacoagent.cli.app")


class TacoCliTests(unittest.TestCase):
    def test_info(self) -> None:
        result = CliRunner().invoke(app, ["info"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("TacoAgent", result.stdout)

    def test_run_renders_graph_workflow_events(self) -> None:
        events = iter(
            [
                {
                    "type": "plan_snapshot",
                    "plan_summary": "Make a file",
                    "todos": [{"id": "todo-1", "status": "pending", "content": "Create it"}],
                },
                {"type": "tool_call", "name": "file_read", "args": {"file_path": "a.txt"}},
                {"type": "tool_result", "name": "file_read", "result": {"ok": True}},
                {"type": "verifier_result", "passed": True, "attempts": 1, "results": []},
                {"type": "final_answer", "content": "done"},
            ]
        )
        with patch.object(cli_module, "stream_single_agent_session_events", return_value=events) as stream:
            result = CliRunner().invoke(app, ["run", "task"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Planner", result.stdout)
        self.assertIn("Verifier", result.stdout)
        self.assertIn("done", result.stdout)
        stream.assert_called_once()

    def test_run_can_switch_to_react_compatibility_workflow(self) -> None:
        events = iter([{"type": "final_answer", "content": "react done"}])
        with (
            patch.object(cli_module, "stream_react_session_events", return_value=events) as react_stream,
            patch.object(cli_module, "stream_single_agent_session_events") as graph_stream,
        ):
            result = CliRunner().invoke(
                app,
                ["run", "task", "--mode", "react", "--max-attempts", "5"],
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("react done", result.stdout)
        react_stream.assert_called_once()
        graph_stream.assert_not_called()

    def test_run_can_switch_to_multi_agent_workflow(self) -> None:
        events = iter([{"type": "final_answer", "content": "multi done"}])
        with (
            patch.object(
                cli_module, "stream_multi_agent_session_events", return_value=events
            ) as multi_stream,
            patch.object(cli_module, "stream_single_agent_session_events") as single_stream,
        ):
            result = CliRunner().invoke(
                app, ["run", "task", "--mode", "multi-agent"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("multi done", result.stdout)
        multi_stream.assert_called_once()
        single_stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()
