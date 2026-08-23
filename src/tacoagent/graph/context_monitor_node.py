from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import stream_writer
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, format_layered_memory_for_prompt, memory_event


def message_content_text(message: object) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def context_monitor_node(state: TacoGraphState) -> dict[str, Any]:
    memory = build_layered_memory(state, node="context_monitor")
    stream_writer()(memory_event(memory, node="context_monitor"))
    memory_payload = HumanMessage(
        content=f"Layered memory snapshot:\n{format_layered_memory_for_prompt(memory)}"
    )
    messages = list(state.get("messages", []))
    try:
        token_count = int(create_model().get_num_tokens_from_messages(messages + [memory_payload]))
    except Exception:
        message_text = "\n".join(message_content_text(message) for message in messages)
        text = "\n".join(part for part in (message_text, memory_payload.content) if part)
        token_count = len(text) // 4
    token_limit = int(state.get("context_token_limit", 400_000))
    return {
        "context_token_count": token_count,
        "context_should_compress": token_count > token_limit,
        "context_next_node": state.get("context_next_node", "verifier"),
    }


def context_monitor_route(state: TacoGraphState) -> str:
    if state.get("passed"):
        return "final"
    if state.get("context_should_compress"):
        return "context_compressor"
    return state.get("context_next_node", "verifier")
