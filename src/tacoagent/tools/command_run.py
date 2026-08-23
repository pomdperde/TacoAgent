from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from tacoagent.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    classify_command_risk,
    normalize_approval_mode,
)
from tacoagent.core.workspace import WorkspaceSandbox


@dataclass(slots=True)
class CommandRunTool:
    """Run a command with a fixed workspace cwd and bounded execution time."""

    workspace: WorkspaceSandbox
    approval_mode: str = "inline"
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None = None
    name: str = "command_run"
    description: str = "Run a shell command from the workspace with timeout control."

    def invoke(self, **kwargs: Any) -> dict[str, object]:
        command = str(kwargs.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        try:
            timeout_seconds = float(kwargs.get("timeout_seconds", 30))
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout_seconds must be a number") from exc
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if timeout_seconds > 300:
            raise ValueError("timeout_seconds must not exceed 300 seconds")

        risk_reason = classify_command_risk(command)
        approval_request = None
        if risk_reason:
            approval_request = ApprovalRequest.create(command, risk_reason)
            mode = normalize_approval_mode(self.approval_mode)
            if mode == "deny":
                return _approval_denied(approval_request, "approval mode is deny")
            if mode == "inline":
                if self.approval_handler is None:
                    return _approval_denied(
                        approval_request, "no inline approval handler configured"
                    )
                try:
                    raw_decision = self.approval_handler(approval_request)
                    decision = (
                        raw_decision
                        if isinstance(raw_decision, ApprovalDecision)
                        else ApprovalDecision(bool(raw_decision))
                    )
                except Exception as exc:
                    return _approval_denied(
                        approval_request,
                        f"approval handler failed: {type(exc).__name__}: {exc}",
                    )
                if not decision.approved:
                    return _approval_denied(
                        approval_request, decision.reason or "approval denied"
                    )

        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            result = {
                "ok": False,
                "command": command,
                "cwd": str(self.workspace.root),
                "exit_code": None,
                "stdout": _timeout_text(exc.stdout),
                "stderr": _timeout_text(exc.stderr),
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
                "error": f"Command timed out after {timeout_seconds:g} seconds",
            }
            if approval_request is not None:
                result.update(_approval_metadata(approval_request))
            return result

        result = {
            "ok": completed.returncode == 0,
            "command": command,
            "cwd": str(self.workspace.root),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
            "timeout_seconds": timeout_seconds,
        }
        if approval_request is not None:
            result.update(_approval_metadata(approval_request))
        return result


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _approval_denied(request: ApprovalRequest, reason: str) -> dict[str, object]:
    return {
        "ok": False,
        "command": request.command,
        "exit_code": None,
        "stdout": "",
        "stderr": reason,
        "timed_out": False,
        "requires_approval": True,
        "approval_id": request.id,
        "risk_reason": request.risk_reason,
        "approval_denied": True,
        "error": reason,
    }


def _approval_metadata(request: ApprovalRequest) -> dict[str, object]:
    return {
        "requires_approval": True,
        "approval_id": request.id,
        "risk_reason": request.risk_reason,
    }
