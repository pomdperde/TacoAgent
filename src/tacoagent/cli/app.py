from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty

from tacoagent import __version__
from tacoagent.workflow import (
    stream_multi_agent_session_events,
    stream_react_session_events,
    stream_single_agent_session_events,
)


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
) -> None:
    try:
        if workflow_mode in {WorkflowMode.REACT}:
            events = stream_react_session_events(
                task, session_workspace=workspace, max_loops=max_attempts
            )
        elif workflow_mode == WorkflowMode.MULTI_AGENT:
            events = stream_multi_agent_session_events(
                task, session_workspace=workspace, max_attempts=max_attempts
            )
        else:
            events = stream_single_agent_session_events(
                task, session_workspace=workspace, max_attempts=max_attempts
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
    _run_and_render(task, workspace, max_attempts, workflow_mode)


@app.command()
def chat(
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
            _run_and_render(task, workspace, max_attempts, workflow_mode)


@app.command()
def info() -> None:
    """Show project information."""
    console.print(f"[bold yellow]TacoAgent[/] {__version__}")


def main() -> None:
    app()
