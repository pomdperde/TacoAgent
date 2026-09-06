import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evals.swebench.dataset import load_verified_instances
from evals.swebench.git_ops import export_model_patch, is_test_artifact
from evals.swebench.models import SWEInstance
from evals.swebench.runner import (
    _cleanup_instance_verifiers,
    _event_diagnostics,
    _safe_changed_test_artifacts,
    run_instance,
)


class FakeRows(list):
    def select(self, indexes):
        return FakeRows(self[index] for index in indexes)


class SweBenchSmokeTests(unittest.TestCase):
    def test_event_diagnostics_records_policy_and_provider_waits(self) -> None:
        diagnostics = _event_diagnostics([
            {"type": "provider_wait", "seconds": 1.25},
            {"type": "provider_wait", "seconds": 2.5},
            {"type": "tool_policy_blocked", "phase": "minimal_verification",
             "tool": "file_edit", "args": {"file_path": "module.py"},
             "reason": "deferred", "blocked_edit": True, "deferred": True},
            {"type": "policy_reprompt"},
        ])
        self.assertEqual(diagnostics["provider_wait_seconds"], 3.75)
        self.assertEqual(diagnostics["policy_block_count"], 1)
        self.assertEqual(diagnostics["policy_reprompt_count"], 1)
        self.assertTrue(diagnostics["blocked_edits"][0]["deferred"])

    def test_optional_test_artifact_diagnostics_do_not_mask_failures(self) -> None:
        with patch(
            "evals.swebench.runner.changed_test_artifacts",
            side_effect=RuntimeError("repository has no HEAD"),
        ):
            self.assertEqual(_safe_changed_test_artifacts(Path("partial-workspace")), [])

    def test_cleanup_instance_verifiers_removes_matching_containers(self) -> None:
        listed = subprocess.CompletedProcess([], 0, stdout="container-a\ncontainer-b\n", stderr="")
        removed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch("evals.swebench.runner.subprocess.run", side_effect=[listed, removed]) as run:
            _cleanup_instance_verifiers(Path("workspaces") / "owner__repo-1")

        self.assertEqual(
            run.call_args_list[0].args[0],
            ["docker", "ps", "-aq", "--filter", "name=^taco-verifier-owner__repo-1-"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "rm", "-f", "container-a", "container-b"],
        )

    def test_dataset_loader_limits_default_subset_to_five(self) -> None:
        rows = FakeRows(
            {
                "instance_id": f"repo__issue-{index}",
                "repo": "owner/repo",
                "base_commit": f"commit-{index}",
                "problem_statement": f"issue {index}",
            }
            for index in range(8)
        )
        fake_module = SimpleNamespace(load_dataset=lambda *_args, **_kwargs: rows)
        with patch.dict("sys.modules", {"datasets": fake_module}):
            instances = load_verified_instances(limit=5)
        self.assertEqual(len(instances), 5)
        self.assertEqual(instances[0].instance_id, "repo__issue-0")

    def test_dataset_loader_supports_ten_and_rejects_more(self) -> None:
        rows = FakeRows(
            {"instance_id": f"repo__issue-{index}", "repo": "owner/repo", "base_commit": f"c-{index}", "problem_statement": "issue"}
            for index in range(12)
        )
        fake_module = SimpleNamespace(load_dataset=lambda *_args, **_kwargs: rows)
        with patch.dict("sys.modules", {"datasets": fake_module}):
            self.assertEqual(len(load_verified_instances(limit=10)), 10)
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            load_verified_instances(limit=11)

    def test_patch_export_includes_source_but_excludes_runtime_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "module.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

            (repo / "module.py").write_text("value = 2\n", encoding="utf-8")
            (repo / "regression_test.py").write_text("def test_value(): pass\n", encoding="utf-8")
            (repo / "TODO.md").write_text("internal plan\n", encoding="utf-8")
            (repo / ".tacoagent").mkdir()
            (repo / ".tacoagent" / "trace.json").write_text("{}", encoding="utf-8")

            model_patch = export_model_patch(repo)

        self.assertIn("module.py", model_patch)
        self.assertIn("regression_test.py", model_patch)
        self.assertNotIn("TODO.md", model_patch)
        self.assertNotIn("trace.json", model_patch)

    def test_production_patch_excludes_tests_that_conflict_with_official_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "pkg").mkdir()
            (repo / "pkg" / "tests").mkdir()
            (repo / "pkg" / "module.py").write_text("value = 1\n", encoding="utf-8")
            (repo / "pkg" / "tests" / "test_module.py").write_text("assert True\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

            (repo / "pkg" / "module.py").write_text("value = 2\n", encoding="utf-8")
            (repo / "pkg" / "tests" / "test_module.py").write_text("assert False\n", encoding="utf-8")
            model_patch = export_model_patch(repo, production_only=True)

        self.assertIn("pkg/module.py", model_patch)
        self.assertNotIn("test_module.py", model_patch)

    def test_test_artifact_classification(self) -> None:
        self.assertTrue(is_test_artifact("pkg/tests/test_module.py"))
        self.assertTrue(is_test_artifact("test_feature.py"))
        self.assertTrue(is_test_artifact("pkg/conftest.py"))
        self.assertFalse(is_test_artifact("pkg/testing_helpers.py"))

    def test_runner_forces_workflow_and_disables_multi_agent_search(self) -> None:
        instance = SWEInstance("owner__repo-1", "owner/repo", "abc", "fix bug")
        captured = {}

        def fake_stream(task, **kwargs):
            captured.update(kwargs)
            yield {"type": "model_usage", "input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
            yield {"type": "tool_call", "name": "FileEditTool", "args": {}}
            yield {"type": "final_answer", "content": "done"}

        with tempfile.TemporaryDirectory() as directory, patch(
            "evals.swebench.runner.prepare_repository"
        ), patch("evals.swebench.runner.export_model_patch", return_value="diff --git"):
            result, model_patch = run_instance(
                instance,
                workspace=Path(directory) / "repo",
                workflow_mode="multi-agent",
                max_attempts=2,
                model_name="test-model",
                events_path=Path(directory) / "events.jsonl",
                stream=fake_stream,
            )
            events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]

        self.assertTrue(captured["force_workflow"])
        self.assertTrue(captured["disable_web_search"])
        self.assertEqual(captured["max_attempts"], 2)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.metadata["token_usage"]["total_tokens"], 120)
        self.assertEqual(model_patch, "diff --git")
        self.assertEqual(len(events), 3)


if __name__ == "__main__":
    unittest.main()
