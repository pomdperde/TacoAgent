from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator
from time import perf_counter
from time import monotonic

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tacoagent.graph.actor_node import actor_node
from tacoagent.graph.context_compressor_node import context_compressor_node, context_compressor_route
from tacoagent.graph.context_monitor_node import context_monitor_node, context_monitor_route
from tacoagent.graph.final_node import final_node
from tacoagent.graph.verifier_node import verifier_node, verifier_route
from tacoagent.graph.single_agent_planner_node import single_agent_planner_node
from tacoagent.graph.state import TacoGraphState
from tacoagent.workflow.graph_failure import (
    WorkflowExecutionError, handle_node_error, workflow_failure_node,
)
from tacoagent.core.trace import WorkflowEventRecorder
from tacoagent.core.runtime import RuntimeState
from tacoagent.core.checkpoint import CheckpointManager, merge_checkpoint_update
from tacoagent.core.trace import trace_now
from tacoagent.graph.entry_nodes import chat_responder_node, intent_route_fn, intent_router_node


def _node_instruction(name: str, state: TacoGraphState) -> str:
    if name in {"single_agent_planner", "multi_agent_supervisor"}:
        return str(state.get("last_error") or state.get("task", ""))
    if name == "actor":
        return str(state.get("plan_summary", ""))
    if name == "verifier":
        return str(
            state.get("last_actor_summary")
            or "; ".join(state.get("verification_commands", []))
        )
    if name == "context_monitor":
        return "Estimate context tokens and select the next graph node."
    if name == "context_compressor":
        return "Compress graph messages and durable task context."
    if name == "final":
        return "Format the terminal task and verification result."
    return str(state.get("task", ""))


def _observed_node(
    name: str, node: Callable[[TacoGraphState], dict[str, Any]]
) -> Callable[[TacoGraphState], dict[str, Any]]:
    """Wrap a graph node with real-time lifecycle output events."""
    def observed(state: TacoGraphState) -> dict[str, Any]:
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        except Exception:
            writer = lambda _event: None
        started_at = trace_now()
        started = perf_counter()
        instruction = _node_instruction(name, state)
        writer(
            {
                "type": "node_start",
                "node": name,
                "started_at": started_at,
                "task_goal": state.get("task", ""),
                "input_instruction": instruction,
                "task_status": "running",
            }
        )
        try:
            output = node(state)
        except Exception as exc:
            writer(
                {
                    "type": "node_output",
                    "node": name,
                    "started_at": started_at,
                    "ended_at": trace_now(),
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "task_goal": state.get("task", ""),
                    "input_instruction": instruction,
                    "return_result": {"error": f"{type(exc).__name__}: {exc}"},
                    "task_status": "failed",
                }
            )
            raise
        writer(
            {
                "type": "node_output",
                "node": name,
                "started_at": started_at,
                "ended_at": trace_now(),
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "task_goal": state.get("task", ""),
                "input_instruction": instruction,
                "return_result": output,
                "output": output,
                "task_status": "completed",
            }
        )
        return output

    return observed


def build_workflow() -> CompiledStateGraph:
    """Build TacoAgent's default single-agent graph workflow."""
    graph = StateGraph(TacoGraphState)
    graph.set_node_defaults(error_handler=handle_node_error)
    graph.add_node("workflow_failure", workflow_failure_node, error_handler=None)
    graph.add_edge("workflow_failure", END)

    graph.add_node("intent_router", _observed_node("intent_router", intent_router_node))
    graph.add_node("chat_responder", _observed_node("chat_responder", chat_responder_node))
    graph.add_node(
        "single_agent_planner",
        _observed_node("single_agent_planner", single_agent_planner_node),
    )
    graph.add_node("actor", _observed_node("actor", actor_node))
    graph.add_node("context_monitor", _observed_node("context_monitor", context_monitor_node))
    graph.add_node("context_compressor", _observed_node("context_compressor", context_compressor_node))
    graph.add_node("verifier", _observed_node("verifier", verifier_node))
    graph.add_node("final", _observed_node("final", final_node))

    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router",
        intent_route_fn,
        {"chat": "chat_responder", "workflow": "single_agent_planner"},
    )
    graph.add_edge("chat_responder", END)
    graph.add_edge("single_agent_planner", "context_monitor")
    graph.add_conditional_edges(
        "context_monitor",
        context_monitor_route,
        {
            "context_compressor": "context_compressor",
            "actor": "actor",
            "verifier": "verifier",
            "final": "final",
        },
    )
    graph.add_conditional_edges(
        "context_compressor",
        context_compressor_route,
        {"actor": "actor", "verifier": "verifier", "final": "final"},
    )
    graph.add_edge("actor", "verifier")
    graph.add_conditional_edges(
        "verifier",
        verifier_route,
        {"actor": "actor", "final": "final"},
    )
    graph.add_edge("final", END)

    return graph.compile()


