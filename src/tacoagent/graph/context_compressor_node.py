from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from tacoagent.core.model import create_model
from tacoagent.graph.context_monitor_node import estimate_message_tokens, message_content_text
from tacoagent.graph.node_utils import json_object, model_usage_event, stream_writer
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import _short_text, _trim_handoffs, build_layered_memory, memory_event
from tacoagent.prompt.context_compression_prompt import CONTEXT_COMPRESSION_PROMPT


COMPACTION_LEDGER = ".tacoagent/memory/compactions.jsonl"


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    item = {
        "id": str(getattr(message, "id", "") or ""),
        "type": type(message).__name__,
        "content": message_content_text(message),
    }
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        item["tool_calls"] = tool_calls
    if isinstance(message, ToolMessage):
        item["tool_call_id"] = message.tool_call_id
        item["name"] = message.name
    return item


def _select_compaction_segments(
    messages: list[BaseMessage], keep_recent_tokens: int
) -> tuple[list[BaseMessage], list[BaseMessage], list[BaseMessage], int, bool]:
    """Split history without leaving a ToolMessage orphaned at the tail boundary."""
    suffix_tokens = [0] * (len(messages) + 1)
    for index in range(len(messages) - 1, -1, -1):
        suffix_tokens[index] = suffix_tokens[index + 1] + estimate_message_tokens(messages[index])
    valid_cuts = [
        index
        for index in range(len(messages) + 1)
        if (index == len(messages) or not isinstance(messages[index], ToolMessage))
        and suffix_tokens[index] <= keep_recent_tokens
    ]
    cut = min(valid_cuts, key=lambda index: keep_recent_tokens - suffix_tokens[index])
    if cut <= 0:
        return [], [], list(messages), 0, False
    split_turn = cut < len(messages) and not isinstance(messages[cut], HumanMessage)
    turn_start = cut
    if split_turn:
        for index in range(cut - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                turn_start = index
                break
    return (
        list(messages[:turn_start]),
        list(messages[turn_start:cut]) if split_turn else [],
        list(messages[cut:]),
        cut,
        split_turn,
    )


def _file_operations(messages: list[BaseMessage]) -> dict[str, list[str]]:
    read_files: list[str] = []
    modified_files: list[str] = []
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            name = str(call.get("name", ""))
            args = call.get("args", {}) or {}
            path = str(args.get("path") or args.get("file_path") or "").strip()
            if not path:
                continue
            target = modified_files if name in {"file_edit", "file_create"} else read_files
            if path not in target:
                target.append(path)
    return {"read_files": read_files, "modified_files": modified_files}


def _append_compaction_entry(runtime: Any, entry: dict[str, Any]) -> None:
    path: Path = runtime.sandbox.resolve(COMPACTION_LEDGER)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def context_compressor_node(state: TacoGraphState) -> dict[str, Any]:
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    memory = build_layered_memory(state, node="context_compressor")
    stream_writer()(memory_event(memory, node="context_compressor"))
    messages = list(state.get("messages", []))
    keep_recent_tokens = max(0, int(state.get("context_keep_recent_tokens", 20_000)))
    messages_to_summarize, turn_prefix, retained_tail, cut_index, split_turn = (
        _select_compaction_segments(messages, keep_recent_tokens)
    )
    compacted_messages = messages[:cut_index]
    previous_summary = str(state.get("context_summary", "") or "")
    payload = {
        "previous_summary": previous_summary,
        "update_mode": bool(previous_summary),
        "messages_to_summarize": [_serialize_message(message) for message in messages_to_summarize],
        "turn_prefix_messages": [_serialize_message(message) for message in turn_prefix],
        "split_turn": split_turn,
        "file_operations": _file_operations(compacted_messages),
        "layered_memory": memory,
    }
    try:
        model = create_model()
        response = model.invoke([
            SystemMessage(content=CONTEXT_COMPRESSION_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
        ])
        stream_writer()(model_usage_event(response, node="context_compressor"))
    except Exception as exc:
        failure = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "node": "context_compressor",
            "reason": "compression_failed",
            "before_tokens": int(state.get("context_token_count", 0)),
            "after_tokens": int(state.get("context_token_count", 0)),
            "error": f"{type(exc).__name__}: {exc}",
        }
        stream_writer()({"type": "context_compression_failed", **failure})
        return {
            "context_should_compress": False,
            "compression_events": list(state.get("compression_events", [])) + [failure],
            "context_summary": previous_summary,
        }
    compressed = json_object(response.content)
    summary = _short_text(compressed.get("summary") or message_content_text(response), 6000)
    summary_message = AIMessage(content=f"[Context compaction]\n{summary}")
    try:
        new_token_count = int(model.get_num_tokens_from_messages([summary_message, *retained_tail]))
    except Exception:
        new_token_count = estimate_message_tokens(summary_message) + sum(
            estimate_message_tokens(message) for message in retained_tail
        )
    runtime.sandbox.resolve("HISTORY_SUMMARY.md").write_text(
        f"# History Summary\n\n{summary}\n", encoding="utf-8"
    )
    handoffs = [
        {
            key: _short_text(value, 800) if isinstance(value, str) else value
            for key, value in handoff.items()
        }
        for handoff in _trim_handoffs(state.get("agent_handoffs", []))
    ]
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
        "cut_index": cut_index,
        "retained_messages": len(retained_tail),
        "split_turn": split_turn,
        "file_operations": _file_operations(compacted_messages),
    }
    _append_compaction_entry(runtime, {
        **event,
        "previous_summary": previous_summary,
        "summary": summary,
        "compacted_messages": [_serialize_message(message) for message in compacted_messages],
    })
    return {
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), summary_message, *retained_tail],
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
    next_node = state.get("context_next_node", "verifier")
    return next_node if next_node in {"actor", "verifier", "final"} else "verifier"
