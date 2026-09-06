"""Fetch concise result summaries for completed LangSmith experiments."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from statistics import mean
from typing import Any

from evals.langsmith_suite import configured_client


def summarize_experiment(client: Any, name: str) -> dict[str, Any]:
    project = client.read_project(project_name=name, include_stats=True)
    runs = list(client.list_runs(project_id=project.id, is_root=True))
    feedback_by_run: dict[str, dict[str, float]] = defaultdict(dict)
    if runs:
        for feedback in client.list_feedback(run_ids=[run.id for run in runs]):
            if feedback.score is not None:
                feedback_by_run[str(feedback.run_id)][feedback.key] = float(feedback.score)
    cases = []
    for run in runs:
        scores = feedback_by_run.get(str(run.id), {})
        raw_inputs = run.inputs or {}
        case_inputs = raw_inputs.get("inputs", raw_inputs)
        duration = None
        if run.end_time and run.start_time:
            duration = round((run.end_time - run.start_time).total_seconds(), 3)
        cases.append(
            {
                "run_id": str(run.id),
                "task": str(case_inputs.get("task", "")),
                "duration_seconds": duration,
                "error": str(run.error or ""),
                "scores": scores,
            }
        )
    keys = sorted({key for case in cases for key in case["scores"]})
    averages = {
        key: round(mean(case["scores"].get(key, 0) for case in cases), 4)
        for key in keys
    }
    return {
        "experiment": name,
        "project_id": str(project.id),
        "runs": len(runs),
        "successful_runs": sum(not case["error"] for case in cases),
        "average_scores": averages,
        "overall_score": round(mean(averages.values()), 4) if averages else 0,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", nargs="+")
    args = parser.parse_args()
    client = configured_client()
    summaries = [summarize_experiment(client, name) for name in args.experiments]
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
