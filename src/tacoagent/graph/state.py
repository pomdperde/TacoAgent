from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from tacoagent.core.runtime import RuntimeState


class TodoItem(TypedDict):
    id: str
    content: str
    status: str  # "pending" | "in_progress" | "completed" | "blocked"
    note: str


class VerificationResult(TypedDict):
    command: str
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str


class SourceItem(TypedDict):
    title: str
    url: str


class AgentHandoff(TypedDict, total=False):
    from_agent: str
    to_agent: str
    instruction: str
    result: str


class CompressionEvent(TypedDict, total=False):
    timestamp: str
    node: str
    reason: str
    before_tokens: int
    after_tokens: int
    summary: str


class RulesLayer(TypedDict):
    scope: str
    storage: str
    rules: list[str]


class LayeredMemory(TypedDict):
    rules: RulesLayer
    working_memory: dict[str, Any]
    history_summary_store: dict[str, Any]


class TacoGraphState(TypedDict, total=False):
    task: str
    runtime: RuntimeState
    messages: Annotated[list[BaseMessage], add_messages]
    plan_summary: str
    todos: list[TodoItem]
    acceptance_criteria: list[str]
    verification_commands: list[str]
    verification_results: list[VerificationResult]
    passed: bool
    attempts: int
    max_attempts: int
    final_answer: str
    last_actor_summary: str
    verification_checks: list[dict[str, object]]
    last_error: str
    react_token_usage: dict[str, int]
    react_max_loops: int
    research_notes: str
    sources: list[SourceItem]
    session_context: str
    memory_snapshot: LayeredMemory
    agent_handoffs: list[AgentHandoff]
    code_agent_summary: str
    supervisor_summary: str
    verifier_summary: str
    session_id: str
    session_turn: int
    context_summary: str
    context_token_count: int
    context_token_limit: int
    context_should_compress: bool
    context_next_node: str
    compression_events: list[CompressionEvent]
    history_summary: str
    intent_route: str
    intent_reason: str
    intent_confidence: float
    chat_response: str
