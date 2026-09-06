"""LangSmith dataset, target, and deterministic evaluators for TacoAgent."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langsmith import Client, traceable

from tacoagent.workflow import stream_session_events


DATASET_NAME = "TacoAgent workflow benchmark v1"
DATASET_DESCRIPTION = (
    "Regression cases for TacoAgent intent routing, workflow completion, "
    "event streaming, and workspace artifact creation."
)

DATASET_EXAMPLES: list[dict[str, Any]] = [
    {
        "inputs": {
            "task": "你好，请用一句话介绍你自己。",
            "workflow_mode": "single-agent",
            "max_attempts": 2,
        },
        "outputs": {
            "expected_route": "chat",
            "required_event_types": ["intent_route", "final_answer"],
            "expected_files": [],
        },
        "metadata": {"category": "intent-routing", "difficulty": "easy"},
    },
    {
        "inputs": {
            "task": "创建 hello.txt，内容为 Hello TacoAgent，并确认文件内容正确。",
            "workflow_mode": "single-agent",
            "max_attempts": 2,
        },
        "outputs": {
            "expected_route": "workflow",
            "required_event_types": [
                "intent_route",
                "plan_snapshot",
                "verifier_result",
                "final_answer",
            ],
            "expected_files": ["hello.txt"],
        },
        "metadata": {"category": "file-tool", "difficulty": "easy"},
    },
    {
        "inputs": {
            "task": (
                "用TDD实现一个Python函数 add(a, b)：先写测试，再写实现，"
                "最后运行测试。文件名使用 test_calculator.py 和 calculator.py。"
            ),
            "workflow_mode": "single-agent",
            "max_attempts": 3,
        },
        "outputs": {
            "expected_route": "workflow",
            "required_event_types": [
                "intent_route",
                "plan_snapshot",
                "tool_call",
                "verifier_result",
                "final_answer",
            ],
            "expected_files": ["test_calculator.py", "calculator.py"],
        },
        "metadata": {"category": "coding-tdd", "difficulty": "medium"},
    },
]


def configured_client() -> Client:
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("missing LANGSMITH_API_KEY")
    return Client()


def register_dataset(
    client: Client, *, dataset_name: str = DATASET_NAME
) -> tuple[Any, bool]:
    """Create the benchmark once; return ``(dataset, created)``."""
    existing = list(client.list_datasets(dataset_name=dataset_name, limit=1))
    if existing:
        return existing[0], False
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description=DATASET_DESCRIPTION,
    )
    client.create_examples(dataset_id=dataset.id, examples=DATASET_EXAMPLES)
    return dataset, True


def _workspace_files(workspace: Path) -> list[str]:
    return sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() and ".tacoagent" not in path.parts
    )


@traceable(name="tacoagent_workflow_evaluation_target")
def tacoagent_target(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run one isolated real workflow and return observable outcomes."""
    mode = os.getenv("TACO_LANGSMITH_WORKFLOW_MODE") or str(
        inputs.get("workflow_mode", "single-agent")
    )
    directory = tempfile.mkdtemp(prefix="tacoagent-langsmith-")
    try:
        events = list(
            stream_session_events(
                str(inputs["task"]),
                session_workspace=directory,
                workflow_mode=mode,
                max_attempts=int(inputs.get("max_attempts", 3)),
            )
        )
        route_event = next(
            (event for event in events if event.get("type") == "intent_route"), {}
        )
        final_event = next(
            (event for event in reversed(events) if event.get("type") == "final_answer"),
            {},
        )
        verifier_event = next(
            (event for event in reversed(events) if event.get("type") == "verifier_result"),
            {},
        )
        final_answer = str(final_event.get("content", ""))
        route = route_event.get("intent_route", "")
        if route == "chat":
            task_passed = bool(final_answer.strip())
        elif verifier_event:
            task_passed = verifier_event.get("passed") is True
        else:
            task_passed = bool(final_answer.strip())
        return {
            "route": route,
            "final_answer": final_answer,
            "task_passed": task_passed,
            "event_types": [str(event.get("type", "")) for event in events],
            "workspace_files": _workspace_files(Path(directory)),
            "workflow_mode": mode,
        }
    finally:
        # Local Mem0/Qdrant may retain its SQLite handle until process teardown
        # on Windows. Evaluation output must not be replaced by a cleanup error.
        shutil.rmtree(directory, ignore_errors=True)


def route_correct(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict[str, Any]:
    expected = str(reference_outputs.get("expected_route", ""))
    actual = str(outputs.get("route", ""))
    return {
        "key": "route_correct",
        "score": int(actual == expected),
        "comment": f"expected={expected!r}, actual={actual!r}",
    }


def final_answer_present(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict[str, Any]:
    answer = str(outputs.get("final_answer", "")).strip()
    return {
        "key": "final_answer_present",
        "score": int(bool(answer)),
        "comment": "Final answer returned." if answer else "Final answer missing.",
    }


def task_success(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict[str, Any]:
    expected_route = str(reference_outputs.get("expected_route", ""))
    actual_route = str(outputs.get("route", ""))
    actual_files = set(outputs.get("workspace_files", []))
    expected_files = set(reference_outputs.get("expected_files", []))
    passed = (
        outputs.get("task_passed") is True
        and actual_route == expected_route
        and expected_files <= actual_files
    )
    return {
        "key": "task_success_strict_v2",
        "score": int(passed),
        "comment": (
            "Expected route completed successfully."
            if passed
            else f"Expected route {expected_route!r}; actual route {actual_route!r} did not pass."
        ),
    }


def required_events_present(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict[str, Any]:
    actual = set(outputs.get("event_types", []))
    expected_route = str(reference_outputs.get("expected_route", ""))
    mode = str(outputs.get("workflow_mode", ""))
    if expected_route == "workflow" and mode == "react":
        required = {"intent_route", "tool_call", "final_answer"}
    else:
        required = set(reference_outputs.get("required_event_types", []))
    missing = sorted(required - actual)
    return {
        "key": "required_events_architecture",
        "score": int(not missing),
        "comment": "All required events observed." if not missing else f"Missing: {missing}",
    }


def expected_artifacts_present(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict[str, Any]:
    actual = set(outputs.get("workspace_files", []))
    expected = set(reference_outputs.get("expected_files", []))
    missing = sorted(expected - actual)
    return {
        "key": "expected_artifacts_present",
        "score": int(not missing),
        "comment": "All expected artifacts exist." if not missing else f"Missing: {missing}",
    }


EVALUATORS = [
    route_correct,
    final_answer_present,
    task_success,
    required_events_present,
    expected_artifacts_present,
]
