from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .approval import ApprovalDecision, ApprovalRequest, normalize_approval_mode
from .checkpoint import normalize_checkpoint_mode
from .trace import normalize_trace_mode
from .workspace import WorkspaceSandbox


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Runtime resources shared by TacoAgent graph nodes."""

    workspace: Path
    approval_mode: str
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None
    checkpoint_mode: str
    trace_mode: str
    trace_id: str | None

    def __init__(
        self,
        workspace: str | Path,
        *,
        approval_mode: str = "inline",
        approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None = None,
        checkpoint_mode: str = "light",
        trace_mode: str = "on",
        trace_id: str | None = None,
    ) -> None:
        sandbox = WorkspaceSandbox(workspace)
        object.__setattr__(self, "workspace", sandbox.root)
        object.__setattr__(self, "approval_mode", normalize_approval_mode(approval_mode))
        object.__setattr__(self, "approval_handler", approval_handler)
        object.__setattr__(self, "checkpoint_mode", normalize_checkpoint_mode(checkpoint_mode))
        object.__setattr__(self, "trace_mode", normalize_trace_mode(trace_mode))
        object.__setattr__(self, "trace_id", trace_id)

    @property
    def sandbox(self) -> WorkspaceSandbox:
        return WorkspaceSandbox(self.workspace)
