from __future__ import annotations

from tacoagent.graph.node_utils import stream_writer
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, memory_event


def final_node(state: TacoGraphState) -> dict[str, str]:
    if state.get("runtime") is not None:
        memory = build_layered_memory(state, node="final")
        stream_writer()(memory_event(memory, node="final"))
    attempts = int(state.get("attempts", 0))
    if state.get("passed"):
        summary = state.get("last_actor_summary", "").strip()
        content = f"Task completed and verified after {attempts} attempt(s)."
        if summary:
            content = f"{content}\n\n{summary}"
    else:
        error = state.get("last_error", "No verifier reason was provided.").strip()
        content = f"Task did not pass verification after {attempts} attempt(s).\n\n{error}"
    return {"final_answer": content}
