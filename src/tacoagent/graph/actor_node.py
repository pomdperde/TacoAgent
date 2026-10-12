from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import build_todo_update_tool, build_tools, invoke_tool, model_usage_event, stream_writer
from tacoagent.graph.patch_status import inspect_workspace_patch
from tacoagent.graph.state import TacoGraphState
from tacoagent.memory import build_layered_memory, memory_event, store_external_memory
from tacoagent.prompt.graph_prompt import ACTOR_PROMPT

READ_ONLY_TOOLS = {"file_read", "regex_search", "list_workspace_files", "command_run", "memory_get"}
BROAD_DISCOVERY_TOOLS = {"regex_search", "list_workspace_files"}
EDIT_TOOLS = {"file_edit", "file_create", "file_delete"}
FIRST_EDIT_READ_TOOLS = {"file_read", "regex_search"}
ACTOR_PHASES = ("diagnosis", "first_edit", "minimal_verification", "correction", "final_verification", "end")
DEFAULT_PHASE_TIME_BUDGETS = {"diagnosis": 60, "first_edit": 150, "minimal_verification": 35, "correction": 90, "final_verification": 35}
DEFAULT_PHASE_TOKEN_BUDGETS = {"diagnosis": 12_000, "first_edit": 16_000, "minimal_verification": 8_000, "correction": 12_000, "final_verification": 6_000}
PHASE_TURN_LIMITS = {"diagnosis": 3, "first_edit": 6, "minimal_verification": 2, "correction": 3, "final_verification": 2}
DIAGNOSIS_TOOL_CALL_LIMIT = 3
NO_PROGRESS_TURN_LIMIT = 2
PROGRESS_EXTENSION_SECONDS = 45
PROMPT_TOOL_RESULT_CHARS = 6_000
EVIDENCE_CAPSULE_CHARS = 8_000
PHASE_INSTRUCTIONS = {
    "diagnosis": "Current phase: diagnosis. Gather only minimum evidence. Editing tools are disabled.",
    "first_edit": "Current phase: first edit. Make the smallest supported production edit. Do not test or explore broadly.",
    "minimal_verification": "Current phase: minimal verification. Run exactly one smallest relevant check. Prefer the first supplied verification_commands entry when it names an existing test. Do not edit.",
    "correction": "Current phase: one correction. Read the failed command output already in the conversation, preserve the asserted compatibility contract, and make at most one focused production-code correction. The next phase will re-run the focused check.",
    "final_verification": "Current phase: final verification after correction. Re-run the same focused failing command exactly once. Do not edit. The corrected patch is not verified unless this command passes.",
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
                          first_edit_read_calls: int = 0,
                          edit_retry_pending: bool = False,
                          edit_recovery_read_calls: int = 0,
                          diagnosis_tool_calls: int = 0) -> str:
    if name == "TodoUpdateTool":
        return ""
    if phase == "diagnosis" and name not in READ_ONLY_TOOLS:
        return "Diagnosis phase permits only read-only tools."
    if phase == "diagnosis" and diagnosis_tool_calls >= DIAGNOSIS_TOOL_CALL_LIMIT:
        return "Diagnosis evidence limit is exhausted. Use the existing evidence and proceed to the smallest edit."
    if phase == "first_edit" and name in FIRST_EDIT_READ_TOOLS:
        if first_edit_read_calls < 2:
            return ""
        if edit_retry_pending and edit_recovery_read_calls < 1:
            return ""
        if edit_retry_pending:
            return "The edit-recovery read allowance has already been used. Retry the edit now."
        if first_edit_read_calls >= 2:
            return "The two restricted first-edit read allowances have already been used. Edit now."
        return ""
    if phase == "first_edit" and name not in EDIT_TOOLS:
        return "First-edit phase permits one restricted read, then only file_edit, file_create or file_delete."
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
            return "Correction phase permits only file_edit, file_create or file_delete."
        if correction_edits >= 1:
            return "The single correction allowance has already been used."
    return "Actor is in the terminal phase; tools are disabled." if phase == "end" else ""


def _phase_budgets(state: TacoGraphState, key: str, defaults: dict[str, int]) -> dict[str, int]:
    configured = state.get(key, {})
    return {phase: max(1, int(configured.get(phase, default))) for phase, default in defaults.items()}


def _actor_phases_for_attempt(*, attempts: int, has_production_patch: bool) -> tuple[str, ...]:
    """Avoid replaying a complete investigation after verifier feedback."""
    if attempts <= 0:
        return ACTOR_PHASES[:-1]
    if has_production_patch:
        return ("correction", "final_verification")
    return ("first_edit", "minimal_verification")


