from __future__ import annotations

from tacoagent.graph.state import TacoGraphState


def final_node(state: TacoGraphState) -> dict[str, str]:
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
