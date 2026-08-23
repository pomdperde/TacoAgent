from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import (
    build_read_only_tools,
    build_tools,
    invoke_tool,
    json_object,
    stream_writer,
)
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, memory_event
from tacoagent.prompt.graph_prompt import VERIFIER_PROMPT


def _run_verification_commands(state: TacoGraphState) -> list[dict[str, Any]]:
    command_tool = next((tool for tool in build_tools(state) if tool.name == "command_run"), None)
    results = []
    for command in state.get("verification_commands", []):
        raw = invoke_tool(command_tool, {"command": command, "timeout_seconds": 60})
        item = raw if isinstance(raw, dict) else {}
        results.append({
            "command": command,
            "ok": bool(item.get("ok", False)),
            "exit_code": item.get("exit_code"),
            "stdout": str(item.get("stdout", "")),
            "stderr": str(item.get("stderr", item.get("error", ""))),
        })
    return results


def verifier_node(state: TacoGraphState) -> dict[str, Any]:
    writer = stream_writer()
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
            review = json_object(response.content)
            break
        for call in calls:
            name = str(call.get("name", ""))
            result = invoke_tool(by_name.get(name), call.get("args", {}) or {})
            messages.append(ToolMessage(
                content=json.dumps(result, ensure_ascii=False, default=str),
                tool_call_id=str(call.get("id", f"call-{name}")),
                name=name,
            ))

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
    last_error = "" if passed else str(review.get("recommended_next_instruction") or reason)
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