def _provider_call_timeout(
    *, configured: float, phase_remaining: float, actor_remaining: float,
    run_remaining: float | None,
) -> float:
    limits = [float(configured), float(phase_remaining), float(actor_remaining)]
    if run_remaining is not None:
        limits.append(float(run_remaining))
    return max(1.0, min(limits))


def _dynamic_actor_window(
    *, run_remaining: float | None, preferred_budget: float,
    verifier_reserve: float,
) -> tuple[float, float]:
    """Return a soft Actor allocation and the instance-bounded maximum window."""
    if run_remaining is None:
        maximum = max(5.0, float(preferred_budget))
    else:
        maximum = max(1.0, float(run_remaining))
    reserve = min(max(0.0, float(verifier_reserve)), max(0.0, maximum - 1.0))
    soft = min(maximum, max(5.0, min(float(preferred_budget), maximum - reserve)))
    return soft, maximum


def _extend_soft_deadline(
    current_deadline: float, *, maximum_deadline: float, made_progress: bool,
    extension_seconds: float = PROGRESS_EXTENSION_SECONDS,
) -> float:
    """Extend a soft allocation only when the preceding turn made progress."""
    if not made_progress:
        return current_deadline
    return min(maximum_deadline, current_deadline + max(0.0, extension_seconds))


def _verification_succeeded(result: Any, *, has_production_patch: bool) -> bool:
    return (
        isinstance(result, dict)
        and bool(result.get("ok", False))
        and has_production_patch
    )


def _allowed_tools_prompt(
    phase: str, *, first_edit_read_calls: int,
    edit_retry_pending: bool = False, edit_recovery_read_calls: int = 0,
) -> str:
    if phase == "diagnosis":
        allowed = "file_read, regex_search, list_workspace_files, command_run, memory_get"
    elif phase == "first_edit":
        remaining = max(0, 2 - first_edit_read_calls)
        recovery = int(edit_retry_pending and edit_recovery_read_calls < 1)
        read = f"{remaining + recovery} restricted file_read/regex_search call(s) remain; " if remaining + recovery else ""
        allowed = f"{read}file_edit, file_create, file_delete"
    elif phase in {"minimal_verification", "final_verification"}:
        allowed = "command_run only"
    else:
        allowed = "file_edit, file_create or file_delete only"
    return f"Current phase: {phase}. Allowed tools: {allowed}. Follow this list exactly."


def _restrict_first_edit_read(name: str, args: dict[str, Any]) -> dict[str, Any]:
    restricted = dict(args)
    if name == "file_read":
        restricted["max_chars"] = min(int(restricted.get("max_chars", 12_000)), 12_000)
    elif name == "regex_search":
        restricted["max_results"] = min(int(restricted.get("max_results", 40)), 40)
    return restricted


