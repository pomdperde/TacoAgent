from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langsmith import Client

from .dataset import load_verified_instances


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def register_run(run_dir: Path, dataset_name: str) -> tuple[Any, int, bool]:
    load_dotenv()
    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("missing LANGSMITH_API_KEY")
    run_dir = run_dir.resolve()
    results = _read_jsonl(run_dir / "results.jsonl")
    predictions = _read_jsonl(run_dir / "predictions.jsonl")
    result_by_id = {item["instance_id"]: item for item in results}
    prediction_by_id = {item["instance_id"]: item for item in predictions}
    ids = list(result_by_id)
    instances = load_verified_instances(limit=min(10, len(ids)), instance_ids=ids)
    client = Client()
    existing = list(client.list_datasets(dataset_name=dataset_name, limit=1))
    if existing:
        return existing[0], 0, False
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description=(
            "SWE-bench Verified smoke run (up to ten instances) produced by TacoAgent. "
            "Outputs are ungraded model patches and local execution metrics."
        ),
    )
    examples = []
    for instance in instances:
        result = result_by_id[instance.instance_id]
        prediction = prediction_by_id[instance.instance_id]
        examples.append(
            {
                "inputs": {
                    "instance_id": instance.instance_id,
                    "repo": instance.repo,
                    "base_commit": instance.base_commit,
                    "problem_statement": instance.problem_statement,
                },
                "outputs": {
                    "model_patch": prediction.get("model_patch", ""),
                    "status": result.get("status", ""),
                    "patch_chars": result.get("patch_chars", 0),
                    "duration_seconds": result.get("duration_seconds", 0),
                    "tool_calls": result.get("tool_calls", 0),
                    "event_count": result.get("event_count", 0),
                },
                "metadata": {
                    "workflow_mode": result.get("workflow_mode", "react"),
                    "model": os.getenv("OPENAI_MODEL", result.get("model_name", "")),
                    "officially_graded": False,
                    "source_run_dir": str(run_dir),
                },
            }
        )
    client.create_examples(dataset_id=dataset.id, examples=examples)
    return dataset, len(examples), True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a completed TacoAgent SWE-bench smoke run in LangSmith."
    )
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--dataset",
        default="TacoAgent SWE-bench Verified smoke react v1",
    )
    args = parser.parse_args()
    dataset, count, created = register_run(args.run_dir, args.dataset)
    action = "created" if created else "already exists"
    print(f"LangSmith dataset {action}: {dataset.name} ({dataset.id}); examples={count}")


if __name__ == "__main__":
    main()
