from __future__ import annotations

import json
import re
import shlex
import subprocess
import uuid
from copy import deepcopy
from time import monotonic
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from tacoagent.core.model import create_model
from tacoagent.graph.node_utils import (
    build_tools,
    invoke_tool,
    json_object,
    model_usage_event,
    stream_writer,
)
from tacoagent.graph.state import TacoGraphState
from tacoagent.graph.patch_status import inspect_workspace_patch
from tacoagent.graph.verification_feedback import build_verification_feedback
from tacoagent.memory import build_layered_memory, memory_event
from tacoagent.prompt.graph_prompt import VERIFIER_PROMPT


_BLOCKED_PATTERNS = re.compile(
    r"ModuleNotFoundError|No module named|without building the extension|"
    r"broken or not installed|command not found|is not recognized as an internal",
    re.IGNORECASE,
)


def _verification_status(item: dict[str, Any]) -> tuple[str, str]:
    if item.get("timed_out"):
        return "timed_out", "command_timeout"
    if item.get("ok"):
        return "passed", "command_passed"
    detail = f"{item.get('stderr', '')}\n{item.get('stdout', '')}"
    if _BLOCKED_PATTERNS.search(detail):
        return "blocked", "environment_unavailable"
    return "failed", "command_failed"


def _run_verification_commands(
    state: TacoGraphState, *, deadline: float
) -> list[dict[str, Any]]:
    """Reuse Actor evidence and execute only commands it did not already run."""
    actor_results = list(state.get("actor_verification_results", []))
    completed_commands = {
        str(item.get("command", "")).strip()
        for item in actor_results
        if str(item.get("command", "")).strip()
    }
    commands = list(dict.fromkeys(
        str(command).strip()
        for command in state.get("verification_commands", [])
        if str(command).strip() and str(command).strip() not in completed_commands
    ))
    results = actor_results
    remaining = max(0, int(deadline - monotonic()))
    if not commands or remaining <= 0:
        return results
    docker_image = str(state.get("verification_docker_image", "")).strip()
    if docker_image:
        results.append(_run_docker_commands(
            docker_image,
            state["runtime"].workspace,
            commands,
            timeout=max(1, remaining),
        ))
        return results
    command_tool = next((tool for tool in build_tools(state) if tool.name == "command_run"), None)
    for command in commands:
        remaining = max(0, int(deadline - monotonic()))
        if remaining <= 0:
            break
        raw = invoke_tool(
            command_tool,
            {"command": command, "timeout_seconds": min(60, remaining)},
        )
        item = raw if isinstance(raw, dict) else {}
        status, category = _verification_status(item)
        results.append({
            "command": command,
            "ok": bool(item.get("ok", False)),
            "status": status,
            "category": category,
            "exit_code": item.get("exit_code"),
            "stdout": str(item.get("stdout", "")),
            "stderr": str(item.get("stderr", item.get("error", ""))),
        })
    return results


