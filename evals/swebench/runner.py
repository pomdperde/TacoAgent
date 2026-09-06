from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import queue
import subprocess
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from dotenv import load_dotenv

# Tracing clients are initialized while workflow modules are imported, so the
# environment must be populated before those imports happen.
load_dotenv()

from tacoagent.workflow.multi_agent_workflow import stream_multi_agent_workflow_events
from tacoagent.workflow.react_workflow import stream_react_workflow_events
from tacoagent.workflow.single_agent_workflow import stream_workflow_events

from .dataset import DATASET_NAME, load_verified_instances
from .git_ops import changed_test_artifacts, export_model_patch, prepare_repository
from .models import SWEInstance, SmokeResult


EventStream = Callable[..., Iterator[dict[str, Any]]]
STREAMS: dict[str, EventStream] = {
    "single-agent": stream_workflow_events,
    "multi-agent": stream_multi_agent_workflow_events,
    "react": stream_react_workflow_events,
}


def build_task(instance: SWEInstance) -> str:
    return f"""Resolve this GitHub issue in the current repository.

Issue:
{instance.problem_statement}

Requirements:
- Inspect the repository and reproduce the problem when practical.
- Implement a focused fix; do not merely describe one.
- Run relevant existing tests or a temporary reproduction command.
- Do not edit, add, delete, or reformat test files, fixtures, snapshots, or test configuration.
  SWE-bench applies its own hidden regression-test patch after your implementation patch;
  touching tests can make that official patch conflict and invalidate the evaluation.
- Do not search the web for the issue, pull a solution commit, or access benchmark answers.
- Do not modify TacoAgent runtime files as part of the solution.
- Finish with a concise summary of changed files and tests run.
"""


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _completed_ids(ledger_path: Path) -> set[str]:
    if not ledger_path.exists():
        return set()
    completed = set()
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("status") in {"completed", "no_patch", "failed", "timed_out"}:
            completed.add(str(item.get("instance_id", "")))
    return completed


def _event_token_usage(events: list[dict[str, Any]]) -> dict[str, int]:
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for event in events:
        if event.get("type") != "model_usage":
            continue
        for key in usage:
            usage[key] += int(event.get(key, 0) or 0)
    return usage


