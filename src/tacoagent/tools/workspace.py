from __future__ import annotations

from dataclasses import dataclass

from tacoagent.core.workspace import WorkspaceSandbox


@dataclass(slots=True)
class ListWorkspaceFilesTool:
    workspace: WorkspaceSandbox
    name: str = "list_workspace_files"
    description: str = "List files in the current TacoAgent workspace."

    def invoke(self, **kwargs) -> list[str]:
        limit = max(1, min(int(kwargs.get("limit", 50)), 500))
        return self.workspace.files(limit=limit)
