from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.core.react import stream_agent_events
from tacoagent.tools import (
    CommandRunTool,
    FileCreateTool,
    FileEditTool,
    FileReadTool,
    ListWorkspaceFilesTool,
    RegexSearchTool,
    ToolRegistry,
    build_tool_registry,
)


@dataclass(frozen=True, slots=True)
class AgentResponse:
    content: str
    turn: int


class TacoAgent:
    """Small workspace-scoped agent with replaceable response logic."""

    def __init__(
        self, workspace: str | Path, *, tools: ToolRegistry | None = None
    ) -> None:
        self.sandbox = WorkspaceSandbox(workspace)
        self.workspace = self.sandbox.root
        self.history: list[dict[str, str]] = []
        self.tools = tools or build_tool_registry(
            [
                ListWorkspaceFilesTool(self.sandbox),
                FileReadTool(self.sandbox),
                FileCreateTool(self.sandbox),
                FileEditTool(self.sandbox),
                RegexSearchTool(self.sandbox),
                CommandRunTool(self.sandbox),
            ],
            log_path=self.workspace / ".tacoagent" / "tool_calls.jsonl",
        )

    def run(self, task: str) -> AgentResponse:
        task = task.strip()
        if not task:
            raise ValueError("Task must not be empty")
        self.history.append({"role": "user", "content": task})
        if task.startswith("/") or task.casefold() in {"help", "帮助"}:
            content = self._respond(task)
        else:
            final_event = None
            for event in stream_agent_events(task, workspace=self.workspace):
                if event["type"] == "final_answer":
                    final_event = event
            content = str((final_event or {}).get("content", ""))
        self.history.append({"role": "assistant", "content": content})
        return AgentResponse(content=content, turn=len(self.history) // 2)

    def _respond(self, task: str) -> str:
        """Local starter backend; replace this method with an LLM integration."""
        lowered = task.casefold()
        if lowered in {"/files", "files", "列出文件"}:
            files = self.tools.invoke(
                "list_workspace_files",
                task_name=task,
                limit=50,
            )
            if not files:
                return "Workspace is empty."
            return "Workspace files:\n" + "\n".join(f"- {name}" for name in files[:50])
        if lowered in {"/help", "help", "帮助"}:
            return (
                "Commands: /files lists workspace files; "
                "/read <relative-path> reads a text file; "
                "/create <relative-path> [content] creates a file; "
                "/edit <path> <old> => <new> edits one unique fragment; "
                "/grep <pattern> [path] searches with a regular expression; "
                "/run <command> executes a command with a timeout; "
                "/help shows this message."
            )
        if lowered.startswith("/read "):
            file_path = task[6:].strip()
            result = self.tools.invoke(
                "file_read",
                task_name=task,
                file_path=file_path,
            )
            return str(result["content"])
        if lowered.startswith("/create "):
            arguments = task[8:].strip().split(maxsplit=1)
            if not arguments:
                raise ValueError("Usage: /create <relative-path> [content]")
            file_path = arguments[0]
            content = arguments[1] if len(arguments) > 1 else ""
            result = self.tools.invoke(
                "file_create",
                task_name=task,
                file_path=file_path,
                content=content,
            )
            return f"Created {result['path']} ({result['characters']} characters)."
        if lowered.startswith("/edit "):
            edit_request = task[6:].strip()
            try:
                file_path, replacement = edit_request.split(maxsplit=1)
                old_text, new_text = replacement.split("=>", maxsplit=1)
            except ValueError as exc:
                raise ValueError("Usage: /edit <path> <old-text> => <new-text>") from exc
            result = self.tools.invoke(
                "file_edit",
                task_name=task,
                file_path=file_path,
                old_text=old_text.strip(),
                new_text=new_text.strip(),
            )
            return f"Edited {result['path']} ({result['replacements']} replacement)."
        if lowered.startswith("/grep "):
            arguments = task[6:].strip().split(maxsplit=1)
            if not arguments:
                raise ValueError("Usage: /grep <pattern> [relative-path]")
            pattern = arguments[0]
            search_path = arguments[1] if len(arguments) > 1 else "."
            result = self.tools.invoke(
                "regex_search",
                task_name=task,
                pattern=pattern,
                path=search_path,
            )
            matches = result["matches"]
            if not matches:
                return "No matches found."
            return "\n".join(
                f"{item['path']}:{item['line']}:{item['column']}: {item['text']}"
                for item in matches
            )
        if lowered.startswith("/run "):
            command = task[5:].strip()
            result = self.tools.invoke(
                "command_run",
                task_name=task,
                command=command,
                timeout_seconds=30,
            )
            output = str(result.get("stdout") or result.get("stderr") or "").rstrip()
            if result["ok"]:
                return output or f"Command completed with exit code {result['exit_code']}."
            return output or str(result.get("error") or f"Command failed with exit code {result['exit_code']}.")
        return (
            f"Task received: {task}\n"
            f"Workspace: {self.workspace}\n"
            "TacoAgent is ready for a model backend to plan and execute this task."
        )
