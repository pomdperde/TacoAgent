from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval import evaluate
from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig
from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase, ToolCall
from langchain_core.messages import AIMessage

from tacoagent.core import agent as agent_module


class MetadataMetric(BaseMetric):
    key = ""
    label = "Metadata Metric"
    threshold = 1.0
    async_mode = False
    include_reason = True

    @property
    def __name__(self) -> str:
        return self.label

    def calculate(self, test_case: LLMTestCase) -> tuple[float, str]:
        raise NotImplementedError

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        self.score, self.reason = self.calculate(test_case)
        self.success = self.is_successful()
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case, *args, **kwargs)


class ToolSelectionMetric(MetadataMetric):
    label = "Tool Selection Accuracy"

    def calculate(self, test_case: LLMTestCase) -> tuple[float, str]:
        actual = [tool.name for tool in test_case.tools_called or []]
        expected = [tool.name for tool in test_case.expected_tools or []]
        score = 1.0 if actual == expected else 0.0
        return score, f"expected={expected}; actual={actual}"


class TaskCompletionMetric(MetadataMetric):
    label = "Task Completion"

    def calculate(self, test_case: LLMTestCase) -> tuple[float, str]:
        passed = bool((test_case.metadata or {}).get("task_completed"))
        return float(passed), "artifact/result check passed" if passed else "artifact/result check failed"


class WorkspaceSafetyMetric(MetadataMetric):
    label = "Workspace Safety"

    def calculate(self, test_case: LLMTestCase) -> tuple[float, str]:
        passed = bool((test_case.metadata or {}).get("workspace_safe"))
        return float(passed), "workspace boundary preserved" if passed else "workspace boundary violated"


class LatencyMetric(MetadataMetric):
    label = "Latency Budget"
    threshold = 0.8

    def calculate(self, test_case: LLMTestCase) -> tuple[float, str]:
        metadata = test_case.metadata or {}
        latency = float(metadata.get("latency_ms", 0.0))
        budget = float(metadata.get("latency_budget_ms", 250.0))
        score = 1.0 if latency <= budget else max(0.0, budget / latency)
        return score, f"latency={latency:.3f}ms; budget={budget:.3f}ms"


class TokenEfficiencyMetric(MetadataMetric):
    label = "Token Efficiency"
    threshold = 0.8

    def calculate(self, test_case: LLMTestCase) -> tuple[float, str]:
        metadata = test_case.metadata or {}
        tokens = int(metadata.get("total_tokens", 0))
        budget = int(metadata.get("token_budget", 100))
        if metadata.get("error") or tokens <= 0:
            return 0.0, f"no successful model completion; tokens={tokens}"
        score = 1.0 if tokens <= budget else max(0.0, budget / tokens)
        return score, f"tokens={tokens}; budget={budget}"


class ScriptedModel:
    def __init__(self, tool_steps: list[list[dict[str, Any]]], final: str) -> None:
        responses = []
        for index, calls in enumerate(tool_steps):
            responses.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": call["name"],
                            "args": call["args"],
                            "id": f"call-{index}-{call_index}",
                        }
                        for call_index, call in enumerate(calls)
                    ],
                    usage_metadata={
                        "input_tokens": 20,
                        "output_tokens": 5,
                        "total_tokens": 25,
                    },
                )
            )
        responses.append(
            AIMessage(
                content=final,
                usage_metadata={
                    "input_tokens": 25,
                    "output_tokens": 10,
                    "total_tokens": 35,
                },
            )
        )
        self.responses = iter(responses)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return next(self.responses)


def execute_case(
    *,
    name: str,
    task: str,
    workspace: Path,
    tool_steps: list[list[dict[str, Any]]],
    expected_tools: list[str],
    check: Callable[[list[dict[str, Any]]], bool],
    final: str = "Task completed.",
    latency_budget_ms: float = 250.0,
    token_budget: int = 100,
) -> LLMTestCase:
    model = ScriptedModel(tool_steps, final)
    started = perf_counter()
    with patch.object(agent_module, "create_model", return_value=model):
        events = list(
            agent_module.stream_agent_events(task, workspace=workspace, max_loops=10)
        )
    latency_ms = (perf_counter() - started) * 1000
    tool_events = [event for event in events if event["type"] == "tool_call"]
    result_events = [event for event in events if event["type"] == "tool_result"]
    final_event = next(event for event in reversed(events) if event["type"] == "final_answer")
    actual_tools = [
        ToolCall(name=event["name"], input_parameters=event["args"])
        for event in tool_events
    ]
    return LLMTestCase(
        name=name,
        input=task,
        actual_output=str(final_event["content"]),
        expected_output=final,
        tools_called=actual_tools,
        expected_tools=[ToolCall(name=tool_name) for tool_name in expected_tools],
        completion_time=latency_ms / 1000,
        metadata={
            "task_completed": check(result_events),
            "workspace_safe": all(
                not (workspace.parent / "escaped.txt").exists()
                for _ in [0]
            ),
            "latency_ms": latency_ms,
            "latency_budget_ms": latency_budget_ms,
            "total_tokens": final_event["token_usage"]["total_tokens"],
            "token_budget": token_budget,
            "tool_durations_ms": [event["duration_ms"] for event in result_events],
        },
    )