def _run_docker_commands(image: str, workspace: Any, commands: list[str], *, timeout: int) -> dict[str, Any]:
    """Verify the current checkout inside its official SWE-bench image."""
    workspace = workspace.resolve()
    workspace_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", workspace.name)[:48]
    container = f"taco-verifier-{workspace_slug}-{uuid.uuid4().hex[:8]}"
    command_text = " && ".join(f"({command})" for command in commands)
    script = (
        "source /opt/miniconda3/bin/activate && conda activate testbed && "
        "cd /testbed && python -m pip install 'setuptools<70' 'Cython<3' extension-helpers && "
        "python -m pip install -e . --no-build-isolation && "
        f"{command_text}"
    )
    try:
        created = subprocess.run(
            ["docker", "create", "--name", container, "--workdir", "/testbed", image, "sleep", "infinity"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        if created.returncode:
            raise OSError(created.stderr or created.stdout or "docker create failed")
        subprocess.run(["docker", "start", container], check=True, capture_output=True, timeout=30)
        changed = inspect_workspace_patch(workspace).get("changed_files", [])
        for relative in changed:
            source = workspace / relative
            destination = f"/testbed/{relative}"
            if source.is_file():
                parent = str(relative).replace("\\", "/").rsplit("/", 1)[0] if "/" in str(relative).replace("\\", "/") else "."
                subprocess.run(
                    ["docker", "exec", container, "mkdir", "-p", f"/testbed/{parent}"],
                    check=True, capture_output=True, timeout=30,
                )
                subprocess.run(
                    ["docker", "cp", str(source), f"{container}:{destination}"],
                    check=True, capture_output=True, timeout=30,
                )
            else:
                subprocess.run(
                    ["docker", "exec", container, "bash", "-lc", f"rm -f {shlex.quote(destination)}"],
                    check=True, capture_output=True, timeout=30,
                )
        completed = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", script],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
        raw = {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        raw = {
            "ok": False, "exit_code": None,
            "stdout": exc.stdout or "", "stderr": exc.stderr or "",
            "timed_out": True,
        }
    except OSError as exc:
        raw = {"ok": False, "exit_code": None, "stdout": "", "stderr": str(exc), "timed_out": False}
    except subprocess.CalledProcessError as exc:
        raw = {"ok": False, "exit_code": exc.returncode, "stdout": exc.stdout or "", "stderr": exc.stderr or "", "timed_out": False}
    finally:
        try:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
    status, category = _verification_status(raw)
    return {
        "command": command_text,
        "environment": "swebench-docker",
        "docker_image": image,
        "ok": bool(raw["ok"]),
        "status": status,
        "category": category,
        "exit_code": raw["exit_code"],
        "stdout": str(raw["stdout"]),
        "stderr": str(raw["stderr"]),
    }


def verifier_node(state: TacoGraphState) -> dict[str, Any]:
    writer = stream_writer()
    memory = build_layered_memory(state, node="verifier")
    writer(memory_event(memory, node="verifier"))
    configured_budget = max(1, int(state.get("verifier_time_budget_seconds", 120)))
    run_deadline = float(state.get("run_deadline_monotonic", 0) or 0)
    remaining_run = max(1, int(run_deadline - monotonic())) if run_deadline else configured_budget
    # The configured value is a soft scheduling target. Verification may use
    # time returned by earlier nodes; only the shared instance deadline is hard.
    soft_budget = min(configured_budget, remaining_run)
    deadline = run_deadline if run_deadline else monotonic() + remaining_run
    writer({"type": "verifier_budget", "node": "verifier",
            "soft_budget_seconds": soft_budget,
            "remaining_run_seconds": remaining_run})
    command_results = _run_verification_commands(state, deadline=deadline)
    preliminary_feedback = build_verification_feedback(command_results)
    verifier_input = {
        "task": state.get("task", ""),
        "plan_summary": state.get("plan_summary", ""),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "last_actor_summary": state.get("last_actor_summary", ""),
        "verification_results": preliminary_feedback,
        "patch_status": inspect_workspace_patch(state["runtime"].workspace),
        "layered_memory": memory,
    }
    review: dict[str, Any] = {}
    command_statuses = {item["status"] for item in command_results}
    commands_failed = "failed" in command_statuses
    commands_blocked = bool(command_statuses & {"blocked", "timed_out"})
    # Failed or unavailable command evidence is already conclusive. Asking a
    # model to rediscover that conclusion only burns the correction budget.
    should_review = not commands_failed and not commands_blocked
    remaining = max(0, int(deadline - monotonic()))
    if should_review and remaining > 0:
        provider_started = monotonic()
        try:
            response = create_model(
                timeout=max(1, min(60, remaining)), max_retries=0,
            ).invoke([
                SystemMessage(content=VERIFIER_PROMPT),
                HumanMessage(content=json.dumps(verifier_input, ensure_ascii=False)),
            ])
        except Exception as exc:
            writer({"type": "provider_wait", "node": "verifier",
                    "seconds": round(monotonic() - provider_started, 3),
                    "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        else:
            writer({"type": "provider_wait", "node": "verifier",
                    "seconds": round(monotonic() - provider_started, 3), "status": "ok"})
            writer(model_usage_event(response, node="verifier"))
            review = json_object(response.content)
    patch_status = inspect_workspace_patch(state["runtime"].workspace)
    model_passed = review.get("passed") is True
    commands_conclusive = not command_results or "passed" in command_statuses
    patch_required = bool(state.get("require_production_patch", False))
    patch_passed = (not patch_required) or bool(patch_status.get("production_files_changed"))
    review_available = bool(review)
    passed = model_passed and not commands_failed and not commands_blocked and commands_conclusive and patch_passed
    reason = str(review.get("reason", "Verifier returned no valid assessment"))
    if commands_failed:
        reason = "One or more verification commands ran and failed"
    elif commands_blocked:
        reason = "Verification is blocked by the local environment or command timeout"
    if patch_required and not patch_passed:
        reason = "No meaningful implementation change was produced"
    todos = deepcopy(state.get("todos", []))
    if passed:
        for todo in todos:
            if todo.get("status") != "blocked":
                todo["status"] = "completed"
    last_error = "" if passed else str(review.get("recommended_next_instruction") or reason)
    if patch_required and not patch_passed:
        last_error = "Create a focused implementation edit that resolves the issue; the current change is empty or contains only supporting artifacts."
    checks = list(review.get("checks", []))
    feedback = build_verification_feedback(
        command_results,
        checks,
        recommended_instruction=last_error,
    )
    if passed:
        verification_status = "passed"
    elif "timed_out" in command_statuses:
        verification_status = "timed_out"
    elif "blocked" in command_statuses:
        verification_status = "blocked"
    elif commands_failed:
        verification_status = "failed"
    elif not review_available:
        verification_status = "not_verified"
    else:
        verification_status = "failed"
    return {
        "passed": passed,
        "attempts": int(state.get("attempts", 0)) + 1,
        "verification_results": command_results,
        "verification_checks": checks,
        "verifier_summary": reason,
        "verification_status": verification_status,
        "verification_feedback": feedback,
        "last_error": last_error,
        "todos": todos,
        "patch_status": patch_status,
        "context_next_node": "final" if passed else "actor",
    }


def verifier_route(state: TacoGraphState) -> str:
    if state.get("passed"):
        return "final"
    if state.get("verification_status") in {"blocked", "timed_out", "not_verified"}:
        return "final"
    if int(state.get("attempts", 0)) >= int(state.get("max_attempts", 3)):
        return "final"
    return "actor"
