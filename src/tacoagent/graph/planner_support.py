from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from tacoagent.graph.node_utils import build_todo_write_tool, invoke_tool, stream_writer
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event


def prepare_planner(
    state: TacoGraphState,
    *,
    node: str,
    include_specialist_context: bool = False,
) -> tuple[dict[str, Any], StructuredTool, dict[str, Any]]:
    """Build the memory snapshot, Todo tool, and shared planner input."""
    memory = build_layered_memory(state, node=node)
    stream_writer()(memory_event(memory, node=node))
    planner_input: dict[str, Any] = {
        "task": state.get("task", ""),
        "current_plan": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "last_error": state.get("last_error", ""),
        "layered_memory": format_layered_memory_for_prompt(memory),
    }
    if include_specialist_context:
        planner_input.update(
            research_notes=state.get("research_notes", ""),
            sources=state.get("sources", []),
        )
    return memory, build_todo_write_tool(), planner_input


def execute_plan_call(
    tool: StructuredTool, calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Execute the TodoWriteTool call contained in an LLM response."""
    call = next((item for item in calls if item.get("name") == tool.name), None)
    result = invoke_tool(tool, call.get("args", {}) or {}) if call else {}
    return validate_plan_result(result)


def validate_plan_result(result: object) -> dict[str, Any]:
    if not isinstance(result, dict) or not result.get("todos"):
        raise ValueError("Planner did not return a valid TodoWriteTool plan")
    return result


def apply_plan_result(target: TacoGraphState, result: object) -> dict[str, Any]:
    """Validate a published plan and copy its normalized fields into state."""
    plan = validate_plan_result(result)
    target["plan_summary"] = str(plan.get("plan_summary", ""))
    target["todos"] = list(plan["todos"])
    target["acceptance_criteria"] = list(plan.get("acceptance_criteria", []))
    target["verification_commands"] = list(plan.get("verification_commands", []))
    return plan


def plan_state_update(result: object, *, next_node: str) -> dict[str, Any]:
    plan = validate_plan_result(result)
    return {
        "plan_summary": str(plan.get("plan_summary", "")),
        "todos": list(plan["todos"]),
        "acceptance_criteria": list(plan.get("acceptance_criteria", [])),
        "verification_commands": list(plan.get("verification_commands", [])),
        "context_next_node": next_node,
    }
