from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tacoagent.core.react import stream_agent_events
from tacoagent.core.runtime import RuntimeState
from tacoagent.core.trace import trace_now
from tacoagent.core.trace import WorkflowEventRecorder
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event
from tacoagent.graph.state import TacoGraphState
from tacoagent.graph.entry_nodes import chat_responder_node, intent_route_fn, intent_router_node
from tacoagent.graph.patch_status import inspect_workspace_patch
from tacoagent.workflow.single_agent_workflow import _observed_node


def react_node(state: TacoGraphState) -> dict[str, Any]:
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        writer = lambda _event: None
    memory = build_layered_memory(state, node="react")
    writer(memory_event(memory, node="react"))
    started_at, started = trace_now(), perf_counter()
    instruction = state.get("task", "")
    writer({"type": "node_start", "node": "react", "started_at": started_at, "task_goal": instruction, "input_instruction": instruction, "task_status": "running"})
    final_answer, token_usage = "", {}
    try:
        for event in stream_agent_events(
            instruction,
            workspace=runtime.workspace,
            max_loops=int(state.get("react_max_loops", 10)),
            memory_context=format_layered_memory_for_prompt(memory),
        ):
            if event["type"] == "final_answer":
                final_answer = str(event.get("content", ""))
                token_usage = dict(event.get("token_usage", {}))
            else:
                writer(event)
    except Exception as exc:
        writer({"type": "node_output", "node": "react", "started_at": started_at, "ended_at": trace_now(), "duration_ms": round((perf_counter() - started) * 1000, 3), "task_goal": instruction, "input_instruction": instruction, "return_result": {"error": f"{type(exc).__name__}: {exc}"}, "task_status": "failed"})
        raise
    patch_status = inspect_workspace_patch(runtime.workspace)
    output = {
        "final_answer": final_answer,
        "last_actor_summary": final_answer,
        "passed": False,
        "attempts": 1,
        "verification_status": "not_verified",
        "patch_status": patch_status,
        "react_token_usage": token_usage,
    }
    writer({"type": "node_output", "node": "react", "started_at": started_at, "ended_at": trace_now(), "duration_ms": round((perf_counter() - started) * 1000, 3), "task_goal": instruction, "input_instruction": instruction, "return_result": output, "output": output, "task_status": "completed"})
    return output


def build_react_workflow() -> CompiledStateGraph:
    graph = StateGraph(TacoGraphState)
    graph.add_node("intent_router", _observed_node("intent_router", intent_router_node))
    graph.add_node("chat_responder", _observed_node("chat_responder", chat_responder_node))
    graph.add_node("react", react_node)
    graph.add_edge(START, "intent_router")
    graph.add_conditional_edges(
        "intent_router",
        intent_route_fn,
        {"chat": "chat_responder", "workflow": "react"},
    )
    graph.add_edge("chat_responder", END)
    graph.add_edge("react", END)
    return graph.compile()


def stream_react_workflow_events(
    task: str,
    *,
    workspace: str | Path,
    max_loops: int = 10,
    session_context: str = "",
    session_id: str = "",
    session_turn: int = 0,
    force_workflow: bool = False,
) -> Iterator[dict[str, Any]]:
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty")
    if max_loops <= 0:
        raise ValueError("max_loops must be greater than zero")
    runtime = RuntimeState(workspace)
    recorder = WorkflowEventRecorder(runtime, task=task)
    recorder.start("react", task)
    inputs: TacoGraphState = {
        "task": task,
        "runtime": runtime,
        "react_max_loops": max_loops,
        "session_context": session_context,
        "session_id": session_id,
        "session_turn": session_turn,
        "force_workflow": force_workflow,
    }
    for mode, event in build_react_workflow().stream(inputs, stream_mode=["updates", "custom"]):
        if mode == "custom":
            recorder.record("react", event)
            yield event
            continue
        if not isinstance(event, dict):
            continue
        for node, raw_update in event.items():
            update = raw_update if isinstance(raw_update, dict) else {}
            recorder.record_graph_update({node: update})
            if node == "intent_router":
                output = {"type": "intent_route", **update}
            elif node in {"chat_responder", "react"}:
                output = {
                    "type": "final_answer",
                    "content": update.get("final_answer", ""),
                    "token_usage": update.get("react_token_usage", {}),
                }
            else:
                output = {"type": "node_update", "node": node, "update": update}
            recorder.record("react", output)
            if output["type"] == "final_answer":
                recorder.end(status="completed", result=output.get("content", ""))
            yield output
