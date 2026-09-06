import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evals.swebench.run_evaluation import execute_evaluation


class SweBenchRunEvaluationTests(unittest.TestCase):
    def test_writes_consolidated_report_and_langsmith_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)

            def fake_smoke(**kwargs):
                run_dir = kwargs["output_root"]
                run_dir.mkdir(parents=True)
                (run_dir / "results.jsonl").write_text(
                    json.dumps(
                        {
                            "instance_id": "owner__repo-1",
                            "status": "completed",
                            "duration_seconds": 12.5,
                            "patch_chars": 4,
                            "tool_calls": 3,
                            "event_count": 8,
                            "verifier_passed": True,
                            "metadata": {
                                "policy_block_count": 2,
                                "policy_reprompt_count": 1,
                                "blocked_edits": [{"tool": "file_edit"}],
                                "provider_wait_seconds": 7.25,
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (run_dir / "predictions.jsonl").write_text(
                    json.dumps(
                        {
                            "instance_id": "owner__repo-1",
                            "model_name_or_path": "tacoagent/react/test",
                            "model_patch": "diff",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {"processed": 1, "workflow_mode": kwargs["workflow_mode"]}

            dataset = SimpleNamespace(name="dataset-name", id="dataset-id")
            with patch(
                "evals.swebench.run_evaluation.run_smoke", side_effect=fake_smoke
            ) as run_smoke, patch(
                "evals.swebench.run_evaluation.register_run",
                return_value=(dataset, 1, True),
            ):
                report = execute_evaluation(
                    mode="react",
                    run_id="test-run",
                    output_root=output_root,
                    limit=1,
                    instance_timeout=30,
                )

            saved = json.loads(
                (output_root / "test-run" / "evaluation_report.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(report["status"], "finished")
        self.assertEqual(saved["metrics"]["patches_generated"], 1)
        self.assertEqual(saved["metrics"]["tool_calls"], 3)
        self.assertEqual(saved["metrics"]["policy_block_count"], 2)
        self.assertEqual(saved["metrics"]["policy_reprompt_count"], 1)
        self.assertEqual(saved["metrics"]["blocked_edit_count"], 1)
        self.assertEqual(saved["metrics"]["blocked_edits"][0]["instance_id"], "owner__repo-1")
        self.assertEqual(saved["metrics"]["provider_wait_seconds_total"], 7.25)
        self.assertEqual(saved["langsmith"]["dataset_id"], "dataset-id")
        self.assertFalse(saved["official_harness"]["requested"])
        self.assertEqual(saved["prediction_summaries"][0]["patch_chars"], 4)
        self.assertEqual(run_smoke.call_args.kwargs["workflow_mode"], "react")
        self.assertEqual(run_smoke.call_args.kwargs["instance_timeout"], 30)

    def test_rejects_unknown_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "react or single-agent"):
                execute_evaluation(
                    mode="unknown",
                    run_id="bad",
                    output_root=Path(directory),
                    upload_langsmith=False,
                )


if __name__ == "__main__":
    unittest.main()
