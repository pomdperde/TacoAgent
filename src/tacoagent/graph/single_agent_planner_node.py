from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from tacoagent.core.model import create_model
from tacoagent.graph.planner_support import execute_plan_call, plan_state_update, prepare_planner
from tacoagent.graph.state import TacoGraphState


SINGLE_AGENT_PLANNER_PROMPT = """You are TacoAgent's planner node.

Create or revise a concrete implementation plan. If verification previously
failed, use last_error to focus the revision. Call TodoWriteTool exactly once
with plan_summary, todos, acceptance_criteria, and verification_commands.
"""


def single_agent_planner_node(state: TacoGraphState) -> dict[str, Any]:
    _memory, tool, planner_input = prepare_planner(
        state, node="single_agent_planner"
    )
    model = create_model().bind_tools([tool])
    response = model.invoke(
        [
            SystemMessage(content=SINGLE_AGENT_PLANNER_PROMPT),
            HumanMessage(content=json.dumps(planner_input, ensure_ascii=False)),
        ]
    )
    calls = getattr(response, "tool_calls", None) or []
    return plan_state_update(execute_plan_call(tool, calls), next_node="actor")
