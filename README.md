# TacoAgent

## Documentation

- [Project summary (2026-09-01)](docs/TACOAGENT_PROJECT_SUMMARY_2026-09-01.md)
- [Single-agent SWE-bench Verified rerun (2026-09-01)](docs/SWE_BENCH_SINGLE_AGENT_RERUN_2026-09-01.md)
- [Workflow architecture and selection](docs/WORKFLOWS.md)
- [ReAct SWE-bench Verified smoke report (2026-08-28)](docs/SWE_BENCH_REACT_SMOKE_2026-08-28.md)
- [Single-agent SWE-bench Verified smoke report (2026-08-28)](docs/SWE_BENCH_SINGLE_AGENT_SMOKE_2026-08-28.md)
- [Full single-agent SWE-bench smoke without planner fallback (2026-08-29)](docs/SWE_BENCH_SINGLE_AGENT_FULL_2026-08-29.md)
- [Full single-agent SWE-bench smoke with 300-second budget (2026-08-29)](docs/SWE_BENCH_SINGLE_AGENT_FULL_300S_2026-08-29.md)
- [Single-agent convergence changes and SWE-bench rerun (2026-08-30)](docs/SWE_BENCH_SINGLE_AGENT_CONVERGENCE_2026-08-30.md)
- [Single-agent 10-minute SWE-bench experiment (2026-08-30)](docs/SWE_BENCH_SINGLE_AGENT_10MIN_2026-08-30.md)
- [Default 10-minute single-agent reproduction (2026-08-30)](docs/SWE_BENCH_SINGLE_AGENT_DEFAULT_10MIN_2026-08-30.md)
- [Verifier reliability improvements (2026-08-30)](docs/VERIFIER_IMPROVEMENTS_2026-08-30.md)
- [10-case SWE-bench verifier-v2 experiment (2026-08-30)](docs/SWE_BENCH_SINGLE_AGENT_VERIFIER_V2_10_CASES_2026-08-30.md)

TacoAgent is a compact starter project for building terminal agents with Typer
and Rich. It includes a workspace-scoped agent, one-shot task execution,
interactive chat, and a small test suite.

Normal tasks use the `single-agent` graph workflow by default. The CLI can also
select `multi-agent` or the lightweight `react` tool loop, and streams node,
tool, verification, and final-answer events while the selected workflow runs.

## Install

```bash
python -m pip install -e .
```

## Use

```bash
taco run "summarize this workspace"
taco chat
taco info
```

The model backend is configured through `.env` and created with
`langchain_openai.ChatOpenAI`; file and command operations remain scoped to the
selected workspace.

Future agent tools belong in `src/tacoagent/tools/`. Implement the `Tool`
protocol and register instances with `build_tool_registry()`.

Every registered invocation is audited as `tool_started`, `tool_succeeded`, or
`tool_failed`. The default agent writes JSON Lines records to
`.tacoagent/tool_calls.jsonl` inside its workspace.

The CLI uses `./workspace` by default. Every file path must be relative and is
resolved by `WorkspaceSandbox`; absolute paths and `..` escapes are rejected.

Use `/read <relative-path>` in chat or as a task to read a UTF-8 file. The read
tool also rejects symbolic links whose resolved targets leave the workspace.

Use `/create <relative-path> [content]` to create a new UTF-8 file. Creation is
restricted to the workspace and never overwrites an existing file.

Use `/edit <relative-path> <old-text> => <new-text>` for a focused edit. The old
fragment must occur exactly once; otherwise the tool fails without changing the file.

Use `/grep <pattern> [relative-path]` to run a Python regular-expression search
inside the workspace. Results include file path, line, column, match, and line text.

Use `/run <command>` to execute a command with the workspace as its working
directory. `CommandRunTool` defaults to 30 seconds and rejects timeouts above
300 seconds; timeout and non-zero exit results are logged as tool failures.

Use `build_structured_tools(workspace)` (or `create_tools`) to obtain a list of
LangChain `StructuredTool` instances ready for `model.bind_tools(...)`.

Copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and call
`tacoagent.create_model()`. The factory loads `.env` with `python-dotenv` and
returns `langchain_openai.ChatOpenAI`; `OPENAI_MODEL` defaults to `gpt-4o-mini`.
# TacoAgent
