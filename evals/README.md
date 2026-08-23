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

Run the configured model against real tools with:

```bash
python evals/run_live_deepeval.py
```

The live report is written to `evals/results/live_latest.json`.
