from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tacoagent.agents.code_agent import run_code_agent
from tacoagent.agents.search_agent import run_search_agent
from tacoagent.core.model import create_model
from tacoagent.graph.nodes import _invoke_tool, _todo_write_tool
from tacoagent.graph.state import AgentHandoff, TacoGraphState
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event
from tacoagent.prompt.multi_agent_prompt import PLANNER_PROMPT


Writer = Callable[[dict[str, Any]], None]


class HandoffInput(BaseModel):
    instruction: str = Field(description="Focused instruction for the specialist agent")


def _append_handoff(
    state: TacoGraphState, *, to_agent: str, instruction: str, result: str
) -> None:
    handoffs = list(state.get("agent_handoffs", []))
    handoff: AgentHandoff = {
        "from_agent": "planner",
        "to_agent": to_agent,
        "instruction": instruction,
        "result": result,
    }
    handoffs.append(handoff)
    state["agent_handoffs"] = handoffs


def _call_search_agent_tool(
    state: TacoGraphState, writer: Writer, instruction: str
) -> dict[str, Any]:
    writer(
        {"type": "handoff", "from": "planner", "to": "searchAgent", "instruction": instruction}
    )
    result = run_search_agent(state, instruction, writer=writer)
    summary = str(result.get("summary", ""))
    prior_notes = state.get("research_notes", "").strip()
    state["research_notes"] = "\n\n".join(
        part for part in (prior_notes, summary) if part
    )
    sources = list(state.get("sources", []))
    seen_urls = {item.get("url", "") for item in sources}
    for source in result.get("sources", []):
        if source.get("url") and source["url"] not in seen_urls:
            seen_urls.add(source["url"])
            sources.append(source)
    state["sources"] = sources
    _append_handoff(
        state, to_agent="searchAgent", instruction=instruction, result=summary
    )
    return result


def _call_code_agent_tool(
    state: TacoGraphState, writer: Writer, instruction: str
) -> dict[str, Any]:
    writer(
        {"type": "handoff", "from": "planner", "to": "codeAgent", "instruction": instruction}
    )
    result = run_code_agent(state, instruction, writer=writer)
    summary = str(result.get("summary", ""))
    state["todos"] = result.get("todos", state.get("todos", []))
    state["code_agent_summary"] = summary
    state["last_actor_summary"] = summary
    state["messages"] = list(state.get("messages", [])) + list(result.get("messages", []))
    _append_handoff(
        state, to_agent="codeAgent", instruction=instruction, result=summary
    )
    return result


def _stream_writer() -> Writer:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return lambda _event: None


def planner_node(state: TacoGraphState) -> dict[str, Any]:
    """Supervise planning, research, and implementation through tool handoffs."""
    writer = _stream_writer()
    memory = build_layered_memory(state, node="planner")
    writer(memory_event(memory, node="planner"))
    working: TacoGraphState = dict(state)
    working["todos"] = deepcopy(state.get("todos", []))
    working["sources"] = deepcopy(state.get("sources", []))
    working["agent_handoffs"] = deepcopy(state.get("agent_handoffs", []))
    initial_messages = list(state.get("messages", []))
    working["messages"] = list(initial_messages)
    todo_tool = _todo_write_tool()
    plan_written = False

    def call_search_agent(instruction: str) -> dict[str, Any]:
        if not plan_written:
            return {"ok": False, "error": "TodoWriteTool must be called before delegation"}
        return _call_search_agent_tool(working, writer, instruction)

    def call_code_agent(instruction: str) -> dict[str, Any]:
        if not plan_written:
            return {"ok": False, "error": "TodoWriteTool must be called before delegation"}
        return _call_code_agent_tool(working, writer, instruction)

    search_tool = StructuredTool.from_function(
        func=call_search_agent,
        name="CallSearchAgentTool",
        description="Delegate focused web or document research to searchAgent.",
        args_schema=HandoffInput,
    )
    code_tool = StructuredTool.from_function(
        func=call_code_agent,
        name="CallCodeAgentTool",
        description="Delegate workspace implementation to codeAgent.",
        args_schema=HandoffInput,
    )
    tools = [todo_tool, search_tool, code_tool]
    by_name = {tool.name: tool for tool in tools}
    agent = create_model().bind_tools(tools)
    planner_input = {
        "task": state.get("task", ""),
        "current_plan": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "research_notes": state.get("research_notes", ""),
        "sources": state.get("sources", []),
        "last_error": state.get("last_error", ""),
        "layered_memory": format_layered_memory_for_prompt(memory),
    }
    messages: list[Any] = [
        SystemMessage(content=PLANNER_PROMPT),
        HumanMessage(content=json.dumps(planner_input, ensure_ascii=False, default=str)),
    ]
    output_messages: list[Any] = []
    summary = ""

    for _ in range(10):
        response = agent.invoke(messages)
        messages.append(response)
        output_messages.append(response)
        summary = response.content if isinstance(response.content, str) else str(response.content)
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            break
        for call in calls:
            name = str(call.get("name", ""))
            args = call.get("args", {}) or {}
            writer({"type": "tool_call", "name": name, "args": args})
            before_specialist_messages = len(working.get("messages", []))
            result = _invoke_tool(by_name.get(name), args)
            if name == todo_tool.name and isinstance(result, dict) and result.get("todos"):
                plan_written = True
                working["plan_summary"] = str(result.get("plan_summary", ""))
                working["todos"] = result["todos"]
                working["acceptance_criteria"] = list(result.get("acceptance_criteria", []))
                working["verification_commands"] = list(result.get("verification_commands", []))
            added = list(working.get("messages", []))[before_specialist_messages:]
            output_messages.extend(added)
            tool_message = ToolMessage(
                content=json.dumps(result, ensure_ascii=False, default=str),
                tool_call_id=str(call.get("id", f"call-{name}")),
                name=name,
            )
            messages.append(tool_message)
            output_messages.append(tool_message)
            writer({"type": "tool_result", "name": name, "result": result})

    return {
        "plan_summary": working.get("plan_summary", state.get("plan_summary", "")),
        "todos": working.get("todos", []),
        "acceptance_criteria": working.get("acceptance_criteria", state.get("acceptance_criteria", [])),
        "verification_commands": working.get("verification_commands", state.get("verification_commands", [])),
        "research_notes": working.get("research_notes", ""),
        "sources": working.get("sources", []),
        "agent_handoffs": working.get("agent_handoffs", []),
        "code_agent_summary": working.get("code_agent_summary", ""),
        "last_actor_summary": working.get("last_actor_summary", ""),
        "messages": output_messages,
        "planner_summary": summary,
        "context_next_node": "verifier",
    }
