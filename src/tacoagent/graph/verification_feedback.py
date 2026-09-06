from __future__ import annotations

import re
from typing import Any


_SIGNAL_LINE = re.compile(
    r"failed|error|exception|traceback|assert|timed?\s*out|not found|"
    r"no module named|command not found|is not recognized",
    re.IGNORECASE,
)


def _short_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def concise_command_evidence(result: dict[str, Any], *, limit: int = 1800) -> str:
    """Extract useful failure lines without feeding full command logs to the model."""
    output = f"{result.get('stderr', '')}\n{result.get('stdout', '')}".strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    signals = [line for line in lines if _SIGNAL_LINE.search(line)]
    selected = signals[:16] or lines[-12:]
    return _short_text("\n".join(selected), limit)


def build_verification_feedback(
    results: list[dict[str, Any]],
    checks: list[dict[str, Any]] | None = None,
    *,
    recommended_instruction: str = "",
) -> dict[str, Any]:
    """Build implementation-facing, environment-neutral verification feedback."""
    failed_commands = []
    for result in results:
        if result.get("status") == "passed":
            continue
        failed_commands.append({
            "command": str(result.get("command", "")),
            "status": str(result.get("status", "not_verified")),
            "category": str(result.get("category", "unknown")),
            "exit_code": result.get("exit_code"),
            "evidence": concise_command_evidence(result),
        })
    failed_checks = [
        {
            "name": str(check.get("name", "check")),
            "detail": _short_text(check.get("detail", ""), 600),
        }
        for check in (checks or [])
        if check.get("passed") is False
    ]
    return {
        "failed_commands": failed_commands,
        "failed_checks": failed_checks,
        "recommended_instruction": _short_text(recommended_instruction, 1000),
    }
