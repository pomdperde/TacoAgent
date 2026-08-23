from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from tacoagent.core.model import create_model
from tacoagent.graph.state import TacoGraphState
from tacoagent.prompt.entry_prompt import CHAT_RESPONDER_PROMPT, INTENT_ROUTER_PROMPT


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def _json_object(content: object) -> dict[str, Any]:
    text = _content_text(content).strip()
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


def _entry_input(state: TacoGraphState) -> str:
    task = str(state.get("task", ""))
    context = str(state.get("session_context", "")).strip()
    return f"Latest user input:\n{task}\n\nSession context:\n{context or '(none)'}"


def intent_router_node(state: TacoGraphState) -> dict[str, Any]:
    """Use the LLM to choose chat or task workflow; ambiguity defaults to chat."""
    try:
        response = create_model().invoke(
            [
                SystemMessage(content=INTENT_ROUTER_PROMPT),
                HumanMessage(content=_entry_input(state)),
            ]
        )
        payload = _json_object(response.content)
        route = str(payload.get("route", "")).strip().lower()
        confidence = float(payload.get("confidence", 0.0))
        if route not in {"chat", "workflow"} or confidence < 0.55:
            return {
                "intent_route": "chat",
                "intent_reason": "Intent was ambiguous; defaulted to chat.",
                "intent_confidence": confidence,
            }
        return {
            "intent_route": route,
            "intent_reason": str(payload.get("reason", "")),
            "intent_confidence": max(0.0, min(confidence, 1.0)),
        }
    except Exception as exc:
        return {
            "intent_route": "chat",
            "intent_reason": f"Intent routing failed; defaulted to chat: {type(exc).__name__}",
            "intent_confidence": 0.0,
        }


def chat_responder_node(state: TacoGraphState) -> dict[str, Any]:
    """Answer conversational input directly without binding or invoking tools."""
    try:
        response = create_model().invoke(
            [
                SystemMessage(content=CHAT_RESPONDER_PROMPT),
                HumanMessage(content=_entry_input(state)),
            ]
        )
        answer = _content_text(response.content).strip()
    except Exception as exc:
        answer = f"暂时无法生成聊天回复（{type(exc).__name__}）。"
    return {"chat_response": answer, "final_answer": answer}


def intent_route_fn(state: TacoGraphState) -> str:
    return "workflow" if state.get("intent_route") == "workflow" else "chat"
