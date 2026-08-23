from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from tacoagent.tools.audit import json_safe, utc_now
from tacoagent.core.trace import TraceRecorder


class WorkflowEventRecorder:
    """Append workflow events to a workspace-local JSONL audit trail."""

    def __init__(self, runtime, task: str = "") -> None:
        self.path = Path(runtime.workspace).resolve() / ".tacoagent" / "workflow_events.jsonl"
        self._lock = Lock()
        self.trace = TraceRecorder(runtime, task=task)
        self._trace_ended = False
        self.latest_node: str | None = None

    def start(self, workflow: str, task: str) -> None:
        self.trace.start({"workflow": workflow, "task": task, "instruction": task})

    def end(self, *, status: str, result: Any = None) -> dict[str, Any] | None:
        if self._trace_ended:
            return {}
        self._trace_ended = True
        return self.trace.end(
            status=status, latest_node=self.latest_node, final_state=result
        )

    def record(self, workflow: str, event: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "timestamp": utc_now(),
            "workflow": workflow,
            **json_safe(event),
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if event.get("node"):
            self.latest_node = str(event["node"])
        self.trace.record_custom_event(event)
        if event.get("type") == "node_output" and event.get("task_status") == "failed":
            self.end(status="failed", result=event.get("return_result"))
        return payload

    def record_graph_update(self, event: dict[str, Any]) -> None:
        nodes = [str(node) for node in event if not str(node).startswith("__")]
        if nodes:
            self.latest_node = nodes[-1]
        self.trace.record_graph_update(event)
