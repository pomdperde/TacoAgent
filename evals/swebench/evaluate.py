from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .dataset import DATASET_NAME


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade TacoAgent predictions with the official SWE-bench harness.")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--run-id", default="tacoagent-smoke")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--report-dir", type=Path, default=Path("evals/results/swebench-smoke/official"))
    args = parser.parse_args()
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).with_name("harness_compat.py").resolve()),
        "--dataset_name",
        args.dataset,
        "--predictions_path",
        str(args.predictions.resolve()),
        "--max_workers",
        str(args.workers),
        "--timeout",
        str(args.timeout),
        "--run_id",
        args.run_id,
    ]
    raise SystemExit(subprocess.call(command, cwd=report_dir))


if __name__ == "__main__":
    main()
