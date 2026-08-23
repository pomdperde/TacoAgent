from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from tacoagent.core.model import create_model
from tacoagent.graph.nodes import build_todo_update_tool, build_tools
from tacoagent.graph.state import TacoGraphState, TodoItem
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event
from tacoagent.prompt.graph_prompt import CODE_AGENT_PROMPT


def build_layered_memory_snapshot(state: TacoGraphState) -> dict[str, Any]:
    """Compatibility seam for the future layered-memory implementation."""
    return build_layered_memory(state, node="codeAgent")


def _persist_todos(state: TacoGraphState, todos: list[TodoItem]) -> None:
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    lines = ["# TacoAgent TODO", ""]
    markers = {
        "pending": " ",
        "in_progress": "~",
        "completed": "x",
        "blocked": "!",
    }
    for item in todos:
        status = item.get("status", "pending")
        lines.append(
            f"- [{markers.get(status, ' ')}] {item.get('id', '')}: "
            f"{item.get('content', '')} ({status})"
        )
        if item.get("note"):
            lines.append(f"  - Note: {item['note']}")
    runtime.sandbox.resolve("TODO.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def run_code_agent(
    state: TacoGraphState,
    instruction: str,
    *,
    writer: Callable[[dict[str, Any]], None] | None = None,
    max_loops: int = 10,
) -> dict[str, Any]:
    """Run the workspace-scoped implementation specialist ReAct loop."""
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("instruction must not be empty")
    if max_loops <= 0:
        raise ValueError("max_loops must be greater than zero")
    emit = writer or (lambda _event: None)
    todos = deepcopy(state.get("todos", []))
    todo_tool = build_todo_update_tool(todos)
    tools = build_tools(state) + [todo_tool]
    tools_by_name = {tool.name: tool for tool in tools}
    agent = create_model().bind_tools(tools)
    memory = build_layered_memory_snapshot(state)
    emit(memory_event(memory, node="codeAgent"))
    context = {
        "task": state.get("task", ""),
        "instruction": instruction,
        "session_context": state.get("session_context", ""),
        "memory": format_layered_memory_for_prompt(memory),
    }
    messages: list[Any] = [
        SystemMessage(content=CODE_AGENT_PROMPT),
        HumanMessage(content=json.dumps(context, ensure_ascii=False, default=str)),
    ]
    tool_events: list[dict[str, Any]] = []
    summary = ""

    for _ in range(max_loops):
        response = agent.invoke(messages)
        messages.append(response)
        summary = _content_text(response.content)
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            break
        for call in calls:
            name = str(call.get("name", ""))
            args = call.get("args", {}) or {}
            call_event = {"type": "tool_call", "name": name, "args": args}
            emit(call_event)
            tool_events.append(call_event)
            tool = tools_by_name.get(name)
            try:
                result: object = (
                    {"ok": False, "error": f"Unknown tool: {name}"}
                    if tool is None
                    else tool.invoke(args)
                )
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if name == todo_tool.name:
                _persist_todos(state, todos)
            result_event = {"type": "tool_result", "name": name, "result": result}
            emit(result_event)
            tool_events.append(result_event)
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=str(call.get("id", f"call-{name}")),
                    name=name,
                )
            )

    return {
        "ok": True,
        "summary": summary,
        "todos": todos,
        "messages": messages,
        "tool_events": tool_events,
    }
