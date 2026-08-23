"""Shared multi-turn session workflow driver for TacoAgent."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from tacoagent.core.session import SessionManager
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
    manager = SessionManager(workspace)
    turn = manager.start_turn(task)
    route = "chat"
    final_content = ""
    task_events: list[dict[str, Any]] = []
    yield {
        "type": "session_turn",
        "session_id": turn.session_id,
        "turn": turn.number,
        "role": "user",
    }
    try:
        for event in stream(
            task,
            workspace=workspace,
            session_context=turn.context,
            session_id=turn.session_id,
            session_turn=turn.number,
            **(stream_kwargs or {}),
        ):
            task_events.append(event)
            if event.get("type") == "intent_route":
                candidate = str(event.get("intent_route", ""))
                route = candidate if candidate in {"chat", "workflow"} else "chat"
            elif event.get("type") == "final_answer":
                final_content = str(event.get("content", ""))
            yield event
    except KeyboardInterrupt:
        manager.complete_turn(
            turn,
            route=route,
            content="The turn was interrupted.",
            summary="Interrupted before completion.",
            status="interrupted",
        )
        raise
    except GeneratorExit:
        manager.complete_turn(
            turn,
            route=route,
            content="The event stream was closed before completion.",
            summary="Cancelled before completion.",
            status="cancelled",
        )
        raise
    except Exception as exc:
        manager.complete_turn(
            turn,
            route=route,
            content=f"{type(exc).__name__}: {exc}",
            summary=f"Turn failed: {type(exc).__name__}",
            status="failed",
        )
        raise

    memory = None
    saved_memory_event = None
    if route == "workflow":
        memory = generate_task_summary(
            task,
            task_events,
            final_answer=final_content,
            route=route,
            session_id=turn.session_id,
            session_turn=turn.number,
        )
    manager.complete_turn(
        turn,
        route=route,
        content=final_content,
        summary=str(memory.get("summary", "")) if memory else "",
    )
    if memory is not None:
        stored_memory = save_task_summary(workspace, memory)
        saved_memory_event = {
            "type": "task_memory_saved",
            "memory_id": memory["memory_id"],
            "status": memory["status"],
            "path": ".tacoagent/memory/mem0_qdrant",
            "backend": "mem0-local-qdrant",
            "stored": stored_memory.get("mem0_storage", {}).get("stored", False),
            "deduplicated": stored_memory.get("mem0_storage", {}).get("deduplicated", False),
        }
    if saved_memory_event is not None:
        yield saved_memory_event
    yield {
        "type": "session_saved",
        "session_id": turn.session_id,
        "turn": turn.number,
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
        from tacoagent.workflow.multi_agent_workflow import stream_multi_agent_workflow_events

        selected = stream_multi_agent_workflow_events
        kwargs = {"max_attempts": max_attempts}
    elif mode == "react":
        from tacoagent.workflow.react_workflow import stream_react_workflow_events

        selected = stream_react_workflow_events
        kwargs = {"max_loops": max_attempts}
    elif mode in {"single-agent", "graph"}:
        from tacoagent.workflow.single_agent_workflow import stream_workflow_events

        selected = stream_workflow_events
        kwargs = {"max_attempts": max_attempts}
    else:
        raise ValueError(f"Unknown workflow mode: {workflow_mode}")
    return run_session_event_stream(
        task,
        session_workspace=session_workspace,
        stream=selected,
        stream_kwargs=kwargs,
    )
