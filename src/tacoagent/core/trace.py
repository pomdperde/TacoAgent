from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4


VALID_TRACE_MODES = {"on", "off"}


def normalize_trace_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    return value if value in VALID_TRACE_MODES else "on"


def trace_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return str(value)


class TraceRecorder:
    def __init__(self, runtime, task: str = "") -> None:
        self.workspace = runtime.workspace
        self.mode = normalize_trace_mode(runtime.trace_mode)
        self.trace_id = runtime.trace_id or f"trace-{uuid4().hex[:12]}"
        self.root = self.workspace / ".tacoagent" / "traces" / self.trace_id
        self.task = task
        self.started_at = trace_now()
        self._started_clock = perf_counter()
        self.node_visits: dict[str, int] = {}
        self.tool_calls = 0
        self.failed_tool_calls = 0
        self.approval_count = 0
        self.checkpoint_count = 0
        self.handoff_count = 0
        self.timeline: list[dict[str, Any]] = []
        self.workflow = ""

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def _record(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        payload = {
            "timestamp": trace_now(),
            "trace_id": self.trace_id,
            **_safe(event),
        }
        self.timeline.append(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def start(
        self,
        inputs: dict[str, Any],
        *,
        resumed: bool = False,
        resume_event: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.task = str(inputs.get("task") or self.task)
        self.workflow = str(inputs.get("workflow", ""))
        return self._record(
            {
                "type": "run_start",
                "workflow": self.workflow,
                "task": self.task,
                "task_goal": self.task,
                "input_instruction": inputs.get("instruction") or self.task,
                "status": "running",
                "task_status": "running",
                "resumed": resumed,
                "resume_event": resume_event,
            }
        )

    def record_custom_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        event_type = event.get("type")
        if event_type == "tool_call":
            self.tool_calls += 1
        elif event_type == "tool_result":
            result = event.get("result", {})
            if isinstance(result, dict):
                if result.get("ok") is False:
                    self.failed_tool_calls += 1
                if result.get("requires_approval"):
                    self.approval_count += 1
        elif event_type == "handoff":
            self.handoff_count += 1
        elif event_type == "checkpoint_saved":
            self.checkpoint_count += 1
        return self._record({"category": "custom", **event})

    def record_graph_update(self, event: dict[str, Any]) -> dict[str, Any] | None:
        for node in event:
            if not str(node).startswith("__"):
                self.node_visits[str(node)] = self.node_visits.get(str(node), 0) + 1
        return self._record({"type": "graph_update", "category": "graph", "update": event})

    def end(
        self,
        *,
        status: str,
        latest_node: str | None,
        final_state: Any,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        ended_at = trace_now()
        duration_ms = round((perf_counter() - self._started_clock) * 1000, 3)
        self._record(
            {
                "type": "run_end",
                "task": self.task,
                "status": status,
                "task_status": status,
                "latest_node": latest_node,
                "return_result": final_state,
            }
        )
        head = self.timeline[:20]
        tail_start = max(20, len(self.timeline) - 80)
        tail = self.timeline[tail_start:]
        omitted = max(0, len(self.timeline) - len(head) - len(tail))
        payload = {
            "trace_id": self.trace_id,
            "task": self.task,
            "workflow": self.workflow,
            "status": status,
            "task_status": status,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "latest_node": latest_node,
            "node_visits": self.node_visits,
            "tool_calls": self.tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "approval_count": self.approval_count,
            "checkpoint_count": self.checkpoint_count,
            "handoff_count": self.handoff_count,
            "timeline_head": head,
            "timeline_tail": tail,
            "timeline_omitted": omitted,
            "return_result": _safe(final_state),
        }
        (self.root / "trace.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.root / "timeline.md").write_text(
            _timeline_markdown(payload, self.timeline), encoding="utf-8"
        )
        return payload


def _timeline_markdown(payload: dict[str, Any], timeline: list[dict[str, Any]]) -> str:
    lines = [
        f"# TacoAgent Trace `{payload['trace_id']}`",
        "",
        f"- Task: {payload['task']}",
        f"- Workflow: {payload['workflow']}",
        f"- Status: {payload['status']}",
        f"- Duration: {payload['duration_ms']} ms",
        f"- Latest node: {payload.get('latest_node') or 'unknown'}",
        "",
        "## Statistics",
        "",
        f"- Node visits: {json.dumps(payload['node_visits'], ensure_ascii=False)}",
        f"- Tool calls: {payload['tool_calls']}",
        f"- Failed tool calls: {payload['failed_tool_calls']}",
        f"- Approvals: {payload['approval_count']}",
        f"- Checkpoints: {payload['checkpoint_count']}",
        f"- Handoffs: {payload['handoff_count']}",
        "",
        "## Timeline",
        "",
    ]
    for event in timeline:
        label = event.get("type", "event")
        node = event.get("node") or ""
        status = event.get("task_status") or event.get("status") or ""
        details = " · ".join(part for part in (str(node), str(status)) if part)
        lines.append(f"- `{event.get('timestamp', '')}` **{label}**{f' — {details}' if details else ''}")
    return "\n".join(lines) + "\n"


class WorkflowEventRecorder:
    """Adapt workflow events to a single per-run :class:`TraceRecorder`."""

    def __init__(self, runtime, task: str = "") -> None:
        self.trace = TraceRecorder(runtime, task=task)
        self._trace_ended = False
        self.latest_node: str | None = None

    def start(self, workflow: str, task: str) -> None:
        self.trace.start(
            {"workflow": workflow, "task": task, "instruction": task}
        )

    def end(self, *, status: str, result: Any = None) -> dict[str, Any] | None:
        if self._trace_ended:
            return {}
        self._trace_ended = True
        return self.trace.end(
            status=status,
            latest_node=self.latest_node,
            final_state=result,
        )

    def record(self, workflow: str, event: dict[str, Any]) -> dict[str, Any]:
        """Record a custom event without creating a duplicate global event log."""
        if event.get("node"):
            self.latest_node = str(event["node"])
        payload = self.trace.record_custom_event(
            {"workflow": workflow, **event}
        )
        if event.get("type") == "node_output" and event.get("task_status") == "failed":
            self.end(status="failed", result=event.get("return_result"))
        return payload or {"workflow": workflow, **_safe(event)}

    def record_graph_update(self, event: dict[str, Any]) -> None:
        nodes = [str(node) for node in event if not str(node).startswith("__")]
        if nodes:
            self.latest_node = nodes[-1]
        self.trace.record_graph_update(event)
