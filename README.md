# TacoAgent

TacoAgent is a compact starter project for building terminal agents with Typer
and Rich. It includes a workspace-scoped agent, one-shot task execution,
interactive chat, and a small test suite.

Normal tasks run through a `ChatOpenAI.bind_tools()` ReAct loop. The CLI streams
tool arguments and results as they happen, reports each tool's duration, and
shows per-response plus accumulated token usage. Use `--max-loops` to bound the
number of model iterations.

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

The default backend is intentionally local and deterministic. Replace
`tacoagent.core.agent.TacoAgent._respond()` with an LLM/provider implementation while keeping the CLI
and session interfaces unchanged.

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
