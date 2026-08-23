from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from time import perf_counter
from typing import Any

from .audit import ToolExecutionLogger
from .base import Tool


class ToolRegistry:
    """In-memory registry for TacoAgent tools."""

    def __init__(
        self,
        tools: Iterable[Tool] = (),
        *,
        logger: ToolExecutionLogger | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.logger = logger or ToolExecutionLogger()
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool, *, replace: bool = False) -> Tool:
        name = str(getattr(tool, "name", "")).strip()
        if not name:
            raise ValueError("Tool name must not be empty")
        if not callable(getattr(tool, "invoke", None)):
            raise TypeError(f"Tool {name!r} must define an invoke() method")
        if name in self._tools and not replace:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool
        return tool

    def unregister(self, name: str) -> Tool:
        try:
            return self._tools.pop(name)
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def invoke(self, name: str, *, task_name: str = "", **kwargs: Any) -> Any:
        tool = self.get(name)
        started = perf_counter()
        self.logger.record(
            "tool_started",
            tool_name=name,
            task_name=task_name,
            arguments=kwargs,
        )
        try:
            result = tool.invoke(**kwargs)
        except Exception as exc:
            self.logger.record(
                "tool_failed",
                tool_name=name,
                task_name=task_name,
                arguments=kwargs,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round((perf_counter() - started) * 1000, 3),
            )
            raise
        outcome_failed = isinstance(result, dict) and result.get("ok") is False
        self.logger.record(
            "tool_failed" if outcome_failed else "tool_succeeded",
            tool_name=name,
            task_name=task_name,
            arguments=kwargs,
            result=result,
            error=(result.get("error") or result.get("stderr", ""))
            if outcome_failed
            else "",
            duration_ms=round((perf_counter() - started) * 1000, 3),
        )
        return result

    def names(self) -> list[str]:
        return sorted(self._tools)

    def descriptions(self) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "description": str(getattr(self._tools[name], "description", "")),
            }
            for name in self.names()
        ]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def build_tool_registry(
    tools: Iterable[Tool] = (), *, log_path: str | Path | None = None
) -> ToolRegistry:
    """Build the registry used for one TacoAgent runtime."""
    return ToolRegistry(tools, logger=ToolExecutionLogger(log_path))
