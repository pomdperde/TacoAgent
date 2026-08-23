from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tacoagent.core.model import create_model
from tacoagent.memory import (
    _short_text,
    _trim_handoffs,
    build_layered_memory,
    format_layered_memory_for_prompt,
    memory_event,
)
from tacoagent.graph.state import TacoGraphState, TodoItem
from tacoagent.prompt.graph_prompt import ACTOR_PROMPT, VERIFIER_PROMPT
from tacoagent.prompt.context_compression_prompt import CONTEXT_COMPRESSION_PROMPT
from tacoagent.tools import build_structured_tools


Writer = Callable[[dict[str, Any]], None]


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


def _writer() -> Writer:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return lambda _event: None


def _json_object(content: object) -> dict[str, Any]:
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
    allowed = {"list_workspace_files", "file_read", "regex_search"}
    return [tool for tool in build_tools(state) if tool.name in allowed]


def _todo_write_tool() -> StructuredTool:
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


def _invoke_tool(tool: StructuredTool | None, args: dict[str, Any]) -> object:
    if tool is None:
        return {"ok": False, "error": "Unknown tool"}
    try:
        return tool.invoke(args)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def actor_node(state: TacoGraphState) -> dict[str, Any]:
    writer = _writer()
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
            result = _invoke_tool(by_name.get(name), args)
            message = ToolMessage(
                content=json.dumps(result, ensure_ascii=False, default=str),
                tool_call_id=str(call.get("id", f"call-{name}")),
                name=name,
            )
            messages.append(message)
            emitted.append(message)
            writer({"type": "tool_result", "name": name, "result": result})
    return {"messages": emitted, "todos": todos, "last_actor_summary": summary}


def _run_verification_commands(state: TacoGraphState) -> list[dict[str, Any]]:
    command_tool = next(
        (tool for tool in build_tools(state) if tool.name == "command_run"), None
    )
    results = []
    for command in state.get("verification_commands", []):
        raw = _invoke_tool(command_tool, {"command": command, "timeout_seconds": 60})
        item = raw if isinstance(raw, dict) else {}
        results.append(
            {
                "command": command,
                "ok": bool(item.get("ok", False)),
                "exit_code": item.get("exit_code"),
                "stdout": str(item.get("stdout", "")),
                "stderr": str(item.get("stderr", item.get("error", ""))),
            }
        )
    return results


def verifier_node(state: TacoGraphState) -> dict[str, Any]:
    writer = _writer()
    memory = build_layered_memory(state, node="verifier")
    writer(memory_event(memory, node="verifier"))
    tools = build_read_only_tools(state)
    by_name = {tool.name: tool for tool in tools}
    agent = create_model().bind_tools(tools)
    verifier_input = {
        "task": state.get("task", ""),
        "plan_summary": state.get("plan_summary", ""),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "last_actor_summary": state.get("last_actor_summary", ""),
        "layered_memory": memory,
    }
    messages: list[Any] = [
        SystemMessage(content=VERIFIER_PROMPT),
        HumanMessage(content=json.dumps(verifier_input, ensure_ascii=False)),
    ]
    review: dict[str, Any] = {}
    for _ in range(4):
        response = agent.invoke(messages)
        messages.append(response)
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            review = _json_object(response.content)
            break
        for call in calls:
            name = str(call.get("name", ""))
            result = _invoke_tool(by_name.get(name), call.get("args", {}) or {})
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=str(call.get("id", f"call-{name}")),
                    name=name,
                )
            )

    command_results = _run_verification_commands(state)
    model_passed = review.get("passed") is True
    commands_passed = all(item["ok"] for item in command_results)
    passed = model_passed and commands_passed
    reason = str(review.get("reason", "Verifier returned no valid assessment"))
    if model_passed and not commands_passed:
        reason = "One or more verification commands failed"
    todos = deepcopy(state.get("todos", []))
    if passed:
        for todo in todos:
            if todo.get("status") != "blocked":
                todo["status"] = "completed"
    last_error = "" if passed else str(
        review.get("recommended_next_instruction") or reason
    )
    return {
        "passed": passed,
        "attempts": int(state.get("attempts", 0)) + 1,
        "verification_results": command_results,
        "verification_checks": list(review.get("checks", [])),
        "last_error": last_error,
        "todos": todos,
        "context_next_node": "final" if passed else "planner",
    }


def verifier_route(state: TacoGraphState) -> str:
    if state.get("passed"):
        return "final"
    if int(state.get("attempts", 0)) >= int(state.get("max_attempts", 3)):
        return "final"
    return "planner"


