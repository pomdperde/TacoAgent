"""Run TacoAgent's real workflow benchmark as a LangSmith experiment."""

from __future__ import annotations

import argparse
import os

from evals.langsmith_suite import (
    DATASET_NAME,
    EVALUATORS,
    configured_client,
    register_dataset,
    tacoagent_target,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--experiment-prefix", default="tacoagent-workflow")
    parser.add_argument(
        "--mode",
        choices=["single-agent", "multi-agent", "react"],
        help="Override the workflow mode stored in every dataset example.",
    )
    parser.add_argument("--max-concurrency", type=int, default=1)
    args = parser.parse_args()
    if args.mode:
        os.environ["TACO_LANGSMITH_WORKFLOW_MODE"] = args.mode
    client = configured_client()
    dataset, _created = register_dataset(client, dataset_name=args.dataset)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client.evaluate(
        tacoagent_target,
        data=dataset.name,
        evaluators=EVALUATORS,
        experiment_prefix=args.experiment_prefix,
        description="TacoAgent end-to-end workflow regression benchmark.",
        max_concurrency=max(0, args.max_concurrency),
        metadata={
            "models": [f"openai:{model}"],
            "workflow_mode": args.mode or "dataset-default",
            "suite": "tacoagent-workflow-v1",
        },
    )


if __name__ == "__main__":
    main()
