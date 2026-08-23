from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from tacoagent.core.model import create_model
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event
from tacoagent.prompt.graph_prompt import SEARCH_AGENT_PROMPT
from tacoagent.tools.web_search_tool import WebSearchTool


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def run_search_agent(
    state: TacoGraphState,
    instruction: str,
    *,
    writer: Callable[[dict[str, Any]], None] | None = None,
    max_loops: int = 4,
) -> dict[str, Any]:
    """Run the focused Tavily-only research ReAct agent."""
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("instruction must not be empty")
    if max_loops <= 0:
        raise ValueError("max_loops must be greater than zero")
    emit = writer or (lambda _event: None)
    memory = build_layered_memory(state, node="searchAgent") if state.get("runtime") else None
    if memory is not None:
        emit(memory_event(memory, node="searchAgent"))
    web_search = WebSearchTool()
    agent = create_model().bind_tools([web_search])
    context = {
        "task": state.get("task", ""),
        "instruction": instruction,
        "existing_research_notes": state.get("research_notes", ""),
        "existing_sources": state.get("sources", []),
        "layered_memory": format_layered_memory_for_prompt(memory) if memory else "",
    }
    messages: list[Any] = [
        SystemMessage(content=SEARCH_AGENT_PROMPT),
        HumanMessage(content=json.dumps(context, ensure_ascii=False)),
    ]
    queries: list[str] = []
    answers: list[str] = []
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
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
            if name != web_search.name:
                result: dict[str, Any] = {
                    "ok": False,
                    "error": f"Unknown search tool: {name}",
                }
            else:
                result = web_search.invoke(args)
            query = str(result.get("query") or args.get("query", ""))
            if query:
                queries.append(query)
            answer = str(result.get("answer", ""))
            if answer:
                answers.append(answer)
            for item in result.get("results", []):
                url = str(item.get("url", ""))
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    sources.append({"title": str(item.get("title", "")), "url": url})
            result_event = {
                "type": "search_results",
                "name": name,
                "query": query,
                "result": result,
            }
            emit(result_event)
            tool_events.append(result_event)
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=str(call.get("id", "search-call")),
                    name=name,
                )
            )

    if not summary:
        summary = "\n\n".join(answers)
    return {
        "ok": True,
        "summary": summary,
        "queries": queries,
        "sources": sources,
        "messages": messages,
        "tool_events": tool_events,
    }
