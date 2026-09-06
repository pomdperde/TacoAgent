from __future__ import annotations

import json
from copy import deepcopy
from time import monotonic
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import build_todo_update_tool, build_tools, invoke_tool, model_usage_event, stream_writer
from tacoagent.graph.patch_status import inspect_workspace_patch
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, memory_event
from tacoagent.prompt.graph_prompt import ACTOR_PROMPT

READ_ONLY_TOOLS = {"file_read", "regex_search", "list_workspace_files", "command_run", "memory_get"}
BROAD_DISCOVERY_TOOLS = {"regex_search", "list_workspace_files"}
EDIT_TOOLS = {"file_edit", "file_create"}
FIRST_EDIT_READ_TOOLS = {"file_read", "regex_search"}
ACTOR_PHASES = ("diagnosis", "first_edit", "minimal_verification", "correction", "final_verification", "end")
DEFAULT_PHASE_TIME_BUDGETS = {"diagnosis": 70, "first_edit": 70, "minimal_verification": 50, "correction": 55, "final_verification": 45}
DEFAULT_PHASE_TOKEN_BUDGETS = {"diagnosis": 20_000, "first_edit": 20_000, "minimal_verification": 10_000, "correction": 14_000, "final_verification": 8_000}
PHASE_TURN_LIMITS = {"diagnosis": 4, "first_edit": 3, "minimal_verification": 2, "correction": 2, "final_verification": 2}
PHASE_INSTRUCTIONS = {
    "diagnosis": "Hard phase: diagnosis. Gather only minimum evidence. Editing tools are disabled.",
    "first_edit": "Hard phase: first edit. Make the smallest supported production edit. Do not test or explore broadly.",
    "minimal_verification": "Hard phase: minimal verification. Run exactly one smallest relevant check. Prefer the first supplied verification_commands entry when it names an existing test. Do not edit.",
    "correction": "Hard phase: one correction. Read the failed command output already in the conversation, preserve the asserted compatibility contract, and make at most one focused production-code correction. The next phase will re-run the focused check.",
    "final_verification": "Hard phase: final verification after correction. Re-run the same focused failing command exactly once. Do not edit. The corrected patch is not verified unless this command passes.",
}


def _response_stop_reason(response: Any) -> str:
    metadata = getattr(response, "response_metadata", None) or {}
    return str(metadata.get("finish_reason") or metadata.get("stop_reason") or getattr(response, "stop_reason", "") or "").lower()


def _tool_calls_are_truncated(response: Any) -> bool:
    return _response_stop_reason(response) in {"length", "max_tokens", "max_output_tokens"}


def _blocked_tool_reason(name: str, *, has_production_patch: bool, read_only_calls: int,
                         investigation_limit: int, evidence_calls: int,
                         convergence_limit: int, finalizing: bool) -> str:
    """Compatibility helper for the superseded soft loop policy."""
    if name not in READ_ONLY_TOOLS:
        return ""
    if finalizing and name in BROAD_DISCOVERY_TOOLS:
        return "Actor is finalizing; broad discovery tools are disabled."
    if not has_production_patch and read_only_calls >= investigation_limit:
        return "Investigation budget is exhausted and no production patch exists. Make the smallest justified edit or report a concrete blocker."
    if has_production_patch and evidence_calls >= convergence_limit:
        return "Post-edit evidence budget is exhausted. Apply one focused correction from existing evidence or finalize the current patch."
    return ""


