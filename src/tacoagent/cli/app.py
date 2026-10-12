from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty

from tacoagent import __version__
from tacoagent.workflow import stream_session_events


app = typer.Typer(
    name="taco",
    no_args_is_help=True,
    add_completion=False,
    help="TacoAgent — a workspace-scoped LangGraph agent.",
)
console = Console()


class WorkflowMode(str, Enum):
    MULTI_AGENT = "multi-agent"
    SINGLE_AGENT = "single-agent"
    # Backward-compatible aliases for earlier CLI versions.
    GRAPH = "graph"
    REACT = "react"

def _run_and_render(
    task: str,
    workspace: Path,
    max_attempts: int,
    workflow_mode: WorkflowMode = WorkflowMode.SINGLE_AGENT,
    user_id: str | None = None,
    session_id: str | None = None,
) -> None:
    try:
        events = stream_session_events(
            task,
            session_workspace=workspace,
            workflow_mode=workflow_mode.value,
            max_attempts=max_attempts,
            **({"user_id": user_id, "session_id": session_id} if user_id is not None or session_id is not None else {}),
        )
        for event in events:
            event_type = event["type"]
            if event_type == "node_start":
                console.print(f"\n[bold magenta]▶ {event.get('node', 'node')}[/]")
            elif event_type == "node_output":
                console.print(
                    f"[bold magenta]● {event.get('node', 'node')} output[/]"
                )
                console.print(Pretty(event.get("output", {})))
            elif event_type == "plan_snapshot":
                console.print("\n[bold blue]📋 Planner[/]")
                console.print(str(event.get("plan_summary", "")))
                for todo in event.get("todos", []):
                    console.print(
                        f"  {todo.get('id', '?')} [{todo.get('status', 'pending')}] "
                        f"{todo.get('content', '')}"
                    )
            elif event_type == "ai_message":
                content = str(event.get("content", "")).strip()
                if content:
                    console.print(f"[dim]{content}[/]")
            elif event_type == "tool_call":
                console.print(f"\n[bold cyan]🔧 {event.get('name', 'tool')}[/]")
                console.print(Pretty(event.get("args", {})))
            elif event_type == "tool_result":
                result = event.get("result", {})
                ok = not isinstance(result, dict) or result.get("ok") is not False
                console.print(f"{'✅' if ok else '❌'} [bold]Tool result[/]")
                console.print(Pretty(result))
            elif event_type == "actor_summary":
                console.print("\n[bold yellow]🔧 Actor[/]")
                console.print(str(event.get("content", "")))
            elif event_type == "verifier_result":
                passed = bool(event.get("passed"))
                color = "green" if passed else "red"
                marker = "✅" if passed else "❌"
                console.print(
                    f"\n[bold {color}]{marker} Verifier — attempt "
                    f"{event.get('attempts', 0)}[/]"
                )
                if event.get("last_error"):
                    console.print(str(event["last_error"]))
                if event.get("results"):
                    console.print(Pretty(event["results"]))
            elif event_type == "final_answer":
                console.print(Panel(str(event.get("content", "")), title="🌮 TacoAgent"))
    except Exception as exc:
        console.print(f"[bold red]TacoAgent failed:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="Task for TacoAgent.")],
    user: Annotated[str | None, typer.Option("--user")] = None,
    session: Annotated[str | None, typer.Option("--session")] = None,
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace available to the agent."),
    ] = Path("workspace"),
    max_attempts: Annotated[
        int,
        typer.Option("--max-attempts", min=1, max=20, help="Maximum verification attempts."),
    ] = 3,
    workflow_mode: Annotated[
        WorkflowMode,
        typer.Option(
            "--workflow-mode",
            "--mode",
            help=(
                "Agent mode: multi-agent (supervisor/specialists/verifier) or "
                "single-agent (ReAct). Legacy graph/react values are also accepted."
            ),
        ),
    ] = WorkflowMode.SINGLE_AGENT,
) -> None:
    """Run a task in multi-agent or single-agent mode."""
    _run_and_render(task, workspace, max_attempts, workflow_mode, user, session)


@app.command()
def chat(
    user: Annotated[str | None, typer.Option("--user")] = None,
    session: Annotated[str | None, typer.Option("--session")] = None,
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace available to the agent."),
    ] = Path("workspace"),
    max_attempts: Annotated[
        int,
        typer.Option("--max-attempts", min=1, max=20, help="Maximum verification attempts per turn."),
    ] = 3,
    workflow_mode: Annotated[
        WorkflowMode,
        typer.Option(
            "--workflow-mode",
            "--mode",
            help="Agent mode for each turn: multi-agent or single-agent.",
        ),
    ] = WorkflowMode.SINGLE_AGENT,
) -> None:
    """Start a multi-turn terminal session; use /exit to leave."""
    console.print("[bold yellow]🌮 TacoAgent[/] — type /exit to quit")
    while True:
        try:
            task = console.input("[bold cyan]You> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if task.casefold() in {"/exit", "/quit", "exit", "quit"}:
            break
        if task:
            _run_and_render(task, workspace, max_attempts, workflow_mode, user, session)


@app.command("session-new")
def session_new(
    user: Annotated[str, typer.Option("--user")],
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("workspace"),
) -> None:
    """Create an isolated session copy for a local user identity."""
    from tacoagent.core.session_space import SessionSpace
    session_id, path = SessionSpace(workspace, user).create()
    console.print(f"Session: {session_id}\nWorkspace: {path}")


@app.command("session-list")
def session_list(
    user: Annotated[str, typer.Option("--user")],
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("workspace"),
) -> None:
    """List sessions belonging to this local user identity."""
    from tacoagent.core.session_space import SessionSpace
    for session_id in SessionSpace(workspace, user).list():
        console.print(session_id)


@app.command()
def info() -> None:
    """Show project information."""
    console.print(f"[bold yellow]TacoAgent[/] {__version__}")


@app.command()
def rollback(
    change_id: Annotated[str | None, typer.Argument(help="Change ID to undo; omit to list changes.")] = None,
    workspace: Annotated[Path, typer.Option("--workspace", "-w")] = Path("workspace"),
    user: Annotated[str | None, typer.Option("--user")] = None,
    session: Annotated[str | None, typer.Option("--session")] = None,
) -> None:
    """List file changes or undo one change after checking its current version."""
    from tacoagent.core.file_journal import FileJournal
    from tacoagent.core.session_space import SessionSpace, workspace_lease

    try:
        if (user is None) != (session is None):
            raise ValueError("--user and --session must be provided together")
        target = SessionSpace(workspace, user).open(session) if user is not None else workspace.resolve()
        with workspace_lease(target):
            journal = FileJournal(target)
            if change_id is None:
                for record in journal.entries():
                    console.print({key: record[key] for key in ("id", "path", "operation", "status")})
            else:
                result = journal.rollback(change_id)
                console.print(Pretty(result))
                if not result["ok"]:
                    raise typer.Exit(code=1)
    except (OSError, ValueError, RuntimeError) as exc:
        console.print(f"Rollback failed: {exc}")
        raise typer.Exit(code=1) from exc


def main() -> None:
    app()
