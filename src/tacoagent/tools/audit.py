from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


class ToolExecutionLogger:
    """Write structured tool lifecycle records to logging and optional JSONL."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path is not None else None
        self._logger = logging.getLogger("tacoagent.tools")
        self._lock = Lock()

    def record(self, event: str, **details: Any) -> dict[str, Any]:
        payload = {
            "event": event,
            "timestamp": utc_now(),
            **{key: json_safe(value) for key, value in details.items()},
        }
        self._logger.info("tool_event=%s", json.dumps(payload, ensure_ascii=False))
        if self.path is not None:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload
