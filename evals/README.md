# TacoAgent DeepEval suite

Install and run:

```bash
python -m pip install -e ".[evaluation]"
python evals/run_deepeval.py
```

The default suite is offline and reproducible: a scripted model generates known
ReAct tool trajectories while TacoAgent executes its real StructuredTools. It
measures tool selection, artifact completion, workspace safety, latency, and
token efficiency through custom DeepEval `BaseMetric` implementations.

The JSON report is written to `evals/results/latest.json`. A configured
`OPENAI_API_KEY` is required for a separate real-model/LLM-judge benchmark.

## Node memory trimming A/B benchmark

Compare the legacy uniform memory snapshot with the current node-specific
profiles using the same deterministic long-context fixture:

```bash
python -m evals.run_memory_profile_ab --repeats 200
```

The command measures prompt-input tokens plus local memory construction and
JSON serialization time. It does not call a remote model, so it does not
measure generation tokens or provider/network latency. Results are written to
`evals/results/memory_profile_ab_latest.json`.

Run the configured model against real tools with:

```bash
python evals/run_live_deepeval.py
```

The live report is written to `evals/results/live_latest.json`.

## LangSmith end-to-end evaluation

Set these values in `.env` (the file is ignored by Git):

```dotenv
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=tacoagent
```

If the API key belongs to more than one LangSmith workspace, also set
`LANGSMITH_WORKSPACE_ID`. Register the versioned benchmark dataset once:

```bash
python -m evals.register_langsmith_dataset
```

Registration is idempotent: an existing dataset with the same name is reused.
Run the real end-to-end experiment with deterministic code evaluators for intent
routing, final-answer completion, required stream events, and workspace files:

```bash
python -m evals.run_langsmith_eval
```

To compare workflow architectures against the same examples:

```bash
python -m evals.run_langsmith_eval --mode multi-agent --experiment-prefix tacoagent-multi
python -m evals.run_langsmith_eval --mode react --experiment-prefix tacoagent-react
```

Each example runs in a fresh temporary workspace. The evaluation invokes the
configured LLM and may incur model and LangSmith usage costs.

## SWE-bench Verified smoke evaluation

Install the optional dataset and official harness dependencies, then generate
official-format patches for a deterministic subset of up to five instances:

```bash
python -m pip install -e ".[swebench]"
python -m evals.swebench.runner --mode single-agent --limit 5 --run-id single-v1
python -m evals.swebench.evaluate evals/results/swebench-smoke/single-v1/predictions.jsonl --run-id single-v1
```

See `evals/swebench/README.md` for workflow selection, resume behavior, output
artifacts, and benchmark-isolation notes.
