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
    status: str  # "passed" | "failed" | "blocked" | "timed_out" | "not_verified"
    category: str
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
    actor_verification_results: list[VerificationResult]
    verification_docker_image: str
    passed: bool
    attempts: int
    max_attempts: int
    final_answer: str
    last_actor_summary: str
    verification_checks: list[dict[str, object]]
    verification_status: str
    last_error: str
    react_token_usage: dict[str, int]
    react_max_loops: int
    research_notes: str
    sources: list[SourceItem]
    session_context: str
    memory_snapshot: LayeredMemory
    memory_semantic_recall: bool
    memory_profile_mode: str
    agent_handoffs: list[AgentHandoff]
    code_agent_summary: str
    supervisor_summary: str
    verifier_summary: str
    session_id: str
    session_turn: int
    context_summary: str
    context_token_count: int
    context_token_count_source: str
    context_token_limit: int
    context_reserve_tokens: int
    context_keep_recent_tokens: int
    context_compression_threshold: int
    context_should_compress: bool
    context_next_node: str
    compression_events: list[CompressionEvent]
    history_summary: str
    intent_route: str
    intent_reason: str
    intent_confidence: float
    chat_response: str
    force_workflow: bool
    disable_web_search: bool
    patch_status: dict[str, Any]
    actor_time_budget_seconds: int
    actor_phase_time_budgets: dict[str, int]
    actor_phase_token_budgets: dict[str, int]
    actor_phase_usage: dict[str, dict[str, int]]
    actor_investigation_limit: int
    planner_time_budget_seconds: int
    verifier_time_budget_seconds: int
    require_production_patch: bool
    run_deadline_monotonic: float
    verifier_reserve_seconds: int
    actor_finalize_reserve_seconds: int
    actor_convergence_limit: int
    verification_feedback: dict[str, Any]
