from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from evals.langsmith_suite import (
    DATASET_EXAMPLES,
    expected_artifacts_present,
    final_answer_present,
    register_dataset,
    required_events_present,
    route_correct,
    task_success,
)


class LangSmithEvaluationTests(unittest.TestCase):
    def test_registration_reuses_existing_dataset(self) -> None:
        dataset = SimpleNamespace(id="dataset-1", name="benchmark")
        client = Mock()
        client.list_datasets.return_value = iter([dataset])

        result, created = register_dataset(client, dataset_name="benchmark")

        self.assertIs(result, dataset)
        self.assertFalse(created)
        client.create_dataset.assert_not_called()
        client.create_examples.assert_not_called()

    def test_registration_creates_examples_for_new_dataset(self) -> None:
        dataset = SimpleNamespace(id="dataset-2", name="benchmark")
        client = Mock()
        client.list_datasets.return_value = iter([])
        client.create_dataset.return_value = dataset

        result, created = register_dataset(client, dataset_name="benchmark")

        self.assertIs(result, dataset)
        self.assertTrue(created)
        client.create_examples.assert_called_once_with(
            dataset_id="dataset-2", examples=DATASET_EXAMPLES
        )

    def test_evaluators_score_observable_workflow_outputs(self) -> None:
        outputs = {
            "route": "workflow",
            "final_answer": "Completed",
            "task_passed": True,
            "event_types": ["intent_route", "final_answer"],
            "workspace_files": ["hello.txt"],
        }
        reference = {
            "expected_route": "workflow",
            "required_event_types": ["intent_route", "final_answer"],
            "expected_files": ["hello.txt"],
        }

        for evaluator in (
            route_correct,
            final_answer_present,
            task_success,
            required_events_present,
            expected_artifacts_present,
        ):
            with self.subTest(evaluator=evaluator.__name__):
                self.assertEqual(evaluator({}, outputs, reference)["score"], 1)

    def test_evaluators_report_missing_outputs(self) -> None:
        outputs = {
            "route": "chat",
            "final_answer": "",
            "task_passed": False,
            "event_types": [],
            "workspace_files": [],
        }
        reference = {
            "expected_route": "workflow",
            "required_event_types": ["final_answer"],
            "expected_files": ["hello.txt"],
        }
        self.assertEqual(route_correct({}, outputs, reference)["score"], 0)
        self.assertEqual(final_answer_present({}, outputs, reference)["score"], 0)
        self.assertEqual(task_success({}, outputs, reference)["score"], 0)
        self.assertEqual(required_events_present({}, outputs, reference)["score"], 0)
        self.assertEqual(expected_artifacts_present({}, outputs, reference)["score"], 0)

    def test_task_success_requires_the_expected_route(self) -> None:
        outputs = {"route": "chat", "task_passed": True}
        reference = {"expected_route": "workflow"}
        self.assertEqual(task_success({}, outputs, reference)["score"], 0)


if __name__ == "__main__":
    unittest.main()
