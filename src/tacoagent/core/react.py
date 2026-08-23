from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from tacoagent.core.model import create_model
from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.prompt.graph_prompt import ACTOR_PROMPT
from tacoagent.tools import build_structured_tools


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def _token_usage(response: AIMessage) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    if usage:
        return {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
    token_usage = (getattr(response, "response_metadata", {}) or {}).get(
        "token_usage", {}
    )
    return {
        "input_tokens": int(token_usage.get("prompt_tokens", 0) or 0),
        "output_tokens": int(token_usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(token_usage.get("total_tokens", 0) or 0),
    }


def stream_agent_events(
    task: str, *, workspace: str | Path, max_loops: int = 10, memory_context: str = ""
) -> Iterator[dict[str, Any]]:
    """Run the legacy standalone ReAct loop and stream observable events."""
    task = task.strip()
    if not task:
        raise ValueError("Task must not be empty")
    if max_loops <= 0:
        raise ValueError("max_loops must be greater than zero")

    sandbox = WorkspaceSandbox(workspace)
    tools = build_structured_tools(sandbox, task_name=task)
    tools_by_name = {tool.name: tool for tool in tools}
    agent = create_model().bind_tools(tools)
    user_content = task
    if memory_context:
        user_content += f"\n\nLayered memory:\n{memory_context}"
    messages = [SystemMessage(content=ACTOR_PROMPT), HumanMessage(content=user_content)]
    last_ai_content = ""
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    for _ in range(max_loops):
        response = agent.invoke(messages)
        messages.append(response)
        last_ai_content = _content_text(response.content)
        usage = _token_usage(response)
        for key in total_usage:
            total_usage[key] += usage[key]
        yield {
            "type": "ai_message",
            "content": last_ai_content,
            "token_usage": usage,
            "total_token_usage": dict(total_usage),
        }
        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break

        for call in tool_calls:
            name = str(call.get("name", ""))
            args = call.get("args", {}) or {}
            yield {"type": "tool_call", "name": name, "args": args}
            started = perf_counter()
            try:
                tool = tools_by_name.get(name)
                result: object = (
                    {"ok": False, "error": f"Unknown tool: {name}"}
                    if tool is None
                    else tool.invoke(args)
                )
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            duration_ms = round((perf_counter() - started) * 1000, 3)
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=str(call.get("id", f"call-{name}")),
                    name=name,
                )
            )
            yield {
                "type": "tool_result",
                "name": name,
                "result": result,
                "duration_ms": duration_ms,
            }

    yield {
        "type": "final_answer",
        "content": last_ai_content,
        "token_usage": dict(total_usage),
    }

