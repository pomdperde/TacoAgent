"""Offline A/B benchmark for legacy uniform vs node-trimmed layered memory."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import Any, Callable

import tiktoken

from tacoagent.core.runtime import RuntimeState
from tacoagent.memory import (
    EXTERNAL_MEMORY_DB,
    MEM0_QDRANT_PATH,
    RULES_LAYER,
    _short_text,
    _trim_handoffs,
    build_layered_memory,
    load_external_memory_index,
    read_history_summary,
    read_notepad,
    store_external_memory,
)


NODES = (
    "single_agent_planner",
    "multi_agent_supervisor",
    "actor",
    "codeAgent",
    "verifier",
    "searchAgent",
    "context_monitor",
    "context_compressor",
)


def legacy_uniform_memory(state: dict[str, Any], *, node: str) -> dict[str, Any]:
    """Reproduce the uniform snapshot immediately before node profiles existed."""
    runtime = state["runtime"]
    history = read_history_summary(runtime)
    notepad = read_notepad(runtime)
    task_memories = load_external_memory_index(runtime.workspace, limit=6)
    working_memory = {
        "node": node,
        "task": state.get("task", ""),
        "session_id": state.get("session_id", ""),
        "session_turn": state.get("session_turn", 0),
        "plan_summary": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "research_notes": _short_text(state.get("research_notes", ""), 1600),
        "sources": [
            {"title": str(item.get("title", "")), "url": str(item.get("url", ""))}
            for item in state.get("sources", [])
            if isinstance(item, dict)
        ],
        "agent_handoffs": _trim_handoffs(state.get("agent_handoffs", [])),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), 1000),
        "verifier_summary": _short_text(state.get("verifier_summary", ""), 1000),
        "last_error": _short_text(state.get("last_error", ""), 1400),
        "attempts": state.get("attempts", 0),
        "max_attempts": state.get("max_attempts", 3),
    }
    return {
        "rules": dict(RULES_LAYER),
        "working_memory": working_memory,
        "history_summary_store": {
            "history_path": "HISTORY_SUMMARY.md",
            "history_exists": history.get("exists", False),
            "history_summary": _short_text(history.get("content", ""), 2200),
            "notepad_path": "NOTEPAD.md",
            "notepad_exists": notepad.get("exists", False),
            "notepad": _short_text(notepad.get("content", ""), 1800),
            "context_summary": _short_text(state.get("context_summary", ""), 1600),
            "compression_events": state.get("compression_events", [])[-3:],
            "external_memory_path": EXTERNAL_MEMORY_DB,
            "external_memory_backend": "sqlite-detail-store+optional-mem0-index",
            "external_memory_detail_path": EXTERNAL_MEMORY_DB,
            "semantic_index_path": MEM0_QDRANT_PATH,
            "recent_task_memories": task_memories,
        },
    }


def fixture_state(root: Path) -> dict[str, Any]:
    (root / "HISTORY_SUMMARY.md").write_text("history-detail " * 700, encoding="utf-8")
    (root / "NOTEPAD.md").write_text("notepad-finding " * 500, encoding="utf-8")
    for index in range(6):
        store_external_memory(root, {
            "memory_id": f"memory-benchmark-{index}",
            "created_at": f"2026-09-01T00:00:0{index}+00:00",
            "session_id": "benchmark-session",
            "session_turn": index,
            "task": f"Historical task {index}",
            "status": "completed",
            "summary": f"Relevant historical outcome {index}",
            "detail": "external detail " * 1000,
        })
    return {
        "runtime": RuntimeState(root),
        "task": "Implement a repository-wide behavior change with regression coverage. " * 20,
        "session_id": "benchmark-session",
        "session_turn": 7,
        "plan_summary": "Inspect, implement, verify, and document the focused change. " * 40,
        "todos": [
            {"id": f"todo-{i}", "content": f"Detailed implementation step {i} " * 10, "status": "pending", "note": ""}
            for i in range(12)
        ],
        "acceptance_criteria": [f"Acceptance criterion {i} " * 12 for i in range(8)],
        "verification_commands": [f"python -m pytest tests/test_case_{i}.py -q" for i in range(8)],
        "research_notes": "research evidence " * 500,
        "sources": [{"title": f"Source {i}", "url": f"https://example.com/{i}"} for i in range(12)],
        "agent_handoffs": [
            {"from_agent": "planner", "to_agent": "codeAgent", "instruction": f"instruction {i} " * 30, "result": f"result {i} " * 120}
            for i in range(10)
        ],
        "code_agent_summary": "implementation summary " * 200,
        "verifier_summary": "verification summary " * 200,
        "last_error": "AssertionError in tests/test_behavior.py::test_expected_value " * 80,
        "attempts": 2,
        "max_attempts": 3,
        "context_summary": "prior compressed context " * 300,
        "compression_events": [{"summary": f"compression-{i}", "before_tokens": 100000} for i in range(5)],
        "memory_semantic_recall": False,
    }


def percentile_95(values: list[float]) -> float:
    return sorted(values)[max(0, int(len(values) * 0.95) - 1)]


def measure(
    builder: Callable[..., dict[str, Any]], state: dict[str, Any], node: str, repeats: int
) -> dict[str, Any]:
    for _ in range(5):
        builder(state, node=node)
    durations = []
    payload = ""
    for _ in range(repeats):
        started = perf_counter_ns()
        memory = builder(state, node=node)
        payload = json.dumps(memory, ensure_ascii=False, separators=(",", ":"), default=str)
        durations.append((perf_counter_ns() - started) / 1_000_000)
    encoding = tiktoken.get_encoding("cl100k_base")
    return {
        "input_tokens": len(encoding.encode(payload)),
        "payload_chars": len(payload),
        "build_ms_median": round(median(durations), 4),
        "build_ms_p95": round(percentile_95(durations), 4),
    }


def run_benchmark(*, repeats: int = 100) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        state = fixture_state(Path(directory))
        cases = []
        for node in NODES:
            before = measure(legacy_uniform_memory, state, node, repeats)
            after = measure(build_layered_memory, state, node, repeats)
            cases.append({
                "node": node,
                "before": before,
                "after": after,
                "token_reduction": before["input_tokens"] - after["input_tokens"],
                "token_reduction_percent": round(
                    100 * (before["input_tokens"] - after["input_tokens"]) / before["input_tokens"], 2
                ),
                "median_time_reduction_percent": round(
                    100 * (before["build_ms_median"] - after["build_ms_median"]) / before["build_ms_median"], 2
                ),
            })
    before_total = sum(item["before"]["input_tokens"] for item in cases)
    after_total = sum(item["after"]["input_tokens"] for item in cases)
    return {
        "experiment": "node-memory-profile-ab-v1",
        "method": "offline deterministic prompt-input benchmark",
        "tokenizer": "tiktoken cl100k_base",
        "repeats_per_node": repeats,
        "fixture": "long graph state + HISTORY_SUMMARY + NOTEPAD + 6 SQLite memory indexes",
        "cases": cases,
        "aggregate": {
            "before_input_tokens": before_total,
            "after_input_tokens": after_total,
            "token_reduction": before_total - after_total,
            "token_reduction_percent": round(100 * (before_total - after_total) / before_total, 2),
            "before_build_ms_median_sum": round(sum(item["before"]["build_ms_median"] for item in cases), 4),
            "after_build_ms_median_sum": round(sum(item["after"]["build_ms_median"] for item in cases), 4),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=100)
    parser.add_argument("--output", default="evals/results/memory_profile_ab_latest.json")
    args = parser.parse_args()
    if args.repeats <= 0:
        raise SystemExit("--repeats must be greater than zero")
    report = run_benchmark(repeats=args.repeats)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
