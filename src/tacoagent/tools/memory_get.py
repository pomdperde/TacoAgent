from __future__ import annotations

from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.memory import get_external_memory


class MemoryGetTool:
    """Read one detailed task memory from the workspace-local external store."""

    name = "memory_get"
    description = "Fetch complete local task-memory details by an exact memory_id."

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        self.sandbox = sandbox

    def invoke(self, *, memory_id: str) -> dict:
        return get_external_memory(self.sandbox.root, memory_id)
