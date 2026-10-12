from __future__ import annotations

import json
import multiprocessing
from multiprocessing.connection import Connection
from time import monotonic
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import build_todo_write_tool, model_usage_event, stream_writer
from tacoagent.graph.planner_support import execute_plan_call, plan_state_update, prepare_planner
from tacoagent.graph.state import TacoGraphState


SINGLE_AGENT_PLANNER_PROMPT = """You are TacoAgent's planner node.

Create the concrete implementation plan for this task. Call TodoWriteTool exactly once
with plan_summary, todos, acceptance_criteria, and verification_commands.
Plan around observable behavior and the complete call path, including input,
implementation, output, error behavior, and adjacent regression risk. Prefer
the project's native test or check commands discovered from its configuration.
Treat test paths, test names, reproductions, and exact expected values quoted in
the task as primary evidence. Put the narrowest such existing test first in
verification_commands; never guess a similarly named test file. Do not plan edits
to tests, fixtures, snapshots, or test configuration for benchmark bug fixes.
"""


def _provider_timeout(hard_budget_seconds: float) -> float:
    """Leave 40% of the wall-clock budget for startup and clean shutdown."""
    budget = max(1.0, float(hard_budget_seconds))
    return max(1.0, min(budget * 0.6, budget - 0.25))


def _planner_model_worker(
    connection: Connection,
    planner_input: dict[str, Any],
    provider_timeout: float,
) -> None:
    """Run the blocking provider call outside the graph process."""
    try:
        tool = build_todo_write_tool()
        model = create_model(timeout=provider_timeout, max_retries=0).bind_tools([tool])
        response = model.invoke([
            SystemMessage(content=SINGLE_AGENT_PLANNER_PROMPT),
            HumanMessage(content=json.dumps(planner_input, ensure_ascii=False)),
        ])
        connection.send(("ok", response))
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def _run_with_hard_deadline(
    target: Callable[..., None],
    args: tuple[Any, ...],
    timeout_seconds: float,
) -> tuple[str, Any]:
    """Run a pipe-writing target in a terminable spawn process."""
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(child, *args))
    started = monotonic()
    process.start()
    child.close()
    try:
        remaining = max(0.0, float(timeout_seconds) - (monotonic() - started))
        if parent.poll(remaining):
            try:
                return parent.recv()
            except EOFError:
                return "error", "planner subprocess closed without a result"
        return "timeout", f"planner exceeded hard timeout of {timeout_seconds:g} seconds"
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(0.5)
        if process.is_alive():
            process.kill()
            process.join(0.5)


def _fallback_plan(task: str, *, reason: str) -> dict[str, Any]:
    task_hint = " ".join(task.split())[:240]
    return {
        "plan_summary": (
            "Minimal fallback plan: locate the reported behavior, make one focused "
            "production-code edit, and run the smallest relevant verification."
        ),
        "todos": [
            {
                "id": "fallback-diagnose",
                "content": f"Locate the implementation responsible for: {task_hint}",
                "status": "pending",
                "note": reason,
            },
            {
                "id": "fallback-edit",
                "content": "Apply the smallest supported production-code fix.",
                "status": "pending",
                "note": "",
            },
            {
                "id": "fallback-verify",
                "content": "Run one narrow existing check or minimal reproduction.",
                "status": "pending",
                "note": "",
            },
        ],
        "acceptance_criteria": [
            "The reported behavior is corrected by a focused production-code patch.",
            "One smallest relevant regression check is executed when the environment permits.",
        ],
        "verification_commands": [],
    }


def single_agent_planner_node(state: TacoGraphState) -> dict[str, Any]:
    _tool, planner_input = prepare_planner(state, node="single_agent_planner")
    preferred_budget = max(1.0, float(state.get("planner_time_budget_seconds", 30)))
    run_deadline = float(state.get("run_deadline_monotonic", 0) or 0)
    instance_remaining = max(1.0, run_deadline - monotonic()) if run_deadline else preferred_budget
    # Planner's configured budget is a soft allocation used for the single
    # provider request. The subprocess is only terminated at the shared
    # instance deadline, so unused Planner time is automatically available to
    # Actor and Verifier.
    soft_budget = min(preferred_budget, max(1.0, instance_remaining * 0.25))
    provider_timeout = _provider_timeout(soft_budget)
    writer = stream_writer()
    writer({
        "type": "planner_budget_start",
        "node": "single_agent_planner",
        "soft_budget_seconds": round(soft_budget, 3),
        "instance_remaining_seconds": round(instance_remaining, 3),
        "provider_timeout_seconds": provider_timeout,
        "provider_max_retries": 0,
    })
    started = monotonic()
    status, payload = _run_with_hard_deadline(
        _planner_model_worker,
        (planner_input, provider_timeout),
        instance_remaining,
    )
    elapsed = round(monotonic() - started, 3)
    writer({
        "type": "provider_wait",
        "node": "single_agent_planner",
        "seconds": elapsed,
        "status": status,
    })
    if status == "ok":
        response = payload
        writer(model_usage_event(response, node="single_agent_planner"))
        calls = getattr(response, "tool_calls", None) or []
        try:
            return plan_state_update(
                execute_plan_call(build_todo_write_tool(), calls),
                next_node="actor",
            )
        except Exception as exc:
            status, payload = "invalid_plan", f"{type(exc).__name__}: {exc}"

    reason = str(payload)
    writer({
        "type": "planner_fallback",
        "node": "single_agent_planner",
        "reason": status,
        "error": reason,
        "elapsed_seconds": elapsed,
    })
    return plan_state_update(
        _fallback_plan(state.get("task", ""), reason=reason),
        next_node="actor",
    )
