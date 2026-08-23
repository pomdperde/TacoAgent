"""Shared multi-turn session driver for compiled TacoAgent workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from tacoagent.core.session import (
    append_assistant_turn,
    append_user_turn,
    build_session_context,
    load_or_create_session,
    save_session,
)
from tacoagent.memory import generate_task_summary, save_task_summary


EventStream = Callable[..., Iterator[dict[str, Any]]]


def run_session_event_stream(
    task: str,
    *,
    session_workspace: str | Path | None,
    stream: EventStream,
    stream_kwargs: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Persist one conversation turn around an existing workflow event stream."""
    workspace = Path(session_workspace or "workspace").expanduser().resolve()
    session = load_or_create_session(workspace)
    turn = append_user_turn(session, task)
    save_session(workspace, session)
    session_context = build_session_context(workspace, session)

    route = "workflow"
    final_content = ""
    task_events: list[dict[str, Any]] = []
    yield {
        "type": "session_turn",
        "session_id": session["session_id"],
        "turn": turn,
        "role": "user",
    }
    for event in stream(
        task,
        workspace=workspace,
        session_context=session_context,
        session_id=session["session_id"],
        session_turn=turn,
        **(stream_kwargs or {}),
    ):
        task_events.append(event)
        if event.get("type") == "intent_route":
            candidate = str(event.get("intent_route", ""))
            route = candidate if candidate in {"chat", "workflow"} else "chat"
        elif event.get("type") == "final_answer":
            final_content = str(event.get("content", ""))
        yield event

    append_assistant_turn(
        session,
        turn=turn,
        route=route,
        content=final_content,
        summary=final_content,
    )
    save_session(workspace, session)
    if route == "workflow":
        memory = generate_task_summary(
            task,
            task_events,
            final_answer=final_content,
            route=route,
            session_id=session["session_id"],
            session_turn=turn,
        )
        stored_memory = save_task_summary(workspace, memory)
        yield {
            "type": "task_memory_saved",
            "memory_id": memory["memory_id"],
            "status": memory["status"],
            "path": ".tacoagent/memory/mem0_qdrant",
            "backend": "mem0-local-qdrant",
            "stored": stored_memory.get("mem0_storage", {}).get("stored", False),
            "deduplicated": stored_memory.get("mem0_storage", {}).get("deduplicated", False),
        }
    yield {
        "type": "session_saved",
        "session_id": session["session_id"],
        "turn": turn,
        "route": route,
    }


def stream_session_events(
    task: str,
    *,
    session_workspace: str | Path | None = None,
    workflow_mode: str = "single-agent",
    max_attempts: int = 3,
) -> Iterator[dict[str, Any]]:
    """Dispatch a persistent turn to any of TacoAgent's workflow modes."""
    mode = str(workflow_mode).strip().lower()
    if mode == "multi-agent":
        from tacoagent.workflow.multi_agent_workflow import stream_session_events as selected

        return selected(
            task, session_workspace=session_workspace, max_attempts=max_attempts
        )
    if mode == "react":
        from tacoagent.workflow.react_workflow import stream_session_events as selected

        return selected(task, session_workspace=session_workspace, max_loops=max_attempts)
    if mode not in {"single-agent", "graph"}:
        raise ValueError(f"Unknown workflow mode: {workflow_mode}")
    from tacoagent.workflow.single_agent_workflow import stream_session_events as selected

    return selected(task, session_workspace=session_workspace, max_attempts=max_attempts)
