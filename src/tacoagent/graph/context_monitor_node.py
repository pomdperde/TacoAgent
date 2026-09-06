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


def estimate_message_tokens(message: object) -> int:
    """Conservative local estimate used only beyond the latest usage anchor."""
    return max(1, (len(message_content_text(message)) + 3) // 4)


def authoritative_usage_tokens(message: object) -> int:
    """Read a provider-reported context usage value when one is available."""
    usage = getattr(message, "usage_metadata", None) or {}
    total = usage.get("total_tokens") or usage.get("totalTokens")
    if total:
        return int(total)
    parts = (
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("cache_read", 0),
        usage.get("cache_write", 0),
    )
    return sum(int(value or 0) for value in parts)


def anchored_context_tokens(messages: list[object], extra: object) -> int:
    """Use the latest authoritative usage and estimate only its trailing delta."""
    for index in range(len(messages) - 1, -1, -1):
        anchor = authoritative_usage_tokens(messages[index])
        if anchor > 0:
            return anchor + sum(
                estimate_message_tokens(message) for message in messages[index + 1 :]
            ) + estimate_message_tokens(extra)
    return 0


def context_monitor_node(state: TacoGraphState) -> dict[str, Any]:
    memory = build_layered_memory(state, node="context_monitor")
    stream_writer()(memory_event(memory, node="context_monitor"))
    memory_payload = HumanMessage(
        content=f"Layered memory snapshot:\n{format_layered_memory_for_prompt(memory)}"
    )
    messages = list(state.get("messages", []))
    token_count = anchored_context_tokens(messages, memory_payload)
    count_source = "provider_usage_anchor"
    if not token_count:
        try:
            token_count = int(create_model().get_num_tokens_from_messages(messages + [memory_payload]))
            count_source = "model_tokenizer"
        except Exception:
            token_count = sum(estimate_message_tokens(message) for message in messages)
            token_count += estimate_message_tokens(memory_payload)
            count_source = "heuristic"
    token_limit = int(state.get("context_token_limit", 400_000))
    reserve_tokens = max(0, int(state.get("context_reserve_tokens", min(16_384, token_limit // 5))))
    compression_threshold = max(1, token_limit - reserve_tokens)
    return {
        "context_token_count": token_count,
        "context_token_count_source": count_source,
        "context_compression_threshold": compression_threshold,
        "context_should_compress": token_count > compression_threshold,
        "context_next_node": state.get("context_next_node", "verifier"),
    }


def context_monitor_route(state: TacoGraphState) -> str:
    if state.get("passed"):
        return "final"
    if state.get("context_should_compress"):
        return "context_compressor"
    next_node = state.get("context_next_node", "verifier")
    return next_node if next_node in {"actor", "verifier", "final"} else "verifier"