def stream_workflow_events(
    task: str,
    *,
    workspace: str | Path,
    max_attempts: int = 3,
    session_context: str = "",
    session_id: str = "",
    session_turn: int = 0,
    force_workflow: bool = False,
    require_production_patch: bool = False,
    planner_time_budget_seconds: int = 60,
    actor_time_budget_seconds: int = 300,
    actor_provider_timeout_seconds: float = 60,
    actor_phase_time_budgets: dict[str, int] | None = None,
    actor_phase_token_budgets: dict[str, int] | None = None,
    verifier_time_budget_seconds: int = 120,
    total_time_budget_seconds: int = 600,
    context_token_limit: int = 400_000,
    context_reserve_tokens: int = 16_384,
    context_keep_recent_tokens: int = 20_000,
    memory_profile_mode: str = "node-trimmed",
    verification_docker_image: str = "",
    checkpoint_mode: str = "light",
) -> Iterator[dict[str, Any]]:
    """Run the compiled graph and expose stable events for CLI consumers."""
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    if context_token_limit <= 0:
        raise ValueError("context_token_limit must be greater than zero")
    if not 0 <= context_reserve_tokens < context_token_limit:
        raise ValueError("context_reserve_tokens must be between zero and context_token_limit")
    if context_keep_recent_tokens < 0:
        raise ValueError("context_keep_recent_tokens must not be negative")
    if memory_profile_mode not in {"node-trimmed", "legacy-uniform"}:
        raise ValueError("memory_profile_mode must be node-trimmed or legacy-uniform")

    runtime = RuntimeState(workspace, checkpoint_mode=checkpoint_mode)
    recorder = WorkflowEventRecorder(runtime, task=task)
    checkpoint = CheckpointManager(runtime, task=task)
    recorder.start("single-agent", task)
    inputs: TacoGraphState = {
        "task": task,
        "runtime": runtime,
        "messages": [HumanMessage(content=task)],
        "attempts": 0,
        "max_attempts": max_attempts,
        "passed": False,
        "session_context": session_context,
        "session_id": session_id,
        "session_turn": session_turn,
        "force_workflow": force_workflow,
        # The configured OpenAI-compatible endpoint regularly needs more than
        # 60 seconds for long SWE-bench prompts. This is a request ceiling;
        # the benchmark runner still owns the hard per-instance deadline.
        "planner_time_budget_seconds": planner_time_budget_seconds,
        "actor_time_budget_seconds": actor_time_budget_seconds,
        "actor_provider_timeout_seconds": actor_provider_timeout_seconds,
        "actor_phase_time_budgets": actor_phase_time_budgets or {},
        "actor_phase_token_budgets": actor_phase_token_budgets or {},
        "actor_investigation_limit": 6,
        "actor_convergence_limit": 4,
        "actor_no_progress_turn_limit": 2,
        "actor_finalize_reserve_seconds": max(15, min(60, actor_time_budget_seconds // 10)),
        "verifier_time_budget_seconds": verifier_time_budget_seconds,
        "run_deadline_monotonic": monotonic() + max(1, total_time_budget_seconds),
        "verifier_reserve_seconds": verifier_time_budget_seconds,
        "require_production_patch": require_production_patch,
        "context_token_limit": context_token_limit,
        "context_reserve_tokens": context_reserve_tokens,
        "context_keep_recent_tokens": context_keep_recent_tokens,
        "memory_profile_mode": memory_profile_mode,
        "verification_docker_image": verification_docker_image,
    }
    checkpoint_state: dict[str, Any] = dict(inputs)
    for part in build_workflow().stream(
        inputs, stream_mode=["updates", "custom"], version="v2"
    ):
        mode, event = part["type"], part["data"]
        if mode == "custom":
            recorder.record("graph", event)
            yield event
            continue
        for node, update in event.items():
            payload = update if isinstance(update, dict) else {}
            checkpoint_state = merge_checkpoint_update(checkpoint_state, payload)
            recorder.record_graph_update({node: payload})
            checkpoint_status = (
                "failed" if node == "workflow_failure"
                else "completed" if node in {"chat_responder", "final"}
                else "running"
            )
            checkpoint_event = checkpoint.save(
                checkpoint_state,
                status=checkpoint_status,
                latest_node=node,
                event={"type": "graph_update", "node": node, "update": payload},
                snapshot_workspace=checkpoint_status != "running",
            )
            if checkpoint_event is not None:
                recorder.record("graph", checkpoint_event)
                yield checkpoint_event
            if node == "workflow_failure":
                failure = {"type": "workflow_error", **payload["workflow_error"]}
                recorder.record("graph", failure)
                recorder.end(status="failed", result=payload)
                yield failure
                raise WorkflowExecutionError(payload["final_answer"])
            if node == "intent_router":
                output = {"type": "intent_route", **payload}
            elif node == "chat_responder":
                output = {"type": "final_answer", "content": payload.get("final_answer", "")}
            elif node == "single_agent_planner":
                output = {"type": "plan_snapshot", **payload}
            elif node == "actor":
                output = {
                    "type": "actor_summary",
                    "content": payload.get("last_actor_summary", ""),
                    "todos": payload.get("todos", []),
                }
            elif node == "verifier":
                output = {
                    "type": "verifier_result",
                    "passed": bool(payload.get("passed", False)),
                    "attempts": payload.get("attempts", 0),
                    "checks": payload.get("verification_checks", []),
                    "results": payload.get("verification_results", []),
                    "last_error": payload.get("last_error", ""),
                }
            elif node == "final":
                output = {"type": "final_answer", "content": payload.get("final_answer", "")}
            elif node in {"context_monitor", "context_compressor"}:
                output = {"type": "context_update", "node": node, **payload}
            else:
                output = {"type": "node_update", "node": node, "update": payload}
            recorder.record("graph", output)
            if output["type"] == "final_answer":
                recorder.end(status="completed", result=output.get("content", ""))
            yield output