def _restrict_diagnosis_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Bound diagnostic evidence before a tool can create a large transcript."""
    restricted = dict(args)
    if name == "file_read":
        restricted["max_chars"] = min(int(restricted.get("max_chars", 8_000)), 8_000)
    elif name == "regex_search":
        restricted["max_results"] = min(int(restricted.get("max_results", 24)), 24)
    elif name == "command_run":
        restricted["timeout_seconds"] = min(int(restricted.get("timeout_seconds", 30)), 30)
    return restricted


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    head = max(1, limit * 2 // 3)
    tail = max(1, limit - head)
    return f"{text[:head]}\n...[{len(text) - limit} characters omitted]...\n{text[-tail:]}"


def _prompt_tool_result(result: Any, *, workspace: Any, tool_name: str) -> dict[str, Any] | Any:
    """Keep a small evidence capsule in-context and index the full result locally."""
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized) <= PROMPT_TOOL_RESULT_CHARS:
        return result
    memory_id = "tool-" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]
    stored = store_external_memory(workspace, {
        "memory_id": memory_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": f"actor tool output: {tool_name}",
        "status": "tool_output",
        "summary": f"Full {tool_name} result ({len(serialized)} characters)",
        "tool_name": tool_name,
        "result": result,
    })
    preview: dict[str, Any] = {
        "ok": bool(result.get("ok", False)) if isinstance(result, dict) else True,
        "truncated_for_context": True,
        "original_characters": len(serialized),
        "preview": _compact_text(serialized, PROMPT_TOOL_RESULT_CHARS - 700),
    }
    if stored.get("ok"):
        preview.update({"memory_id": memory_id, "detail_tool": "memory_get"})
    return preview


def _phase_context(payload: dict[str, Any], phase: str, evidence: list[str]) -> list[Any]:
    """Start each hard phase from bounded task facts instead of replaying all phases."""
    compact_payload = {
        "task": _compact_text(payload.get("task", ""), 6_000),
        "plan_summary": _compact_text(payload.get("plan_summary", ""), 2_000),
        "acceptance_criteria": payload.get("acceptance_criteria", []),
        "verification_commands": payload.get("verification_commands", []),
        "verification_feedback": payload.get("verification_feedback", {}),
    }
    messages: list[Any] = [
        SystemMessage(content=ACTOR_PROMPT),
        HumanMessage(content=json.dumps(compact_payload, ensure_ascii=False)),
        HumanMessage(content=PHASE_INSTRUCTIONS[phase]),
    ]
    if evidence:
        capsule = _compact_text("\n\n".join(evidence[-2:]), EVIDENCE_CAPSULE_CHARS)
        messages.append(HumanMessage(content=f"Prior-phase evidence capsule:\n{capsule}"))
    return messages


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
    payload: dict[str, Any], phase: str, *, first_edit_read_calls: int,
    edit_retry_pending: bool = False, edit_recovery_read_calls: int = 0,
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
            + _allowed_tools_prompt(
                phase, first_edit_read_calls=first_edit_read_calls,
                edit_retry_pending=edit_retry_pending,
                edit_recovery_read_calls=edit_recovery_read_calls,
            )
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
    messages: list[Any] = []
    evidence_capsules: list[str] = []
    emitted: list[Any] = []
    actor_verification_results: list[dict[str, Any]] = []
    summary = ""
    actor_started = monotonic()
    deadline = float(state.get("run_deadline_monotonic", 0) or 0)
    run_remaining = max(0.0, deadline - actor_started) if deadline else None
    soft_budget, maximum_window = _dynamic_actor_window(
        run_remaining=run_remaining,
        preferred_budget=max(5, int(state.get("actor_time_budget_seconds", 300))),
        verifier_reserve=max(0, int(state.get("verifier_reserve_seconds", 120))),
    )
    soft_deadline = actor_started + soft_budget
    maximum_deadline = deadline if deadline else actor_started + maximum_window
    time_budgets = _phase_budgets(state, "actor_phase_time_budgets", DEFAULT_PHASE_TIME_BUDGETS)
    token_budgets = _phase_budgets(state, "actor_phase_token_budgets", DEFAULT_PHASE_TOKEN_BUDGETS)
    provider_timeout = max(1.0, float(state.get("actor_provider_timeout_seconds", 60)))
    initial_patch_status = inspect_workspace_patch(state["runtime"].workspace)
    active_phases = _actor_phases_for_attempt(
        attempts=int(state.get("attempts", 0)),
        has_production_patch=bool(initial_patch_status.get("production_files_changed", False)),
    )
    first_edits, first_edit_read_calls, verification_calls, correction_edits = 0, 0, 0, 0
    edit_recovery_read_calls = 0
    edit_retry_pending = False
    diagnosis_tool_calls = 0
    consecutive_policy_blocks = 0
    use_compact_reprompt = False
    deferred_edit: dict[str, Any] | None = None
    verification_passed = False
    terminal_requested = False
    exit_reason = "completed"
    no_progress_turns = 0
    progress_extensions = 0
    seen_progress: set[str] = set()
    phase_usage: dict[str, dict[str, int]] = {}
    writer({"type": "actor_loop_start", "node": "actor", "soft_time_budget_seconds": round(soft_budget, 3),
            "instance_remaining_seconds": round(maximum_window, 3),
            "phase_time_budgets": time_budgets, "phase_token_budgets": token_budgets,
            "provider_timeout_seconds": provider_timeout,
            "active_phases": list(active_phases)})

    for phase_index, phase in enumerate(active_phases):
        if phase == "correction" and verification_passed:
            writer({"type": "actor_phase_skipped", "node": "actor", "phase": phase, "reason": "verification_passed"})
            continue
        phase_started, phase_tokens, phase_reason = monotonic(), 0, "turn_limit"
        provider_timeout_retries = 0
        writer({"type": "actor_phase_start", "node": "actor", "phase": phase,
                "soft_time_budget_seconds": time_budgets[phase], "token_budget": token_budgets[phase]})
        messages = _phase_context(payload, phase, evidence_capsules)
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
            now = monotonic()
            phase_elapsed = now - phase_started
            exhausted = "instance_time" if now >= maximum_deadline else (
                "tokens" if phase_tokens >= token_budgets[phase] else "")
            if exhausted:
                phase_reason = f"{exhausted}_budget"
                writer({"type": "phase_budget_exhausted", "node": "actor", "phase": phase,
                        "budget_type": exhausted, "elapsed_seconds": round(phase_elapsed, 3),
                        "tokens_used": phase_tokens, "soft_time_budget_seconds": time_budgets[phase],
                        "token_budget": token_budgets[phase]})
                break
            if now >= soft_deadline and no_progress_turns:
                phase_reason = "soft_budget_without_progress"
                writer({"type": "actor_soft_budget_reached", "node": "actor", "phase": phase,
                        "elapsed_seconds": round(now - actor_started, 3),
                        "no_progress_turns": no_progress_turns})
                break
            if phase_elapsed >= time_budgets[phase] and no_progress_turns:
                phase_reason = "soft_time_budget"
                writer({"type": "phase_soft_budget_reached", "node": "actor", "phase": phase,
                        "elapsed_seconds": round(phase_elapsed, 3),
                        "no_progress_turns": no_progress_turns})
                break
            writer({"type": "actor_turn_start", "node": "actor", "phase": phase, "turn": turn_index + 1})
            call_prompt = HumanMessage(content=_allowed_tools_prompt(
                phase, first_edit_read_calls=first_edit_read_calls,
                edit_retry_pending=edit_retry_pending,
                edit_recovery_read_calls=edit_recovery_read_calls,
            ))
            if use_compact_reprompt:
                invoke_messages = _compact_reprompt_messages(
                    payload, phase, first_edit_read_calls=first_edit_read_calls,
                    edit_retry_pending=edit_retry_pending,
                    edit_recovery_read_calls=edit_recovery_read_calls,
                )
                messages.append(invoke_messages[-1])
                use_compact_reprompt = False
            else:
                messages.append(call_prompt)
                invoke_messages = messages
            run_remaining = max(0.0, deadline - now) if deadline else None
            call_timeout = _provider_call_timeout(
                configured=provider_timeout,
                phase_remaining=max(1.0, maximum_deadline - now),
                actor_remaining=max(1.0, maximum_deadline - now),
                run_remaining=run_remaining,
            )
            agent = create_model(timeout=call_timeout, max_retries=0).bind_tools(tools)
            provider_started = monotonic()
            try:
                response = agent.invoke(invoke_messages)
            except Exception as exc:
                waited = round(monotonic() - provider_started, 3)
                writer({"type": "provider_wait", "node": "actor", "phase": phase,
                        "seconds": waited, "status": "error",
                        "request_timeout_seconds": round(call_timeout, 3)})
                is_timeout = "timeout" in type(exc).__name__.lower() or isinstance(exc, TimeoutError)
                remaining_after_error = min(
                    maximum_deadline - monotonic(),
                    deadline - monotonic() if deadline else maximum_deadline - monotonic(),
                )
                has_patch = bool(inspect_workspace_patch(
                    state["runtime"].workspace
                ).get("production_files_changed", False))
                if is_timeout and not has_patch and provider_timeout_retries < 1 and remaining_after_error > 5:
                    provider_timeout_retries += 1
                    use_compact_reprompt = True
                    writer({"type": "provider_retry", "node": "actor", "phase": phase,
                            "reason": "timeout_without_production_patch",
                            "retry": provider_timeout_retries})
                    continue
                summary, exit_reason, phase_reason = f"Model call failed: {type(exc).__name__}: {exc}", "model_error", "model_error"
                writer({"type": "actor_model_error", "node": "actor", "phase": phase, "error": summary})
                break
            provider_wait = round(monotonic() - provider_started, 3)
            writer({"type": "provider_wait", "node": "actor", "phase": phase,
                    "seconds": provider_wait, "status": "ok",
                    "compact_context": len(invoke_messages) == 3,
                    "request_timeout_seconds": round(call_timeout, 3)})
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
            turn_made_progress = False
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
                    first_edit_read_calls=first_edit_read_calls,
                    edit_retry_pending=edit_retry_pending,
                    edit_recovery_read_calls=edit_recovery_read_calls,
                    diagnosis_tool_calls=diagnosis_tool_calls)
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
                    if phase == "diagnosis" and name in READ_ONLY_TOOLS:
                        args = _restrict_diagnosis_tool(name, args)
                        diagnosis_tool_calls += 1
                    if phase == "first_edit" and name in FIRST_EDIT_READ_TOOLS:
                        args = _restrict_first_edit_read(name, args)
                        if first_edit_read_calls < 2:
                            first_edit_read_calls += 1
                        else:
                            edit_recovery_read_calls += 1
                    if name == "command_run":
                        args = dict(args)
                        remaining = max(1, int(maximum_deadline - monotonic()))
                        args["timeout_seconds"] = min(int(args.get("timeout_seconds", 60)), remaining, 60)
                    result = invoke_tool(by_name.get(name), args)
                    progress_signature = json.dumps(
                        {"phase": phase, "name": name, "args": args, "result": result},
                        ensure_ascii=False, default=str, sort_keys=True,
                    )
                    meaningful_result = not (
                        isinstance(result, dict)
                        and (result.get("policy_blocked") or result.get("ok") is False)
                    )
                    if meaningful_result and progress_signature not in seen_progress:
                        seen_progress.add(progress_signature)
                        turn_made_progress = True
                    if phase in {"minimal_verification", "final_verification"} and name == "command_run":
                        verification_calls, verification_done = verification_calls + 1, True
                        has_production_patch = bool(inspect_workspace_patch(
                            state["runtime"].workspace
                        ).get("production_files_changed", False))
                        verification_passed = _verification_succeeded(
                            result, has_production_patch=has_production_patch
                        )
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
                        turn_made_progress = turn_made_progress or ok or bool(result.get("recovery"))
                        if ok:
                            edit_retry_pending = False
                        first_edits += int(phase == "first_edit" and ok)
                        correction_edits += int(phase == "correction" and ok)
                prompt_result = _prompt_tool_result(
                    result, workspace=state["runtime"].workspace, tool_name=name
                )
                tool_message = ToolMessage(content=json.dumps(prompt_result, ensure_ascii=False, default=str),
                                           tool_call_id=str(call.get("id", f"call-{name}")), name=name)
                messages.append(tool_message)
                emitted.append(tool_message)
                if not (isinstance(prompt_result, dict) and prompt_result.get("policy_blocked")):
                    evidence_capsules.append(
                        _compact_text(f"{phase}/{name}: {json.dumps(prompt_result, ensure_ascii=False, default=str)}", 4_000)
                    )
                writer({"type": "tool_result", "node": "actor", "phase": phase, "name": name, "result": result})
                if name in EDIT_TOOLS and isinstance(result, dict) and not result.get("ok", False) and not result.get("policy_blocked"):
                    edit_retry_pending = True
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
                    "tool_calls": len(calls), "stop_reason": _response_stop_reason(response) or "tool_use",
                    "made_progress": turn_made_progress})
            if turn_made_progress:
                no_progress_turns = 0
                old_deadline = soft_deadline
                soft_deadline = _extend_soft_deadline(
                    soft_deadline, maximum_deadline=maximum_deadline,
                    made_progress=True,
                )
                if soft_deadline > old_deadline:
                    progress_extensions += 1
                    writer({"type": "progress_extension", "node": "actor", "phase": phase,
                            "seconds": round(soft_deadline - old_deadline, 3),
                            "extensions": progress_extensions})
            else:
                no_progress_turns += 1
            if retries:
                messages.append(HumanMessage(content="\n".join(retries)))
            if phase in {"first_edit", "correction"} and successful_edit:
                phase_reason = "edit_succeeded"
                break
            if phase in {"minimal_verification", "final_verification"} and verification_done:
                phase_reason = "verification_passed" if verification_passed else "verification_failed"
                break
            if no_progress_turns >= int(state.get("actor_no_progress_turn_limit", NO_PROGRESS_TURN_LIMIT)):
                phase_reason = "no_progress"
                writer({"type": "no_progress_termination", "node": "actor", "phase": phase,
                        "consecutive_turns": no_progress_turns})
                break
        phase_usage[phase] = {"tokens": phase_tokens, "elapsed_ms": int((monotonic() - phase_started) * 1000)}
        next_phase = "end" if (
            phase in {"minimal_verification", "final_verification"} and verification_passed
        ) or phase_index + 1 >= len(active_phases) else active_phases[phase_index + 1]
        writer({"type": "actor_phase_end", "node": "actor", "phase": phase, "reason": phase_reason,
                "next_phase": next_phase, **phase_usage[phase]})
        if terminal_requested:
            exit_reason = "natural_completion"
            break
        if exit_reason == "model_error" or monotonic() >= maximum_deadline:
            exit_reason = "model_error" if exit_reason == "model_error" else "instance_time_budget"
            break
        if phase_reason == "no_progress" and phase in {"first_edit", "correction"}:
            exit_reason = "no_progress"
            break
        if phase_reason == "soft_budget_without_progress":
            exit_reason = "soft_budget_without_progress"
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