def _phase_tool_rejection(phase: str, name: str, *, first_edits: int,
                          verification_calls: int, correction_edits: int,
                          first_edit_read_calls: int = 0) -> str:
    if name == "TodoUpdateTool":
        return ""
    if phase == "diagnosis" and name not in READ_ONLY_TOOLS:
        return "Diagnosis phase permits only read-only tools."
    if phase == "first_edit" and name in FIRST_EDIT_READ_TOOLS:
        if first_edit_read_calls >= 1:
            return "The single restricted first-edit read allowance has already been used. Edit now."
        return ""
    if phase == "first_edit" and name not in EDIT_TOOLS:
        return "First-edit phase permits one restricted read, then only file_edit or file_create."
    if phase == "first_edit" and first_edits >= 1:
        return "The first-edit allowance has already been used."
    if phase in {"minimal_verification", "final_verification"}:
        if name != "command_run":
            return "Verification phases permit only command_run."
        allowed_calls = 1 if phase == "minimal_verification" else 2
        if verification_calls >= allowed_calls:
            return "This verification phase permits exactly one command execution."
    if phase == "correction":
        if name not in EDIT_TOOLS:
            return "Correction phase permits only file_edit or file_create."
        if correction_edits >= 1:
            return "The single correction allowance has already been used."
    return "Actor is in the terminal phase; tools are disabled." if phase == "end" else ""


def _phase_budgets(state: TacoGraphState, key: str, defaults: dict[str, int]) -> dict[str, int]:
    configured = state.get(key, {})
    return {phase: max(1, int(configured.get(phase, default))) for phase, default in defaults.items()}


def _allowed_tools_prompt(phase: str, *, first_edit_read_calls: int) -> str:
    if phase == "diagnosis":
        allowed = "file_read, regex_search, list_workspace_files, command_run, memory_get"
    elif phase == "first_edit":
        read = "one file_read/regex_search remains; " if first_edit_read_calls == 0 else ""
        allowed = f"{read}file_edit, file_create"
    elif phase in {"minimal_verification", "final_verification"}:
        allowed = "command_run only"
    else:
        allowed = "file_edit or file_create only"
    return f"Current phase: {phase}. Allowed tools: {allowed}. Follow this list exactly."


def _restrict_first_edit_read(name: str, args: dict[str, Any]) -> dict[str, Any]:
    restricted = dict(args)
    if name == "file_read":
        restricted["max_chars"] = min(int(restricted.get("max_chars", 12_000)), 12_000)
    elif name == "regex_search":
        restricted["max_results"] = min(int(restricted.get("max_results", 40)), 40)
    return restricted


def _should_defer_edit(
    phase: str, name: str, *, deferred_exists: bool, correction_edits: int
) -> bool:
    return (
        phase == "minimal_verification"
        and name in EDIT_TOOLS
        and not deferred_exists
        and correction_edits < 1
    )


def _compact_reprompt_messages(
    payload: dict[str, Any], phase: str, *, first_edit_read_calls: int
) -> list[Any]:
    compact_payload = {
        "task": payload.get("task", ""),
        "plan_summary": payload.get("plan_summary", ""),
        "acceptance_criteria": payload.get("acceptance_criteria", []),
        "verification_commands": payload.get("verification_commands", []),
        "verification_feedback": payload.get("verification_feedback", {}),
    }
    return [
        SystemMessage(content=ACTOR_PROMPT),
        HumanMessage(content=json.dumps(compact_payload, ensure_ascii=False)),
        HumanMessage(content=(
            "Recovery after two blocked calls. "
            + _allowed_tools_prompt(phase, first_edit_read_calls=first_edit_read_calls)
        )),
    ]


