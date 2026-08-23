from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import (
    build_todo_update_tool,
    build_tools,
    invoke_tool,
    stream_writer,
)
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, memory_event
from tacoagent.prompt.graph_prompt import ACTOR_PROMPT


def actor_node(state: TacoGraphState) -> dict[str, Any]:
    writer = stream_writer()
    memory = build_layered_memory(state, node="actor")
    writer(memory_event(memory, node="actor"))
    todos = deepcopy(state.get("todos", []))
    tools = build_tools(state) + [build_todo_update_tool(todos)]
    by_name = {tool.name: tool for tool in tools}
    agent = create_model().bind_tools(tools)
    actor_input = {
        "task": state.get("task", ""),
        "plan_summary": state.get("plan_summary", ""),
        "todos": todos,
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "layered_memory": memory,
    }
    messages: list[Any] = [
        SystemMessage(content=ACTOR_PROMPT),
        HumanMessage(content=json.dumps(actor_input, ensure_ascii=False)),
    ]
    emitted: list[Any] = []
    summary = ""
    for _ in range(10):
        response = agent.invoke(messages)
        messages.append(response)
        emitted.append(response)
        summary = response.content if isinstance(response.content, str) else str(response.content)
        writer({"type": "ai_message", "node": "actor", "content": summary})
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            break
        for call in calls:
            name = str(call.get("name", ""))
            args = call.get("args", {}) or {}
            writer({"type": "tool_call", "name": name, "args": args})
            result = invoke_tool(by_name.get(name), args)
            message = ToolMessage(
                content=json.dumps(result, ensure_ascii=False, default=str),
                tool_call_id=str(call.get("id", f"call-{name}")),
                name=name,
            )
            messages.append(message)
            emitted.append(message)
            writer({"type": "tool_result", "name": name, "result": result})
    return {"messages": emitted, "todos": todos, "last_actor_summary": summary}
