$ErrorActionPreference = 'Stop'

$projectRoot = 'C:\Users\caiqiang\Desktop\hello_agents\TacoAgent'
$runId = 'scheduled10-20261008-2100'
$runDirectory = Join-Path $projectRoot "evals\results\swebench-smoke\$runId"
$logPath = Join-Path $runDirectory 'scheduled-task.log'

New-Item -ItemType Directory -Force -Path $runDirectory | Out-Null
Set-Location -LiteralPath $projectRoot
$env:HF_DATASETS_OFFLINE = '1'
$env:HF_HUB_OFFLINE = '1'
$env:LANGSMITH_TRACING = 'false'
$env:LANGCHAIN_TRACING_V2 = 'false'

Start-Transcript -LiteralPath $logPath -Append
try {
    python -m evals.swebench.run_evaluation `
        --mode single-agent `
        --limit 10 `
        --instance django__django-12708 `
        --instance django__django-12713 `
        --instance django__django-12741 `
        --instance django__django-12754 `
        --instance django__django-12774 `
        --instance django__django-12858 `
        --instance django__django-12965 `
        --instance django__django-13012 `
        --instance django__django-13023 `
        --instance django__django-13028 `
        --run-id $runId `
        --instance-timeout 600 `
        --max-attempts 3 `
        --memory-profile node-trimmed `
        --no-langsmith
    if ($LASTEXITCODE -ne 0) {
        throw "SWE-bench evaluation exited with code $LASTEXITCODE"
    }
}
finally {
    Stop-Transcript
}
