"""Core runtime components for TacoAgent."""

from .workspace import WorkspaceBoundaryError, WorkspaceSandbox
from .approval import (
    ApprovalDecision,
    ApprovalRequest,
    classify_command_risk,
    normalize_approval_mode,
)
from .checkpoint import (
    CheckpointManager,
    build_recovery_markdown,
    normalize_checkpoint_mode,
    resume_command,
)
from .trace import TraceRecorder, normalize_trace_mode
from .agent import AgentResponse, TacoAgent
from .model import DEFAULT_MODEL, create_model
from .react import ACTOR_PROMPT, stream_agent_events
from .state import RuntimeState
from .session import (
    append_assistant_turn,
    append_user_turn,
    build_session_context,
    load_or_create_session,
    save_session,
)
from tacoagent.memory import generate_task_summary, load_task_summaries, save_task_summary

__all__ = [
    "AgentResponse",
    "ApprovalDecision",
    "ApprovalRequest",
    "CheckpointManager",
    "TraceRecorder",
    "normalize_trace_mode",
    "ACTOR_PROMPT",
    "DEFAULT_MODEL",
    "TacoAgent",
    "RuntimeState",
    "WorkspaceBoundaryError",
    "WorkspaceSandbox",
    "create_model",
    "classify_command_risk",
    "normalize_approval_mode",
    "normalize_checkpoint_mode",
    "resume_command",
    "build_recovery_markdown",
    "stream_agent_events",
    "load_or_create_session",
    "append_user_turn",
    "append_assistant_turn",
    "save_session",
    "build_session_context",
    "generate_task_summary",
    "save_task_summary",
    "load_task_summaries",
]
