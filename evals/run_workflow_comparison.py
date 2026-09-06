"""Compare all TacoAgent workflows locally with the LangSmith benchmark suite."""

from __future__ import annotations

import argparse
import json
import os
from statistics import mean
from time import perf_counter
from typing import Any

from evals.langsmith_suite import DATASET_EXAMPLES, EVALUATORS, tacoagent_target


WORKFLOW_MODES = ("single-agent", "multi-agent", "react")


def run_comparison() -> dict[str, Any]:
    report: dict[str, Any] = {"suite": "tacoagent-workflow-v1", "workflows": {}}
    for mode in WORKFLOW_MODES:
        os.environ["TACO_LANGSMITH_WORKFLOW_MODE"] = mode
        cases = []
        started = perf_counter()
        for index, example in enumerate(DATASET_EXAMPLES, start=1):
            case_started = perf_counter()
            try:
                outputs = tacoagent_target(dict(example["inputs"]))
                scores = {
                    result["key"]: result["score"]
                    for evaluator in EVALUATORS
                    for result in [
                        evaluator(example["inputs"], outputs, example["outputs"])
                    ]
                }
                error = ""
            except Exception as exc:
                outputs = {}
                scores = {evaluator.__name__: 0 for evaluator in EVALUATORS}
                error = f"{type(exc).__name__}: {exc}"
            cases.append(
                {
                    "case": index,
                    "category": example.get("metadata", {}).get("category", ""),
                    "duration_seconds": round(perf_counter() - case_started, 3),
                    "scores": scores,
                    "outputs": outputs,
                    "error": error,
                }
            )
        score_keys = sorted({key for case in cases for key in case["scores"]})
        averages = {
            key: round(mean(case["scores"].get(key, 0) for case in cases), 4)
            for key in score_keys
        }
        report["workflows"][mode] = {
            "duration_seconds": round(perf_counter() - started, 3),
            "average_scores": averages,
            "overall_score": round(mean(averages.values()), 4) if averages else 0,
            "cases": cases,
        }
    os.environ.pop("TACO_LANGSMITH_WORKFLOW_MODE", None)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_comparison(), ensure_ascii=False, indent=None if args.compact else 2))


if __name__ == "__main__":
    main()
