# TacoAgent SWE-bench Verified smoke runner

## One-command evaluation

Run up to ten instances, write a consolidated local JSON report, and upload the
dataset/results to LangSmith:

```powershell
.\run_swebench.ps1 -Mode single-agent --limit 5
.\run_swebench.ps1 -Mode react --limit 5
```

The cross-platform Python entry point is equivalent:

```bash
python -m evals.swebench.run_evaluation --mode single-agent --limit 5
```

The consolidated report is written to
`evals/results/swebench-smoke/{run-id}/evaluation_report.json`. Full patches
remain in `predictions.jsonl`; the report stores patch lengths and SHA-256
hashes alongside all local result records. LangSmith upload is enabled by
default and can be disabled with `--no-langsmith`.

Add `--official` to invoke the official Docker harness after patch generation.
Docker must be installed and running; harness stdout/stderr and exit status are
captured in the local JSON report.

Install the optional benchmark dependencies and ensure Docker is running:

```bash
python -m pip install -e ".[swebench]"
```

Generate patches for five deterministic Verified instances:

```bash
python -m evals.swebench.runner --mode single-agent --limit 5 --run-id single-v1

Use `--limit 10` for the ten-instance extended smoke suite.
```

Use `--mode multi-agent` or `--mode react` to compare workflows. Explicit IDs
can be supplied with repeated `--instance` options. Each instance gets a fresh
repository and no session workflow is used, preventing cross-instance memory.
Interrupted runs can continue with the same arguments plus `--resume`.
The default `--instance-timeout 600` enforces a ten-minute hard wall-clock budget
per instance while preserving partial event logs and patches.

Compare memory profiles on exactly the same instance IDs and budgets:

```bash
python -m evals.swebench.run_evaluation --mode single-agent --instance astropy__astropy-13033 --memory-profile legacy-uniform --run-id memory-legacy --no-langsmith
python -m evals.swebench.run_evaluation --mode single-agent --instance astropy__astropy-13033 --memory-profile node-trimmed --run-id memory-trimmed --no-langsmith
```

Each result's `metadata.token_usage` contains provider-reported input, output,
and total tokens. The report aggregates these under `metrics.token_usage`.
Each result also records `policy_block_count`, `policy_reprompt_count`,
`blocked_edits`, and `provider_wait_seconds`. The consolidated report aggregates
their counts and preserves blocked-edit details with the corresponding instance ID.
Only compare paired instances that reached equivalent workflow stages; setup
failures and model calls that return no usage must be excluded.

Outputs include `predictions.jsonl` in the official SWE-bench format,
`results.jsonl`, per-instance event streams, workspaces, and `summary.json`.
TacoAgent runtime files are excluded from generated patches.

Grade the predictions with the official Docker harness:

```bash
python -m evals.swebench.evaluate \
  evals/results/swebench-smoke/single-v1/predictions.jsonl \
  --run-id single-v1 --workers 1
```

Register a completed smoke run, including issue inputs, generated patches, and
local execution metrics, as a LangSmith dataset:

```bash
python -m evals.swebench.register_langsmith \
  evals/results/swebench-smoke/single-v1 \
  --dataset "TacoAgent SWE-bench Verified smoke single v1"
```

Web search is not exposed by the single-agent and ReAct tool registries. The
runner also disables the search-agent handoff in multi-agent mode. Command tools
still inherit the host's network access, so use a network-isolated container
when strict benchmark isolation is required.
