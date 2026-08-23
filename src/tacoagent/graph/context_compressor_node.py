from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from tacoagent.core.model import create_model
from tacoagent.graph.context_monitor_node import message_content_text
from tacoagent.graph.node_utils import json_object, stream_writer
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import _short_text, _trim_handoffs, build_layered_memory, memory_event
from tacoagent.prompt.context_compression_prompt import CONTEXT_COMPRESSION_PROMPT


def context_compressor_node(state: TacoGraphState) -> dict[str, Any]:
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    memory = build_layered_memory(state, node="context_compressor")
    stream_writer()(memory_event(memory, node="context_compressor"))
    payload = {
        "messages": [
            {"type": type(message).__name__, "content": message_content_text(message)}
            for message in state.get("messages", [])
        ],
        "layered_memory": memory,
    }
    model = create_model()
    response = model.invoke([
        SystemMessage(content=CONTEXT_COMPRESSION_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ])
    compressed = json_object(response.content)
    summary = _short_text(compressed.get("summary") or message_content_text(response), 6000)
    summary_message = AIMessage(content=summary)
    try:
        new_token_count = int(model.get_num_tokens_from_messages([summary_message]))
    except Exception:
        new_token_count = len(summary) // 4
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
    }
    return {
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), summary_message],
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