def build_cases(root: Path, *, suffix: str = "") -> list[LLMTestCase]:
    cases: list[LLMTestCase] = []

    create_ws = root / "create"
    cases.append(
        execute_case(
            name=f"create_file{suffix}",
            task="Create hello.txt containing hello taco",
            workspace=create_ws,
            tool_steps=[[{"name": "file_create", "args": {"file_path": "hello.txt", "content": "hello taco"}}]],
            expected_tools=["file_create"],
            check=lambda _: (create_ws / "hello.txt").read_text(encoding="utf-8") == "hello taco",
        )
    )

    edit_ws = root / "edit"
    edit_ws.mkdir(parents=True)
    (edit_ws / "note.txt").write_text("old value", encoding="utf-8")
    cases.append(
        execute_case(
            name=f"read_then_edit{suffix}",
            task="Read note.txt then replace old with new",
            workspace=edit_ws,
            tool_steps=[
                [{"name": "file_read", "args": {"file_path": "note.txt"}}],
                [{"name": "file_edit", "args": {"file_path": "note.txt", "old_text": "old", "new_text": "new"}}],
            ],
            expected_tools=["file_read", "file_edit"],
            check=lambda _: (edit_ws / "note.txt").read_text(encoding="utf-8") == "new value",
        )
    )

    search_ws = root / "search"
    search_ws.mkdir(parents=True)
    (search_ws / "app.py").write_text("# TODO: improve\n", encoding="utf-8")
    cases.append(
        execute_case(
            name=f"regex_search{suffix}",
            task="Find TODO markers",
            workspace=search_ws,
            tool_steps=[[{"name": "regex_search", "args": {"pattern": "TODO", "path": "."}}]],
            expected_tools=["regex_search"],
            check=lambda results: bool(results and results[0]["result"].get("match_count") == 1),
        )
    )

    command_ws = root / "command"
    command = f'"{sys.executable}" -c "print(6 * 7)"'
    cases.append(
        execute_case(
            name=f"command_execution{suffix}",
            task="Calculate 6 * 7 with a command",
            workspace=command_ws,
            tool_steps=[[{"name": "command_run", "args": {"command": command, "timeout_seconds": 5}}]],
            expected_tools=["command_run"],
            check=lambda results: bool(results and results[0]["result"].get("stdout", "").strip() == "42"),
            latency_budget_ms=1000,
        )
    )

    safety_ws = root / "safety"
    safety_ws.mkdir(parents=True)
    (root / "secret.txt").write_text("secret", encoding="utf-8")
    cases.append(
        execute_case(
            name=f"workspace_escape_rejected{suffix}",
            task="Read ../secret.txt",
            workspace=safety_ws,
            tool_steps=[[{"name": "file_read", "args": {"file_path": "../secret.txt"}}]],
            expected_tools=["file_read"],
            check=lambda results: bool(results and results[0]["result"].get("ok") is False),
        )
    )
    return cases


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[index]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="tacoagent-eval-") as directory:
        temporary_root = Path(directory)
        cases = []
        for iteration in range(3):
            cases.extend(
                build_cases(
                    temporary_root / f"iteration-{iteration + 1}",
                    suffix=f"_{iteration + 1}",
                )
            )
        metrics = [
            ToolSelectionMetric(),
            TaskCompletionMetric(),
            WorkspaceSafetyMetric(),
            LatencyMetric(),
            TokenEfficiencyMetric(),
        ]
        result = evaluate(
            test_cases=cases,
            metrics=metrics,
            identifier="tacoagent-offline-performance",
            hyperparameters={"model": "scripted-offline", "prompt": "ACTOR_PROMPT"},
            async_config=AsyncConfig(run_async=False),
            cache_config=CacheConfig(write_cache=False, use_cache=False),
            display_config=DisplayConfig(
                show_indicator=False,
                print_results=False,
                inspect_after_run=False,
            ),
        )

    case_results = []
    all_scores: dict[str, list[float]] = {}
    latencies = []
    tool_durations = []
    for test_result, case in zip(result.test_results, cases):
        metric_scores = {}
        for metric in test_result.metrics_data:
            metric_scores[metric.name] = metric.score
            all_scores.setdefault(metric.name, []).append(float(metric.score or 0.0))
        metadata = case.metadata or {}
        latencies.append(float(metadata["latency_ms"]))
        tool_durations.extend(float(value) for value in metadata["tool_durations_ms"])
        case_results.append(
            {
                "name": case.name,
                "success": test_result.success,
                "latency_ms": round(float(metadata["latency_ms"]), 3),
                "total_tokens": metadata["total_tokens"],
                "tools_called": [tool.name for tool in case.tools_called or []],
                "metrics": metric_scores,
            }
        )

    metric_averages = {
        name: round(statistics.mean(scores), 4) for name, scores in all_scores.items()
    }
    report = {
        "evaluation": "TacoAgent offline DeepEval performance suite",
        "deepeval_version": version("deepeval"),
        "mode": "offline_scripted_model_real_tools",
        "limitations": [
            "OPENAI_API_KEY was not configured, so model intelligence and LLM-as-a-judge metrics were not measured.",
            "Tool trajectories are scripted; real Workspace tools, safety checks, logging, and command execution are exercised.",
        ],
        "summary": {
            "cases": len(case_results),
            "passed": sum(bool(item["success"]) for item in case_results),
            "pass_rate": round(sum(bool(item["success"]) for item in case_results) / len(case_results), 4),
            "mean_latency_ms": round(statistics.mean(latencies), 3),
            "p95_latency_ms": round(percentile_95(latencies), 3),
            "mean_tool_duration_ms": round(statistics.mean(tool_durations), 3),
            "metric_averages": metric_averages,
        },
        "cases": case_results,
    }
    output_dir = PROJECT_ROOT / "evals" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "latest.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nReport: {output_path}")
    return 0 if report["summary"]["pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
