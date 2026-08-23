from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tacoagent.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    classify_command_risk,
    normalize_approval_mode,
)
from tacoagent.core.runtime import RuntimeState
from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.tools.command_run import CommandRunTool


class ApprovalTests(unittest.TestCase):
    def test_classifies_risky_commands_and_allows_safe_commands(self) -> None:
        cases = {
            "pip install flask": "Python package installation",
            "python -m pip install flask": "Python package installation",
            "echo ok && uv add flask": "Project dependency change with uv add",
            "npm install": "Node package installation",
            "curl https://example.com": "Network download command",
            "python -m http.server 8000": "Long-running development server",
        }
        for command, reason in cases.items():
            with self.subTest(command=command):
                self.assertEqual(classify_command_risk(command), reason)
        self.assertIsNone(classify_command_risk("python -m unittest"))

    def test_normalizes_modes_and_generates_request_id(self) -> None:
        self.assertEqual(normalize_approval_mode(None), "inline")
        self.assertEqual(normalize_approval_mode("AUTO"), "auto")
        self.assertEqual(normalize_approval_mode("unknown"), "inline")
        request = ApprovalRequest.create("pip install x", "risk")
        self.assertRegex(request.id, r"^approval-[0-9a-f]{8}$")
        self.assertEqual(request.tool_name, "BashTool")

    def test_deny_mode_never_executes_risky_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.tools.command_run.subprocess.run"
        ) as run:
            result = CommandRunTool(
                WorkspaceSandbox(directory), approval_mode="deny"
            ).invoke(command="pip install flask")

        self.assertFalse(result["ok"])
        self.assertTrue(result["approval_denied"])
        self.assertTrue(result["requires_approval"])
        run.assert_not_called()

    def test_auto_mode_executes_and_marks_approval_requirement(self) -> None:
        completed = subprocess.CompletedProcess(
            args="pip install flask", returncode=0, stdout="installed", stderr=""
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.tools.command_run.subprocess.run", return_value=completed
        ) as run:
            result = CommandRunTool(
                WorkspaceSandbox(directory), approval_mode="auto"
            ).invoke(command="pip install flask")

        self.assertTrue(result["ok"])
        self.assertTrue(result["requires_approval"])
        self.assertEqual(result["risk_reason"], "Python package installation")
        run.assert_called_once()

    def test_inline_mode_uses_handler_decision(self) -> None:
        requests = []

        def deny(request):
            requests.append(request)
            return ApprovalDecision(False, "not allowed")

        with tempfile.TemporaryDirectory() as directory, patch(
            "tacoagent.tools.command_run.subprocess.run"
        ) as run:
            result = CommandRunTool(
                WorkspaceSandbox(directory),
                approval_mode="inline",
                approval_handler=deny,
            ).invoke(command="npm install")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not allowed")
        self.assertEqual(len(requests), 1)
        run.assert_not_called()

    def test_runtime_normalizes_approval_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeState(directory, approval_mode="AUTO")
        self.assertEqual(runtime.approval_mode, "auto")


if __name__ == "__main__":
    unittest.main()