def context_monitor_node(state: TacoGraphState) -> dict[str, Any]:
    """Estimate graph context size and decide whether compression is required."""
    memory = build_layered_memory(state, node="context_monitor")
    _writer()(memory_event(memory, node="context_monitor"))
    memory_text = format_layered_memory_for_prompt(memory)
    memory_payload = HumanMessage(
        content=f"Layered memory snapshot:\n{memory_text}"
    )
    messages = list(state.get("messages", []))
    try:
        model = create_model()
        token_count = int(
            model.get_num_tokens_from_messages(messages + [memory_payload])
        )
    except Exception:
        message_text = "\n".join(
            _message_content_text(message) for message in messages
        )
        text = "\n".join(part for part in (message_text, memory_payload.content) if part)
        token_count = len(text) // 4
    token_limit = int(state.get("context_token_limit", 400_000))
    return {
        "context_token_count": token_count,
        "context_should_compress": token_count > token_limit,
        "context_next_node": state.get("context_next_node", "verifier"),
    }


def _message_content_text(message: object) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def context_monitor_route(state: TacoGraphState) -> str:
    if state.get("passed"):
        return "final"
    if state.get("context_should_compress"):
        return "context_compressor"
    return state.get("context_next_node", "verifier")


def context_compressor_node(state: TacoGraphState) -> dict[str, Any]:
    """Compress graph history, replace messages, and persist a durable summary."""
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    memory = build_layered_memory(state, node="context_compressor")
    _writer()(memory_event(memory, node="context_compressor"))
    transcript = [
        {
            "type": type(message).__name__,
            "content": _message_content_text(message),
        }
        for message in state.get("messages", [])
    ]
    payload = {
        "messages": transcript,
        "layered_memory": memory,
    }
    model = create_model()
    response = model.invoke(
        [
            SystemMessage(content=CONTEXT_COMPRESSION_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ]
    )
    compressed = _json_object(response.content)
    raw_summary = compressed.get("summary") or _message_content_text(response)
    summary = _short_text(raw_summary, 6000)
    summary_message = AIMessage(content=summary)
    try:
        new_token_count = int(model.get_num_tokens_from_messages([summary_message]))
    except Exception:
        new_token_count = len(summary) // 4

    history_path = runtime.sandbox.resolve("HISTORY_SUMMARY.md")
    history_path.write_text(f"# History Summary\n\n{summary}\n", encoding="utf-8")

    handoffs = []
    for handoff in _trim_handoffs(state.get("agent_handoffs", [])):
        handoffs.append(
            {
                key: _short_text(value, 800) if isinstance(value, str) else value
                for key, value in handoff.items()
            }
        )
    verification_results = []
    for result in state.get("verification_results", [])[-6:]:
        item = dict(result)
        item["stdout"] = _short_text(item.get("stdout", ""), 1200)
        item["stderr"] = _short_text(item.get("stderr", ""), 1200)
        verification_results.append(item)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "node": "context_compressor",
        "reason": "context token limit exceeded",
        "before_tokens": int(state.get("context_token_count", 0)),
        "after_tokens": new_token_count,
        "summary": _short_text(summary, 500),
    }
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            summary_message,
        ],
        "context_summary": summary,
        "context_token_count": new_token_count,
        "context_should_compress": False,
        "research_notes": _short_text(state.get("research_notes", ""), 1600),
        "agent_handoffs": handoffs,
        "plan_summary": _short_text(state.get("plan_summary", ""), 1400),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), 1000),
        "verifier_summary": _short_text(state.get("verifier_summary", ""), 1000),
        "last_error": _short_text(state.get("last_error", ""), 1400),
        "verification_results": verification_results,
        "history_summary": summary,
        "compression_events": list(state.get("compression_events", [])) + [event],
    }


def context_compressor_route(state: TacoGraphState) -> str:
    if state.get("passed"):
        return "final"
    return state.get("context_next_node", "verifier")


def final_node(state: TacoGraphState) -> dict[str, str]:
    """Format the terminal workflow state as a user-facing answer."""
    if state.get("runtime") is not None:
        memory = build_layered_memory(state, node="final")
        _writer()(memory_event(memory, node="final"))
    attempts = int(state.get("attempts", 0))
    if state.get("passed"):
        summary = state.get("last_actor_summary", "").strip()
        content = f"Task completed and verified after {attempts} attempt(s)."
        if summary:
            content = f"{content}\n\n{summary}"
    else:
        error = state.get("last_error", "No verifier reason was provided.").strip()
        content = (
            f"Task did not pass verification after {attempts} attempt(s)."
            f"\n\n{error}"
        )
    return {"final_answer": content}