def actor_node(state: TacoGraphState) -> dict[str, Any]:
    writer = stream_writer()
    memory = build_layered_memory(state, node="actor")
    writer(memory_event(memory, node="actor"))
    todos = deepcopy(state.get("todos", []))
    tools = build_tools(state) + [build_todo_update_tool(todos)]
    by_name = {tool.name: tool for tool in tools}
    payload = {"task": state.get("task", ""), "plan_summary": state.get("plan_summary", ""),
               "todos": todos, "acceptance_criteria": state.get("acceptance_criteria", []),
               "verification_commands": state.get("verification_commands", []),
               "verification_feedback": state.get("verification_feedback", {}), "layered_memory": memory}
    messages: list[Any] = [SystemMessage(content=ACTOR_PROMPT), HumanMessage(content=json.dumps(payload, ensure_ascii=False))]
    emitted: list[Any] = []
    actor_verification_results: list[dict[str, Any]] = []
    summary = ""
    actor_started = monotonic()
    total_budget = max(5, int(state.get("actor_time_budget_seconds", 300)))
    deadline = float(state.get("run_deadline_monotonic", 0) or 0)
    if deadline:
        total_budget = max(5, min(total_budget, int(deadline - monotonic() - max(0, int(state.get("verifier_reserve_seconds", 120))))))
    time_budgets = _phase_budgets(state, "actor_phase_time_budgets", DEFAULT_PHASE_TIME_BUDGETS)
    token_budgets = _phase_budgets(state, "actor_phase_token_budgets", DEFAULT_PHASE_TOKEN_BUDGETS)
    agent = create_model(timeout=max(5, min(90, total_budget)), max_retries=1).bind_tools(tools)
    first_edits, first_edit_read_calls, verification_calls, correction_edits = 0, 0, 0, 0
    consecutive_policy_blocks = 0
    use_compact_reprompt = False
    deferred_edit: dict[str, Any] | None = None
    verification_passed = False
    terminal_requested = False
    exit_reason = "completed"
    phase_usage: dict[str, dict[str, int]] = {}
    writer({"type": "actor_loop_start", "node": "actor", "time_budget_seconds": total_budget,
            "phase_time_budgets": time_budgets, "phase_token_budgets": token_budgets})

    for phase_index, phase in enumerate(ACTOR_PHASES[:-1]):
        if phase == "correction" and verification_passed:
            writer({"type": "actor_phase_skipped", "node": "actor", "phase": phase, "reason": "verification_passed"})
            continue
        phase_started, phase_tokens, phase_reason = monotonic(), 0, "turn_limit"
        writer({"type": "actor_phase_start", "node": "actor", "phase": phase,
                "time_budget_seconds": time_budgets[phase], "token_budget": token_budgets[phase]})
        messages.append(HumanMessage(content=PHASE_INSTRUCTIONS[phase]))
        deferred_applied = False
        if phase == "correction" and deferred_edit is not None and correction_edits < 1:
            name, args = deferred_edit["name"], deferred_edit["args"]
            writer({"type": "deferred_edit_applied", "node": "actor", "phase": phase,
                    "tool": name, "args": args, "source_phase": deferred_edit["phase"]})
            result = invoke_tool(by_name.get(name), args)
            ok = isinstance(result, dict) and bool(result.get("ok", False))
            correction_edits += int(ok)
            deferred_applied = ok
            messages.append(HumanMessage(content=(
                f"Deferred {name} was applied in correction: "
                f"{json.dumps(result, ensure_ascii=False, default=str)}"
            )))
            writer({"type": "tool_result", "node": "actor", "phase": phase,
                    "name": name, "result": result, "deferred": True})
            writer({"type": "patch_status", "phase": phase,
                    **inspect_workspace_patch(state["runtime"].workspace)})
            deferred_edit = None
        for turn_index in range(PHASE_TURN_LIMITS[phase]):
            if deferred_applied:
                phase_reason = "deferred_edit_succeeded"
                break
            phase_elapsed, actor_elapsed = monotonic() - phase_started, monotonic() - actor_started
            exhausted = "time" if actor_elapsed >= total_budget or phase_elapsed >= time_budgets[phase] else (
                "tokens" if phase_tokens >= token_budgets[phase] else "")
            if exhausted:
                phase_reason = f"{exhausted}_budget"
                writer({"type": "phase_budget_exhausted", "node": "actor", "phase": phase,
                        "budget_type": exhausted, "elapsed_seconds": round(phase_elapsed, 3),
                        "tokens_used": phase_tokens, "time_budget_seconds": time_budgets[phase],
                        "token_budget": token_budgets[phase]})
                break
            writer({"type": "actor_turn_start", "node": "actor", "phase": phase, "turn": turn_index + 1})
            call_prompt = HumanMessage(content=_allowed_tools_prompt(
                phase, first_edit_read_calls=first_edit_read_calls
            ))
            if use_compact_reprompt:
                invoke_messages = _compact_reprompt_messages(
                    payload, phase, first_edit_read_calls=first_edit_read_calls
                )
                messages.append(invoke_messages[-1])
                use_compact_reprompt = False
            else:
                messages.append(call_prompt)
                invoke_messages = messages
            provider_started = monotonic()
            try:
                response = agent.invoke(invoke_messages)
            except Exception as exc:
                writer({"type": "provider_wait", "node": "actor", "phase": phase,
                        "seconds": round(monotonic() - provider_started, 3), "status": "error"})
                summary, exit_reason, phase_reason = f"Model call failed: {type(exc).__name__}: {exc}", "model_error", "model_error"
                writer({"type": "actor_model_error", "node": "actor", "phase": phase, "error": summary})
                break
            provider_wait = round(monotonic() - provider_started, 3)
            writer({"type": "provider_wait", "node": "actor", "phase": phase,
                    "seconds": provider_wait, "status": "ok",
                    "compact_context": len(invoke_messages) == 3})
            messages.append(response)
            usage = model_usage_event(response, node="actor")
            usage["phase"] = phase
            phase_tokens += int(usage["total_tokens"])
            writer(usage)
            emitted.append(response)
            response_summary = response.content if isinstance(response.content, str) else str(response.content)
            if response_summary.strip():
                summary = response_summary
            writer({"type": "ai_message", "node": "actor", "phase": phase, "content": response_summary})
            calls = getattr(response, "tool_calls", None) or []
            if not calls:
                phase_reason = "model_complete"
                terminal_requested = True
                break
            successful_edit = verification_done = False
            retries: list[str] = []
            reprompt = False
            for call in calls:
                name, args = str(call.get("name", "")), call.get("args", {}) or {}
                writer({"type": "tool_call", "node": "actor", "phase": phase, "name": name, "args": args})
                deferred = _should_defer_edit(
                    phase, name, deferred_exists=deferred_edit is not None,
                    correction_edits=correction_edits,
                )
                rejection = "Tool call was not executed because the model response was truncated." if _tool_calls_are_truncated(response) else _phase_tool_rejection(
                    phase, name, first_edits=first_edits, verification_calls=verification_calls,
                    correction_edits=correction_edits,
                    first_edit_read_calls=first_edit_read_calls)
                if rejection:
                    if deferred:
                        deferred_edit = {"name": name, "args": dict(args), "phase": phase}
                        rejection = "Edit saved for the correction phase; run verification now."
                        writer({"type": "deferred_edit_captured", "node": "actor", "phase": phase,
                                "tool": name, "args": args})
                    result = {"ok": False, "error": rejection, "policy_blocked": True,
                              "deferred": deferred}
                    consecutive_policy_blocks += 1
                    writer({"type": "tool_policy_blocked", "node": "actor", "phase": phase,
                            "tool": name, "args": args, "reason": rejection,
                            "blocked_edit": name in EDIT_TOOLS, "deferred": deferred})
                    if consecutive_policy_blocks >= 2:
                        reprompt = True
                else:
                    consecutive_policy_blocks = 0
                    if phase == "first_edit" and name in FIRST_EDIT_READ_TOOLS:
                        args = _restrict_first_edit_read(name, args)
                        first_edit_read_calls += 1
                    if name == "command_run":
                        args = dict(args)
                        remaining = max(1, int(min(time_budgets[phase] - (monotonic() - phase_started), total_budget - (monotonic() - actor_started))))
                        args["timeout_seconds"] = min(int(args.get("timeout_seconds", 60)), remaining, 60)
                    result = invoke_tool(by_name.get(name), args)
                    if phase in {"minimal_verification", "final_verification"} and name == "command_run":
                        verification_calls, verification_done = verification_calls + 1, True
                        verification_passed = isinstance(result, dict) and bool(result.get("ok", False))
                        item = result if isinstance(result, dict) else {}
                        status = "passed" if item.get("ok") else ("timed_out" if item.get("timed_out") else "failed")
                        actor_verification_results.append({
                            "command": str(item.get("command", args.get("command", ""))),
                            "ok": bool(item.get("ok", False)),
                            "status": status,
                            "category": "command_passed" if status == "passed" else (
                                "command_timeout" if status == "timed_out" else "command_failed"
                            ),
                            "exit_code": item.get("exit_code"),
                            "stdout": str(item.get("stdout", "")),
                            "stderr": str(item.get("stderr", item.get("error", ""))),
                        })
                    if phase in {"first_edit", "correction"} and name in EDIT_TOOLS:
                        ok = isinstance(result, dict) and bool(result.get("ok", False))
                        successful_edit = successful_edit or ok
                        first_edits += int(phase == "first_edit" and ok)
                        correction_edits += int(phase == "correction" and ok)
                tool_message = ToolMessage(content=json.dumps(result, ensure_ascii=False, default=str),
                                           tool_call_id=str(call.get("id", f"call-{name}")), name=name)
                messages.append(tool_message)
                emitted.append(tool_message)
                writer({"type": "tool_result", "node": "actor", "phase": phase, "name": name, "result": result})
                if name in EDIT_TOOLS and isinstance(result, dict) and not result.get("ok", False) and not result.get("policy_blocked"):
                    retries.append(f"The attempted {name} failed: {result.get('error', result)}. Target context: {json.dumps(result.get('recovery', {}), ensure_ascii=False)}. Retry once using line mode with expected_text, stable anchors, or a unified patch hunk.")
                    writer({"type": "edit_retry_required", "node": "actor", "phase": phase, "tool": name})
                writer({"type": "patch_status", "phase": phase, **inspect_workspace_patch(state["runtime"].workspace)})
                if reprompt:
                    writer({"type": "policy_reprompt", "node": "actor", "phase": phase,
                            "blocked_count": consecutive_policy_blocks})
                    use_compact_reprompt = True
                    consecutive_policy_blocks = 0
                    break
            writer({"type": "actor_turn_end", "node": "actor", "phase": phase, "turn": turn_index + 1,
                    "tool_calls": len(calls), "stop_reason": _response_stop_reason(response) or "tool_use"})
            if retries:
                messages.append(HumanMessage(content="\n".join(retries)))
            if phase in {"first_edit", "correction"} and successful_edit:
                phase_reason = "edit_succeeded"
                break
            if phase in {"minimal_verification", "final_verification"} and verification_done:
                phase_reason = "verification_passed" if verification_passed else "verification_failed"
                break
        phase_usage[phase] = {"tokens": phase_tokens, "elapsed_ms": int((monotonic() - phase_started) * 1000)}
        next_phase = "end" if phase in {"minimal_verification", "final_verification"} and verification_passed else ACTOR_PHASES[phase_index + 1]
        writer({"type": "actor_phase_end", "node": "actor", "phase": phase, "reason": phase_reason,
                "next_phase": next_phase, **phase_usage[phase]})
        if terminal_requested:
            exit_reason = "natural_completion"
            break
        if exit_reason == "model_error" or monotonic() - actor_started >= total_budget:
            exit_reason = "model_error" if exit_reason == "model_error" else "time_budget"
            break
        if phase in {"minimal_verification", "final_verification"} and verification_passed:
            exit_reason = "verification_passed"
            break
    patch_status = inspect_workspace_patch(state["runtime"].workspace)
    writer({"type": "actor_loop_end", "node": "actor", "phase": "end", "reason": exit_reason,
            "phase_usage": phase_usage, "patch_status": patch_status})
    return {"messages": emitted, "todos": todos, "last_actor_summary": summary,
            "patch_status": patch_status, "actor_phase_usage": phase_usage,
            "actor_verification_results": actor_verification_results}
