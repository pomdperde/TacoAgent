"""Compiled workflows available to TacoAgent's CLI and API."""

from .single_agent_workflow import (
    build_workflow,
    stream_session_events as stream_single_agent_session_events,
    stream_workflow_events,
)
from .multi_agent_workflow import (
    build_multi_agent_workflow,
    stream_multi_agent_workflow_events,
    stream_session_events as stream_multi_agent_session_events,
)
from .react_workflow import (
    build_react_workflow,
    stream_react_workflow_events,
    stream_session_events as stream_react_session_events,
)
from .entry_nodes import chat_responder_node, intent_route_fn, intent_router_node
from .session_events import stream_session_events

__all__ = [
    "build_workflow",
    "stream_workflow_events",
    "build_multi_agent_workflow",
    "stream_multi_agent_workflow_events",
    "build_react_workflow",
    "stream_react_workflow_events",
    "intent_router_node",
    "chat_responder_node",
    "intent_route_fn",
    "stream_single_agent_session_events",
    "stream_multi_agent_session_events",
    "stream_react_session_events",
    "stream_session_events",
]
