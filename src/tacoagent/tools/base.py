from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    """Interface implemented by tools available to TacoAgent."""

    name: str
    description: str

    def invoke(self, **kwargs: Any) -> Any:
        """Execute the tool with validated keyword arguments."""
        ...
