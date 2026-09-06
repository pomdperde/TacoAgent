from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv()

from .dataset import DATASET_NAME
from .register_langsmith import register_run
from .runner import run_smoke


DEFAULT_OUTPUT_ROOT = Path("evals/results/swebench-smoke")


@contextmanager
def _prevent_system_sleep():
    """Keep long Windows benchmark runs awake without changing power settings."""
    kernel32 = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if kernel32 is not None:
        kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    try:
        yield
    finally:
        if kernel32 is not None:
            kernel32.SetThreadExecutionState(0x80000000)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _local_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(item.get("duration_seconds", 0)) for item in results]
    metadata = [item.get("metadata", {}) for item in results]
    usage = [item.get("token_usage", {}) for item in metadata]
    provider_wait = [float(item.get("provider_wait_seconds", 0) or 0) for item in metadata]
    return {
        "instances": len(results),
        "statuses": dict(
            sorted(
                {
                    status: sum(item.get("status") == status for item in results)
                    for status in {str(item.get("status", "")) for item in results}
                }.items()
            )
        ),
        "patches_generated": sum(int(item.get("patch_chars", 0)) > 0 for item in results),
        "duration_seconds_total": round(sum(durations), 3),
        "duration_seconds_average": round(sum(durations) / len(durations), 3)
        if durations
        else 0,
        "tool_calls": sum(int(item.get("tool_calls", 0)) for item in results),
        "events": sum(int(item.get("event_count", 0)) for item in results),
        "verifier_passed": sum(item.get("verifier_passed") is True for item in results),
        "policy_block_count": sum(int(item.get("policy_block_count", 0) or 0) for item in metadata),
        "policy_reprompt_count": sum(int(item.get("policy_reprompt_count", 0) or 0) for item in metadata),
        "blocked_edit_count": sum(len(item.get("blocked_edits", [])) for item in metadata),
        "blocked_edits": [
            {"instance_id": result.get("instance_id", ""), **blocked_edit}
            for result, item in zip(results, metadata)
            for blocked_edit in item.get("blocked_edits", [])
        ],
        "provider_wait_seconds_total": round(sum(provider_wait), 3),
        "provider_wait_seconds_average": round(sum(provider_wait) / len(provider_wait), 3)
        if provider_wait
        else 0,
        "token_usage": {
            key: sum(int(item.get(key, 0) or 0) for item in usage)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        },
    }


