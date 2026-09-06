param(
    [ValidateSet("react", "single-agent")]
    [string]$Mode = "single-agent",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
python -m evals.swebench.run_evaluation --mode $Mode @ExtraArgs
exit $LASTEXITCODE
