from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4


RISK_PATTERNS = [
    (r"(?:^|&&|\|\||;)\s*(?:python\s+-m\s+)?pip\s+install\b", "Python package installation"),
    (r"(?:^|&&|\|\||;)\s*uv\s+add\b", "Project dependency change with uv add"),
    (r"(?:^|&&|\|\||;)\s*uv\s+sync\b", "Dependency synchronization with uv sync"),
    (r"(?:^|&&|\|\||;)\s*uv\s+pip\s+install\b", "Python package installation with uv pip"),
    (r"(?:^|&&|\|\||;)\s*npm\s+install\b", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*pnpm\s+install\b", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*yarn\s+(?:install\b|add\b)", "Node package installation"),
    (r"(?:^|&&|\|\||;)\s*(?:curl|wget)\b", "Network download command"),
    (r"(?:^|&&|\|\||;)\s*uvicorn\b", "Long-running development server"),
    (r"(?:^|&&|\|\||;)\s*python\s+-m\s+http\.server\b", "Long-running development server"),
]


def classify_command_risk(command: str) -> str | None:
    """Return the matching risk reason, or ``None`` for a safe command."""
    for pattern, reason in RISK_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE):
            return reason
    return None


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    command: str
    risk_reason: str
    tool_name: str = "BashTool"

    @classmethod
    def create(cls, command: str, risk_reason: str, tool_name: str = "BashTool"):
        return cls(
            id=f"approval-{uuid4().hex[:8]}",
            command=command,
            risk_reason=risk_reason,
            tool_name=tool_name,
        )


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str = ""


VALID_APPROVAL_MODES = {"inline", "auto", "deny"}


def normalize_approval_mode(mode: str | None) -> str:
    normalized = str(mode or "").strip().lower()
    return normalized if normalized in VALID_APPROVAL_MODES else "inline"
