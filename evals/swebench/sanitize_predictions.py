from __future__ import annotations

import argparse
import json
from pathlib import Path

from .git_ops import changed_test_artifacts, export_model_patch


def sanitize_run(run_dir: Path, output: Path) -> dict[str, object]:
    """Re-export a completed run without test-side changes."""
    report = json.loads((run_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    model_by_id = {
        item["instance_id"]: item["model_name_or_path"]
        for item in report.get("prediction_summaries", [])
    }
    rows: list[dict[str, str]] = []
    excluded: dict[str, list[str]] = {}
    for result in report.get("results", []):
        instance_id = str(result["instance_id"])
        workspace = Path(result["workspace"])
        excluded[instance_id] = changed_test_artifacts(workspace)
        rows.append({
            "instance_id": instance_id,
            "model_name_or_path": model_by_id.get(instance_id, "tacoagent/single-agent"),
            "model_patch": export_model_patch(workspace, production_only=True),
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {
        "output": str(output.resolve()),
        "instances": len(rows),
        "non_empty_patches": sum(bool(row["model_patch"].strip()) for row in rows),
        "excluded_test_artifacts": excluded,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove test-side changes from SWE-bench predictions.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = args.output or (run_dir / "predictions.production-only.jsonl")
    print(json.dumps(sanitize_run(run_dir, output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
