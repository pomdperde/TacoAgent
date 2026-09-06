from __future__ import annotations

import time
import unittest

from tacoagent.graph.single_agent_planner_node import (
    _fallback_plan,
    _provider_timeout,
    _run_with_hard_deadline,
)


def _slow_worker(connection, delay: float) -> None:
    time.sleep(delay)
    connection.send(("ok", "late"))
    connection.close()


def _fast_worker(connection, value: str) -> None:
    connection.send(("ok", value))
    connection.close()


class PlannerDeadlineTests(unittest.TestCase):
    def test_provider_timeout_reserves_part_of_hard_budget(self) -> None:
        self.assertEqual(_provider_timeout(150), 90)
        self.assertLess(_provider_timeout(5), 5)

    def test_subprocess_result_is_returned_before_deadline(self) -> None:
        self.assertEqual(
            _run_with_hard_deadline(_fast_worker, ("plan",), 5),
            ("ok", "plan"),
        )

    def test_subprocess_is_terminated_at_hard_deadline(self) -> None:
        started = time.monotonic()
        status, message = _run_with_hard_deadline(_slow_worker, (5,), 0.2)
        self.assertEqual(status, "timeout")
        self.assertIn("hard timeout", message)
        self.assertLess(time.monotonic() - started, 3)

    def test_fallback_plan_is_valid_and_actionable(self) -> None:
        plan = _fallback_plan("Fix nested model behavior", reason="timeout")
        self.assertEqual(len(plan["todos"]), 3)
        self.assertEqual(plan["todos"][0]["status"], "pending")
        self.assertIn("production-code edit", plan["plan_summary"])
        self.assertEqual(plan["verification_commands"], [])


if __name__ == "__main__":
    unittest.main()