def _prediction_summaries(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for item in predictions:
        patch = str(item.get("model_patch", ""))
        summaries.append(
            {
                "instance_id": item.get("instance_id", ""),
                "model_name_or_path": item.get("model_name_or_path", ""),
                "patch_chars": len(patch),
                "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            }
        )
    return summaries


def _run_official_harness(
    *, predictions: Path, run_id: str, report_dir: Path, workers: int, timeout: int
) -> dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        DATASET_NAME,
        "--predictions_path",
        str(predictions.resolve()),
        "--max_workers",
        str(workers),
        "--timeout",
        str(timeout),
        "--run_id",
        run_id,
    ]
    completed = subprocess.run(
        command,
        cwd=report_dir,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "requested": True,
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "command": command,
        "report_dir": str(report_dir),
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }


def execute_evaluation(
    *,
    mode: str,
    run_id: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    limit: int = 5,
    instance_ids: list[str] | None = None,
    max_attempts: int = 3,
    instance_timeout: int = 600,
    planner_time_budget: int = 60,
    actor_time_budget: int = 300,
    verifier_time_budget: int = 120,
    resume: bool = False,
    upload_langsmith: bool = True,
    langsmith_dataset: str | None = None,
    official: bool = False,
    official_workers: int = 1,
    official_timeout: int = 1800,
    memory_profile: str = "node-trimmed",
) -> dict[str, Any]:
    if mode not in {"react", "single-agent"}:
        raise ValueError("mode must be react or single-agent")
    run_dir = (output_root / run_id).resolve()
    report_path = run_dir / "evaluation_report.json"
    started_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "run_id": run_id,
        "workflow_mode": mode,
        "dataset": DATASET_NAME,
        "started_at": started_at.isoformat(),
        "configuration": {
            "limit": limit,
            "instance_ids": instance_ids or [],
            "max_attempts": max_attempts,
            "instance_timeout": instance_timeout,
            "planner_time_budget": planner_time_budget,
            "actor_time_budget": actor_time_budget,
            "verifier_time_budget": verifier_time_budget,
            "resume": resume,
            "memory_profile": memory_profile,
        },
        "paths": {"run_dir": str(run_dir), "report": str(report_path)},
    }
    try:
        smoke_summary = run_smoke(
            output_root=run_dir,
            workflow_mode=mode,
            max_attempts=max_attempts,
            limit=limit,
            instance_ids=instance_ids,
            resume=resume,
            dataset_name=DATASET_NAME,
            instance_timeout=instance_timeout,
            planner_time_budget=planner_time_budget,
            actor_time_budget=actor_time_budget,
            verifier_time_budget=verifier_time_budget,
            memory_profile=memory_profile,
        )
        results_path = run_dir / "results.jsonl"
        predictions_path = run_dir / "predictions.jsonl"
        results = _read_jsonl(results_path)
        predictions = _read_jsonl(predictions_path)
        report.update(
            smoke_summary=smoke_summary,
            metrics=_local_metrics(results),
            results=results,
            prediction_summaries=_prediction_summaries(predictions),
        )
        report["paths"].update(
            results=str(results_path),
            predictions=str(predictions_path),
            events=str(run_dir / "events"),
            workspaces=str(run_dir / "workspaces"),
        )

        if upload_langsmith:
            dataset_name = langsmith_dataset or (
                f"TacoAgent SWE-bench Verified {mode} {run_id}"
            )
            try:
                dataset, example_count, created = register_run(run_dir, dataset_name)
                report["langsmith"] = {
                    "requested": True,
                    "ok": True,
                    "dataset_name": dataset.name,
                    "dataset_id": str(dataset.id),
                    "examples_uploaded": example_count,
                    "created": created,
                }
            except Exception as exc:
                report["langsmith"] = {
                    "requested": True,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        else:
            report["langsmith"] = {"requested": False}

        report["official_harness"] = (
            _run_official_harness(
                predictions=predictions_path,
                run_id=run_id,
                report_dir=run_dir / "official",
                workers=official_workers,
                timeout=official_timeout,
            )
            if official
            else {"requested": False, "officially_graded": False}
        )
        report["status"] = "finished"
    except BaseException as exc:
        report["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        ended_at = datetime.now(timezone.utc)
        report["ended_at"] = ended_at.isoformat()
        report["duration_seconds"] = round((ended_at - started_at).total_seconds(), 3)
        _write_report(report_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "One-command TacoAgent SWE-bench runner with local JSON reporting "
            "and LangSmith dataset upload."
        )
    )
    parser.add_argument("--mode", choices=["react", "single-agent"], default="single-agent")
    parser.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--instance", action="append", dest="instance_ids")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--instance-timeout", type=int, default=600)
    parser.add_argument("--planner-time-budget", type=int, default=60)
    parser.add_argument("--actor-time-budget", type=int, default=300)
    parser.add_argument("--verifier-time-budget", type=int, default=120)
    parser.add_argument(
        "--memory-profile",
        choices=("legacy-uniform", "node-trimmed"),
        default="node-trimmed",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-langsmith", action="store_true")
    parser.add_argument("--langsmith-dataset", default="")
    parser.add_argument("--official", action="store_true")
    parser.add_argument("--official-workers", type=int, default=1)
    parser.add_argument("--official-timeout", type=int, default=1800)
    args = parser.parse_args()
    run_id = args.run_id or f"{args.mode}-{datetime.now():%Y%m%d-%H%M%S}"
    with _prevent_system_sleep():
        report = execute_evaluation(
            mode=args.mode,
            run_id=run_id,
            output_root=args.output_dir,
            limit=args.limit,
            instance_ids=args.instance_ids,
            max_attempts=args.max_attempts,
            instance_timeout=args.instance_timeout,
            planner_time_budget=args.planner_time_budget,
            actor_time_budget=args.actor_time_budget,
            verifier_time_budget=args.verifier_time_budget,
            memory_profile=args.memory_profile,
            resume=args.resume,
            upload_langsmith=not args.no_langsmith,
            langsmith_dataset=args.langsmith_dataset or None,
            official=args.official,
            official_workers=args.official_workers,
            official_timeout=args.official_timeout,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