def _event_diagnostics(events: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = [event for event in events if event.get("type") == "tool_policy_blocked"]
    blocked_edits = [
        {
            "phase": str(event.get("phase", "")),
            "tool": str(event.get("tool", "")),
            "args": event.get("args", {}),
            "reason": str(event.get("reason", "")),
            "deferred": bool(event.get("deferred", False)),
        }
        for event in blocked
        if event.get("blocked_edit") or event.get("tool") in {"file_edit", "file_create"}
    ]
    return {
        "policy_block_count": len(blocked),
        "policy_reprompt_count": sum(event.get("type") == "policy_reprompt" for event in events),
        "blocked_edits": blocked_edits,
        "provider_wait_seconds": round(sum(
            float(event.get("seconds", 0) or 0)
            for event in events
            if event.get("type") == "provider_wait"
        ), 3),
    }


def _safe_changed_test_artifacts(workspace: Path) -> list[str]:
    """Collect optional diagnostics without masking the instance result."""
    try:
        return changed_test_artifacts(workspace)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return []


def run_instance(
    instance: SWEInstance,
    *,
    workspace: Path,
    workflow_mode: str,
    max_attempts: int,
    model_name: str,
    events_path: Path,
    stream: EventStream | None = None,
    planner_time_budget: int = 60,
    actor_time_budget: int = 300,
    verifier_time_budget: int = 120,
    total_time_budget_seconds: int = 600,
    memory_profile: str = "node-trimmed",
) -> tuple[SmokeResult, str]:
    selected = stream or STREAMS[workflow_mode]
    started = perf_counter()
    final_answer = ""
    verifier_passed: bool | None = None
    event_count = tool_calls = 0
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    policy_block_count = policy_reprompt_count = 0
    blocked_edits: list[dict[str, Any]] = []
    provider_wait_seconds = 0.0
    error = ""
    patch = ""
    try:
        prepare_repository(instance, workspace)
        kwargs = {
            "workspace": workspace,
            "force_workflow": True,
        }
        if workflow_mode == "single-agent":
            kwargs["require_production_patch"] = True
            kwargs["planner_time_budget_seconds"] = planner_time_budget
            kwargs["actor_time_budget_seconds"] = actor_time_budget
            kwargs["verifier_time_budget_seconds"] = verifier_time_budget
            kwargs["total_time_budget_seconds"] = total_time_budget_seconds
            kwargs["memory_profile_mode"] = memory_profile
            kwargs["verification_docker_image"] = instance.docker_image
        if workflow_mode == "multi-agent":
            kwargs["disable_web_search"] = True
        if workflow_mode == "react":
            kwargs["max_loops"] = max_attempts
        else:
            kwargs["max_attempts"] = max_attempts
        for event in selected(build_task(instance), **kwargs):
            event_count += 1
            if event.get("type") == "tool_call":
                tool_calls += 1
            elif event.get("type") == "model_usage":
                for key in token_usage:
                    token_usage[key] += int(event.get(key, 0) or 0)
            elif event.get("type") == "final_answer":
                final_answer = str(event.get("content", ""))
            elif event.get("type") == "verifier_result":
                verifier_passed = event.get("passed") is True
            elif event.get("type") == "tool_policy_blocked":
                policy_block_count += 1
                if event.get("blocked_edit") or event.get("tool") in {"file_edit", "file_create"}:
                    blocked_edits.append({
                        "phase": str(event.get("phase", "")),
                        "tool": str(event.get("tool", "")),
                        "args": event.get("args", {}),
                        "reason": str(event.get("reason", "")),
                        "deferred": bool(event.get("deferred", False)),
                    })
            elif event.get("type") == "policy_reprompt":
                policy_reprompt_count += 1
            elif event.get("type") == "provider_wait":
                provider_wait_seconds += float(event.get("seconds", 0) or 0)
            _append_jsonl(events_path, event)
        patch = export_model_patch(workspace, production_only=True)
        status = "completed" if patch.strip() else "no_patch"
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        if (workspace / ".git").exists():
            try:
                patch = export_model_patch(workspace, production_only=True)
            except Exception:
                patch = ""
    result = SmokeResult(
        instance_id=instance.instance_id,
        workflow_mode=workflow_mode,
        model_name=model_name,
        status=status,
        duration_seconds=round(perf_counter() - started, 3),
        patch_chars=len(patch),
        final_answer=final_answer,
        verifier_passed=verifier_passed,
        error=error,
        event_count=event_count,
        tool_calls=tool_calls,
        workspace=str(workspace),
        metadata={
            "memory_profile": memory_profile,
            "token_usage": token_usage,
            "policy_block_count": policy_block_count,
            "policy_reprompt_count": policy_reprompt_count,
            "blocked_edits": blocked_edits,
            "provider_wait_seconds": round(provider_wait_seconds, 3),
            "excluded_test_artifacts": (
                _safe_changed_test_artifacts(workspace)
                if (workspace / ".git").exists()
                else []
            ),
        },
    )
    return result, patch


def _instance_worker(output_queue: Any, kwargs: dict[str, Any]) -> None:
    result, patch = run_instance(**kwargs)
    output_queue.put((result.as_dict(), patch))


def _cleanup_instance_verifiers(workspace: Path) -> None:
    """Remove verifier containers orphaned by terminating an instance worker."""
    prefix = f"taco-verifier-{workspace.name}-"
    try:
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"name=^{prefix}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_ids = listed.stdout.split()
        if container_ids:
            subprocess.run(
                ["docker", "rm", "-f", *container_ids],
                capture_output=True,
                timeout=60,
            )
    except (OSError, subprocess.SubprocessError):
        pass


def run_instance_with_timeout(
    instance: SWEInstance,
    *,
    workspace: Path,
    workflow_mode: str,
    max_attempts: int,
    model_name: str,
    events_path: Path,
    timeout_seconds: int,
    planner_time_budget: int = 60,
    actor_time_budget: int = 300,
    verifier_time_budget: int = 120,
    memory_profile: str = "node-trimmed",
) -> tuple[SmokeResult, str]:
    """Run one isolated instance and terminate it at a hard wall-clock limit."""
    if timeout_seconds <= 0:
        return run_instance(
            instance,
            workspace=workspace,
            workflow_mode=workflow_mode,
            max_attempts=max_attempts,
            model_name=model_name,
            events_path=events_path,
            planner_time_budget=planner_time_budget,
            actor_time_budget=actor_time_budget,
            verifier_time_budget=verifier_time_budget,
            total_time_budget_seconds=timeout_seconds if timeout_seconds > 0 else 600,
            memory_profile=memory_profile,
        )
    context = multiprocessing.get_context("spawn")
    output_queue = context.Queue()
    kwargs = {
        "instance": instance,
        "workspace": workspace,
        "workflow_mode": workflow_mode,
        "max_attempts": max_attempts,
        "model_name": model_name,
        "events_path": events_path,
        "planner_time_budget": planner_time_budget,
        "actor_time_budget": actor_time_budget,
        "verifier_time_budget": verifier_time_budget,
        "memory_profile": memory_profile,
        "total_time_budget_seconds": timeout_seconds if timeout_seconds > 0 else 600,
    }
    started = perf_counter()
    process = context.Process(target=_instance_worker, args=(output_queue, kwargs))
    process.start()
    payload: dict[str, Any] | None = None
    patch = ""
    deadline = started + timeout_seconds
    while perf_counter() < deadline:
        try:
            payload, patch = output_queue.get(timeout=max(0.01, min(1.0, deadline - perf_counter())))
            break
        except queue.Empty:
            if not process.is_alive():
                break
    if payload is None and process.is_alive():
        process.terminate()
        process.join(10)
        _cleanup_instance_verifiers(workspace)
        events: list[dict[str, Any]] = []
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        patch = ""
        if (workspace / ".git").exists():
            try:
                patch = export_model_patch(workspace, production_only=True)
            except Exception:
                patch = ""
        result = (
            SmokeResult(
                instance_id=instance.instance_id,
                workflow_mode=workflow_mode,
                model_name=model_name,
                status="timed_out",
                duration_seconds=round(perf_counter() - started, 3),
                patch_chars=len(patch),
                error=f"instance exceeded hard timeout of {timeout_seconds} seconds",
                event_count=len(events),
                tool_calls=sum(event.get("type") == "tool_call" for event in events),
                workspace=str(workspace),
                metadata={
                    "memory_profile": memory_profile,
                    "token_usage": _event_token_usage(events),
                    **_event_diagnostics(events),
                },
            ),
            patch,
        )
        output_queue.close()
        return result
    process.join(10)
    if payload is None:
        try:
            payload, patch = output_queue.get_nowait()
        except queue.Empty:
            payload, patch = (
            SmokeResult(
                instance_id=instance.instance_id,
                workflow_mode=workflow_mode,
                model_name=model_name,
                status="failed",
                duration_seconds=round(perf_counter() - started, 3),
                patch_chars=0,
                error=f"worker exited with code {process.exitcode} without a result",
                workspace=str(workspace),
            ).as_dict(),
            "",
            )
    output_queue.close()
    return SmokeResult(**payload), patch


def run_smoke(
    *,
    output_root: Path,
    workflow_mode: str,
    max_attempts: int = 3,
    limit: int = 5,
    instance_ids: list[str] | None = None,
    resume: bool = False,
    dataset_name: str = DATASET_NAME,
    instance_timeout: int = 600,
    planner_time_budget: int = 60,
    actor_time_budget: int = 300,
    verifier_time_budget: int = 120,
    memory_profile: str = "node-trimmed",
) -> dict[str, Any]:
    if workflow_mode not in STREAMS:
        raise ValueError(f"Unknown workflow mode: {workflow_mode}")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    output_root = output_root.expanduser().resolve()
    if output_root.exists() and not resume:
        raise FileExistsError(f"Output root exists; use --resume: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    ledger = output_root / "results.jsonl"
    predictions = output_root / "predictions.jsonl"
    completed = _completed_ids(ledger) if resume else set()
    instances = load_verified_instances(
        limit=limit, instance_ids=instance_ids, dataset_name=dataset_name
    )
    model_name = os.getenv("OPENAI_MODEL", "unknown-model")
    processed = skipped = 0
    statuses: dict[str, int] = {}
    for instance in instances:
        if instance.instance_id in completed:
            skipped += 1
            continue
        workspace = output_root / "workspaces" / instance.instance_id
        result, patch = run_instance_with_timeout(
            instance,
            workspace=workspace,
            workflow_mode=workflow_mode,
            max_attempts=max_attempts,
            model_name=model_name,
            events_path=output_root / "events" / f"{instance.instance_id}.jsonl",
            timeout_seconds=instance_timeout,
            planner_time_budget=planner_time_budget,
            actor_time_budget=actor_time_budget,
            verifier_time_budget=verifier_time_budget,
            memory_profile=memory_profile,
        )
        _append_jsonl(ledger, result.as_dict())
        _append_jsonl(
            predictions,
            {
                "instance_id": instance.instance_id,
                "model_name_or_path": f"tacoagent/{workflow_mode}/{model_name}",
                "model_patch": patch,
            },
        )
        statuses[result.status] = statuses.get(result.status, 0) + 1
        processed += 1
    summary = {
        "dataset": dataset_name,
        "workflow_mode": workflow_mode,
        "model": model_name,
        "memory_profile": memory_profile,
        "requested": len(instances),
        "processed": processed,
        "skipped": skipped,
        "statuses": statuses,
        "predictions_path": str(predictions),
        "ledger_path": str(ledger),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TacoAgent on up to ten SWE-bench Verified instances.")
    parser.add_argument("--mode", choices=sorted(STREAMS), default="single-agent")
    parser.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--instance", action="append", dest="instance_ids")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--run-id", default=f"smoke-{datetime.now():%Y%m%d-%H%M%S}")
    parser.add_argument("--output-dir", type=Path, default=Path("evals/results/swebench-smoke"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--instance-timeout",
        type=int,
        default=600,
        help="Hard wall-clock timeout per instance in seconds; defaults to 10 minutes; 0 disables it.",
    )
    parser.add_argument("--planner-time-budget", type=int, default=60)
    parser.add_argument("--actor-time-budget", type=int, default=300)
    parser.add_argument("--verifier-time-budget", type=int, default=120)
    parser.add_argument(
        "--memory-profile",
        choices=("legacy-uniform", "node-trimmed"),
        default="node-trimmed",
    )
    args = parser.parse_args()
    summary = run_smoke(
        output_root=args.output_dir / args.run_id,
        workflow_mode=args.mode,
        max_attempts=args.max_attempts,
        limit=args.limit,
        instance_ids=args.instance_ids,
        resume=args.resume,
        dataset_name=args.dataset,
        instance_timeout=args.instance_timeout,
        planner_time_budget=args.planner_time_budget,
        actor_time_budget=args.actor_time_budget,
        verifier_time_budget=args.verifier_time_budget,
        memory_profile=args.memory_profile,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
