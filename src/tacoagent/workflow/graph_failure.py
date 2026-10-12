"""Terminal recovery for LangGraph 1.2 node failures.

Recovery describes the failure; it never retries or undoes external writes.
"""
from __future__ import annotations

from typing import Literal

from langgraph.errors import NodeError
from langgraph.types import Command

from tacoagent.graph.state import TacoGraphState


class WorkflowExecutionError(RuntimeError):
    """A graph reached its failure terminal (also marks session turns failed)."""


def handle_node_error(
    state: TacoGraphState, error: NodeError
) -> Command[Literal["workflow_failure"]]:
    return Command(
        update={
            "passed": False,
            "workflow_error": {
                "node": error.node,
                "error_type": type(error.error).__name__,
                "message": str(error.error),
            },
            "last_error": f"{error.node}: {type(error.error).__name__}: {error.error}",
        },
        goto="workflow_failure",
    )


def workflow_failure_node(state: TacoGraphState) -> dict:
    return {
        "passed": False,
        "workflow_error": state["workflow_error"],
        "final_answer": f"Workflow stopped after a node failure.\n\n{state['last_error']}",
    }
