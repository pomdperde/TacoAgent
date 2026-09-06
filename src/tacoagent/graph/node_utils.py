from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tacoagent.graph.state import TacoGraphState, TodoItem
from tacoagent.tools import build_structured_tools


Writer = Callable[[dict[str, Any]], None]


def model_usage_event(response: object, *, node: str) -> dict[str, Any]:
    """Normalize provider-reported model usage for evaluation and tracing."""
    usage = getattr(response, "usage_metadata", None) or {}
    metadata = getattr(response, "response_metadata", None) or {}
    fallback = metadata.get("token_usage", {}) if isinstance(metadata, dict) else {}
    input_tokens = int(usage.get("input_tokens", fallback.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", fallback.get("completion_tokens", 0)) or 0)
    total_tokens = int(usage.get("total_tokens", fallback.get("total_tokens", 0)) or 0)
    return {
        "type": "model_usage",
        "node": node,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or input_tokens + output_tokens,
    }


class TodoPayload(BaseModel):
    id: str
    content: str
    status: str = Field(pattern="^(pending|in_progress|completed|blocked)$")
    note: str = ""


class TodoWriteInput(BaseModel):
    plan_summary: str
    todos: list[TodoPayload]
    acceptance_criteria: list[str]
    verification_commands: list[str]


class TodoUpdateInput(BaseModel):
    id: str
    status: str = Field(pattern="^(pending|in_progress|completed|blocked)$")
    note: str = ""


def stream_writer() -> Writer:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return lambda _event: None


def json_object(content: object) -> dict[str, Any]:
    import json
    text = content if isinstance(content, str) else str(content)
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _runtime_workspace(state: TacoGraphState):
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    return runtime.workspace


def build_tools(state: TacoGraphState) -> list[StructuredTool]:
    return build_structured_tools(
        _runtime_workspace(state),
        task_name=state.get("task", "graph task"),
        approval_mode=state["runtime"].approval_mode,
        approval_handler=state["runtime"].approval_handler,
    )


def build_read_only_tools(state: TacoGraphState) -> list[StructuredTool]:
    allowed = {"list_workspace_files", "file_read", "regex_search", "memory_get"}
    return [tool for tool in build_tools(state) if tool.name in allowed]


def build_todo_write_tool() -> StructuredTool:
    def write_plan(
        plan_summary: str,
        todos: list[TodoPayload],
        acceptance_criteria: list[str],
        verification_commands: list[str],
    ) -> dict[str, Any]:
        return {
            "plan_summary": plan_summary,
            "todos": [
                item.model_dump() if isinstance(item, BaseModel) else dict(item)
                for item in todos
            ],
            "acceptance_criteria": acceptance_criteria,
            "verification_commands": verification_commands,
        }

    return StructuredTool.from_function(
        func=write_plan,
        name="TodoWriteTool",
        description="Publish or revise the structured implementation plan.",
        args_schema=TodoWriteInput,
    )


def build_todo_update_tool(todos: list[TodoItem]) -> StructuredTool:
    def update_todo(id: str, status: str, note: str = "") -> dict[str, Any]:
        for item in todos:
            if item["id"] == id:
                item["status"] = status
                item["note"] = note
                return {"ok": True, "todo": dict(item)}
        return {"ok": False, "error": f"Unknown todo id: {id}"}

    return StructuredTool.from_function(
        func=update_todo,
        name="TodoUpdateTool",
        description="Update one todo's progress status and note.",
        args_schema=TodoUpdateInput,
    )


def invoke_tool(tool: StructuredTool | None, args: dict[str, Any]) -> object:
    if tool is None:
        return {"ok": False, "error": "Unknown tool"}
    try:
        return tool.invoke(args)
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        recovery = getattr(exc, "recovery", None)
        if isinstance(recovery, dict) and recovery:
            result["recovery"] = recovery
        return result
