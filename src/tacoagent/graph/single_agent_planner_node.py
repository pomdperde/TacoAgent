from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from tacoagent.core.model import create_model
from tacoagent.graph.nodes import _invoke_tool, _todo_write_tool
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event


SINGLE_AGENT_PLANNER_PROMPT = """You are TacoAgent's planner node.

Create or revise a concrete implementation plan. If verification previously
failed, use last_error to focus the revision. Call TodoWriteTool exactly once
with plan_summary, todos, acceptance_criteria, and verification_commands.
"""


def single_agent_planner_node(state: TacoGraphState) -> dict[str, Any]:
    memory = build_layered_memory(state, node="planner")
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(memory_event(memory, node="planner"))
    except Exception:
        pass
    tool = _todo_write_tool()
    model = create_model().bind_tools([tool])
    planner_input = {
        "task": state.get("task", ""),
        "current_plan": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "last_error": state.get("last_error", ""),
        "layered_memory": format_layered_memory_for_prompt(memory),
    }
    response = model.invoke(
        [
            SystemMessage(content=SINGLE_AGENT_PLANNER_PROMPT),
            HumanMessage(content=json.dumps(planner_input, ensure_ascii=False)),
        ]
    )
    calls = getattr(response, "tool_calls", None) or []
    payload = calls[0].get("args", {}) if calls else {}
    result = _invoke_tool(tool, payload) if payload else {}
    if not isinstance(result, dict) or not result.get("todos"):
        raise ValueError("Planner did not return a valid TodoWriteTool plan")
    return {
        "plan_summary": str(result.get("plan_summary", "")),
        "todos": result["todos"],
        "acceptance_criteria": list(result.get("acceptance_criteria", [])),
        "verification_commands": list(result.get("verification_commands", [])),
        "context_next_node": "actor",
    }
