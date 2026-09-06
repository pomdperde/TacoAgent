from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SWEInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    docker_image: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SWEInstance":
        required = ("instance_id", "repo", "base_commit", "problem_statement")
        missing = [key for key in required if not str(row.get(key, "")).strip()]
        if missing:
            raise ValueError(f"SWE-bench row is missing fields: {', '.join(missing)}")
        values = {key: str(row[key]) for key in required}
        values["docker_image"] = str(row.get("docker_image", ""))
        return cls(**values)


@dataclass(slots=True)
class SmokeResult:
    instance_id: str
    workflow_mode: str
    model_name: str
    status: str
    duration_seconds: float
    patch_chars: int
    final_answer: str = ""
    verifier_passed: bool | None = None
    error: str = ""
    event_count: int = 0
    tool_calls: int = 0
    workspace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
