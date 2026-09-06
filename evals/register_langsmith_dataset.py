"""Register TacoAgent's benchmark dataset in LangSmith."""

from __future__ import annotations

import argparse

from evals.langsmith_suite import DATASET_NAME, configured_client, register_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=DATASET_NAME)
    args = parser.parse_args()
    dataset, created = register_dataset(configured_client(), dataset_name=args.dataset)
    action = "created" if created else "already exists"
    print(f"LangSmith dataset {action}: {dataset.name} ({dataset.id})")


if __name__ == "__main__":
    main()
