from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.core.approval import ApprovalDecision, ApprovalRequest

from .command_run import CommandRunTool
from .file_create import FileCreateTool
from .file_edit import FileEditTool
from .file_read import FileReadTool
from .memory_get import MemoryGetTool
from .regex_search import RegexSearchTool
from .registry import ToolRegistry, build_tool_registry
from .workspace import ListWorkspaceFilesTool


class ListFilesInput(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


class FileReadInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path")
    max_chars: int = Field(default=100_000, gt=0)


class FileCreateInput(BaseModel):
    file_path: str = Field(description="Workspace-relative path for the new file")
    content: str = Field(default="", description="UTF-8 text content")


class FileEditInput(BaseModel):
    file_path: str = Field(description="Workspace-relative existing file path")
    mode: Literal["exact", "line", "anchor", "patch"] = "exact"
    old_text: str = Field(default="", description="Unique text for exact mode")
    new_text: str = Field(default="", description="Replacement text for exact/line/anchor mode")
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    expected_text: str = Field(default="", description="Optional stale-write guard for line mode")
    anchor_before: str = ""
    anchor_after: str = ""
    patch: str = Field(default="", description="Unified diff hunk(s) for the specified file")


class RegexSearchInput(BaseModel):
    pattern: str = Field(description="Python regular expression")
    path: str = Field(default=".", description="Workspace-relative file or directory")
    case_sensitive: bool = True
    max_results: int = Field(default=200, ge=1, le=2000)


class CommandRunInput(BaseModel):
    command: str = Field(description="Shell command to run from the workspace")
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class MemoryGetInput(BaseModel):
    memory_id: str = Field(description="Exact memory ID from the layered-memory index")


def _default_registry(
    sandbox: WorkspaceSandbox,
    *,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None = None,
) -> ToolRegistry:
    return build_tool_registry(
        [
            ListWorkspaceFilesTool(sandbox),
            FileReadTool(sandbox),
            FileCreateTool(sandbox),
            FileEditTool(sandbox),
            RegexSearchTool(sandbox),
            CommandRunTool(
                sandbox,
                approval_mode=approval_mode,
                approval_handler=approval_handler,
            ),
            MemoryGetTool(sandbox),
        ],
        log_path=sandbox.root / ".tacoagent" / "tool_calls.jsonl",
    )


def build_structured_tools(
    workspace: str | Path | WorkspaceSandbox,
    *,
    task_name: str = "model tool call",
    registry: ToolRegistry | None = None,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None = None,
) -> list[StructuredTool]:
    """Create model-bindable tools without bypassing safety or audit layers."""
    sandbox = workspace if isinstance(workspace, WorkspaceSandbox) else WorkspaceSandbox(workspace)
    tool_registry = registry or _default_registry(
        sandbox,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
    )

    def list_workspace_files(limit: int = 50):
        return tool_registry.invoke(
            "list_workspace_files", task_name=task_name, limit=limit
        )

    def file_read(file_path: str, max_chars: int = 100_000):
        return tool_registry.invoke(
            "file_read",
            task_name=task_name,
            file_path=file_path,
            max_chars=max_chars,
        )

    def file_create(file_path: str, content: str = ""):
        return tool_registry.invoke(
            "file_create", task_name=task_name, file_path=file_path, content=content
        )

    def file_edit(
        file_path: str, mode: str = "exact", old_text: str = "", new_text: str = "",
        start_line: int | None = None, end_line: int | None = None,
        expected_text: str = "", anchor_before: str = "", anchor_after: str = "",
        patch: str = "",
    ):
        return tool_registry.invoke(
            "file_edit",
            task_name=task_name,
            file_path=file_path,
            old_text=old_text,
            new_text=new_text,
            mode=mode,
            start_line=start_line,
            end_line=end_line,
            expected_text=expected_text,
            anchor_before=anchor_before,
            anchor_after=anchor_after,
            patch=patch,
        )

    def regex_search(
        pattern: str,
        path: str = ".",
        case_sensitive: bool = True,
        max_results: int = 200,
    ):
        return tool_registry.invoke(
            "regex_search",
            task_name=task_name,
            pattern=pattern,
            path=path,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )

    def command_run(command: str, timeout_seconds: float = 30):
        return tool_registry.invoke(
            "command_run",
            task_name=task_name,
            command=command,
            timeout_seconds=timeout_seconds,
        )

    def memory_get(memory_id: str):
        return tool_registry.invoke(
            "memory_get", task_name=task_name, memory_id=memory_id
        )

    specifications = [
        (list_workspace_files, "list_workspace_files", "List files in the current TacoAgent workspace.", ListFilesInput),
        (file_read, "file_read", "Read a UTF-8 text file inside the TacoAgent workspace.", FileReadInput),
        (file_create, "file_create", "Create a new text file inside the TacoAgent workspace.", FileCreateInput),
        (file_edit, "file_edit", "Edit one file by exact text, line range, stable anchors, or unified patch hunks.", FileEditInput),
        (regex_search, "regex_search", "Run a regular-expression search over workspace text files.", RegexSearchInput),
        (command_run, "command_run", "Run a shell command from the workspace with timeout control.", CommandRunInput),
        (memory_get, "memory_get", "Fetch detailed local task memory using an exact memory_id from the memory index.", MemoryGetInput),
    ]
    return [
        StructuredTool.from_function(
            func=function,
            name=name,
            description=description,
            args_schema=schema,
        )
        for function, name, description, schema in specifications
    ]


def create_tools(
    workspace: str | Path | WorkspaceSandbox,
    *,
    task_name: str = "model tool call",
    registry: ToolRegistry | None = None,
    approval_mode: str = "inline",
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision | bool] | None = None,
) -> list[StructuredTool]:
    """Compatibility alias for callers that prefer a concise factory name."""
    return build_structured_tools(
        workspace,
        task_name=task_name,
        registry=registry,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
    )
