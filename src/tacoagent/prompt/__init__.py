"""Central prompt templates for TacoAgent."""

from .graph_prompt import (
    ACTOR_PROMPT,
    CODE_AGENT_PROMPT,
    FINAL_PROMPT,
    SEARCH_AGENT_PROMPT,
    VERIFIER_PROMPT,
)
from .multi_agent_prompt import PLANNER_PROMPT
from .context_compression_prompt import CONTEXT_COMPRESSION_PROMPT

__all__ = [
    "ACTOR_PROMPT",
    "CODE_AGENT_PROMPT",
    "FINAL_PROMPT",
    "PLANNER_PROMPT",
    "SEARCH_AGENT_PROMPT",
    "VERIFIER_PROMPT",
    "CONTEXT_COMPRESSION_PROMPT",
]
