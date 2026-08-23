from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tacoagent.core.state import RuntimeState
from tacoagent.graph.event_log import WorkflowEventRecorder
from tacoagent.workflow.single_agent_workflow import _observed_node
from tacoagent.graph.nodes import (
    context_compressor_node,
    context_compressor_route,
    context_monitor_node,
    context_monitor_route,
    final_node,
    verifier_node,
    verifier_route,
)
from tacoagent.graph.planner_node import planner_node
from tacoagent.graph.state import TacoGraphState
from tacoagent.workflow.entry_nodes import chat_responder_node, intent_route_fn, intent_router_node


def build_multi_agent_workflow() -> CompiledStateGraph:
    graph = StateGraph(TacoGraphState)
    graph.add_node("intent_router", _observed_node("intent_router", intent_router_node))
    graph.add_node("chat_responder", _observed_node("chat_responder", chat_responder_node))
    graph.add_node("planner", _observed_node("planner", planner_node))
    graph.add_node("verifier", _observed_node("verifier", verifier_node))
    graph.add_node("context_monitor", _observed_node("context_monitor", context_monitor_node))
    graph.add_node("context_compressor", _observed_node("context_compressor", context_compressor_node))
    graph.add_node("final", _observed_node("final", final_node))
    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router",
        intent_route_fn,
        {"chat": "chat_responder", "workflow": "planner"},
    )
    graph.add_edge("chat_responder", END)
    graph.add_edge("planner", "context_monitor")
    graph.add_conditional_edges(
        "context_monitor",
        context_monitor_route,
        {
            "context_compressor": "context_compressor",
            "verifier": "verifier",
            "planner": "planner",
            "final": "final",
        },
    )
    graph.add_conditional_edges(
        "context_compressor",
        context_compressor_route,
        {"verifier": "verifier", "planner": "planner", "final": "final"},
    )
    graph.add_conditional_edges(
        "verifier", verifier_route, {"planner": "planner", "final": "final"}
    )
    graph.add_edge("final", END)
    return graph.compile()


def stream_multi_agent_workflow_events(
    task: str,
    *,
    workspace: str | Path,
    max_attempts: int = 3,
    session_context: str = "",
    session_id: str = "",
    session_turn: int = 0,
) -> Iterator[dict[str, Any]]:
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    runtime = RuntimeState(workspace)
    recorder = WorkflowEventRecorder(runtime, task=task)
    recorder.start("multi-agent", task)
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
    }
    for mode, event in build_multi_agent_workflow().stream(
        inputs, stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            recorder.record("multi-agent", event)
            yield event
            continue
        for node, update in event.items():
            payload = update if isinstance(update, dict) else {}
            recorder.record_graph_update({node: payload})
            if node == "intent_router":
                output = {"type": "intent_route", **payload}
            elif node == "chat_responder":
                output = {"type": "final_answer", "content": payload.get("final_answer", "")}
            elif node == "planner":
                output = {"type": "plan_snapshot", **payload}
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
            recorder.record("multi-agent", output)
            if output["type"] == "final_answer":
                recorder.end(status="completed", result=output.get("content", ""))
            yield output


def stream_session_events(
    task: str, *, session_workspace: str | Path | None = None, max_attempts: int = 3
) -> Iterator[dict[str, Any]]:
    """Run one persistent multi-turn session through the multi-agent graph."""
    from tacoagent.workflow.session_events import run_session_event_stream

    return run_session_event_stream(
        task,
        session_workspace=session_workspace,
        stream=stream_multi_agent_workflow_events,
        stream_kwargs={"max_attempts": max_attempts},
    )
