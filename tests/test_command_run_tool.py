import json
import sys
import tempfile
import unittest
from pathlib import Path

from tacoagent import TacoAgent
from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.tools import CommandRunTool, build_tool_registry


def python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


class CommandRunToolTests(unittest.TestCase):
    def test_runs_with_workspace_as_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = CommandRunTool(WorkspaceSandbox(root)).invoke(
                command=python_command("import os; print(os.getcwd())")
            )
            self.assertTrue(result["ok"])
            self.assertEqual(Path(str(result["stdout"]).strip()), root)

    def test_timeout_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CommandRunTool(WorkspaceSandbox(directory)).invoke(
                command=python_command("import time; time.sleep(2)"),
                timeout_seconds=0.1,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["timed_out"])
            self.assertIsNone(result["exit_code"])

    def test_nonzero_exit_is_logged_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory, "commands.jsonl")
            registry = build_tool_registry(
                [CommandRunTool(WorkspaceSandbox(directory))], log_path=log_path
            )
            result = registry.invoke(
                "command_run",
                task_name="expected failure",
                command=python_command("raise SystemExit(7)"),
            )
            self.assertEqual(result["exit_code"], 7)
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[-1]["event"], "tool_failed")

    def test_agent_can_run_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            response = TacoAgent(directory).run(
                "/run " + python_command("print('taco command')")
            )
            self.assertEqual(response.content, "taco command")


if __name__ == "__main__":
    unittest.main()
