# Phase 2 operator preflight (no inference)

Use this only after the Phase 1 offline checks in [two-phase-integration.md](two-phase-integration.md)
pass. First model: Groq `qwen/qwen3.8-27b`; scenario: `cmp-ticket-triage`. This is a **new smoke
preflight**, not a replay of an old request. The block contains no `--execute` and makes no
provider API request. It does not approve a smoke or a batch.

Paste into the existing repository PowerShell where you enter credentials. Input is masked;
values remain in that shell only. No `.env` changes. Confirm the ingest key belongs to **Project 1**.
If the correct Groq and ingest values are already present, the block does not replace them.

```powershell
Set-Location C:\Users\PC\LLM-Insight-Development
if (-not $env:GROQ_API_KEY) {
    $env:GROQ_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Groq API key' -AsSecureString)).Password
}
if (-not $env:OTEL_EXPORTER_OTLP_TRACES_HEADERS) {
    $env:OTEL_EXPORTER_OTLP_TRACES_HEADERS = 'X-Project-Key=' + [System.Net.NetworkCredential]::new('', (Read-Host 'Project 1 ingest key' -AsSecureString)).Password
}
$env:LLMI_PROVIDER_MODE = 'real'
$env:LLMI_GROQ_MODE = 'free-plan'
$env:LLMI_GROQ_PLAN = 'free'
# This date attests the Billing/limits check you just performed. Recheck if using on another day.
$env:LLMI_GROQ_PLAN_CHECKED_ON = [DateTime]::UtcNow.ToString('yyyy-MM-dd')
$env:LLMI_LIVE_PROVIDERS = 'groq'
$env:LLMI_LIVE_MODELS = 'qwen/qwen3.8-27b'
$env:LLMI_LIVE_MAX_REQUESTS = '1'
$env:LLMI_LIVE_MAX_RETRIES = '0'
$env:LLMI_LIVE_MAX_CONCURRENCY = '1'
$env:LLMI_LIVE_MAX_INPUT_TOKENS = '1024'
$env:LLMI_LIVE_MAX_OUTPUT_TOKENS = '1024'
# Remove a previous Gemini-specific override; Groq uses its fixed audited deadline.
Remove-Item Env:LLMI_LIVE_TIMEOUT_SECONDS -ErrorAction SilentlyContinue
$env:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = 'http://127.0.0.1:8010/v1/traces'
$env:OTEL_SERVICE_NAME = 'llm-insight-live-compare'
$env:LLMI_DASHBOARD_PROJECT_ID = '1'
$outputPath = 'docs/reports/phase2-qwen-dry-run-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '.json'
try {
    New-Item -ItemType File -Path $outputPath -ErrorAction Stop | Out-Null
    $env:LLMI_LIVE_CONSENT = 'groq-free-plan-single-call'
    $json = & sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001
    $runnerExit = $LASTEXITCODE
    $json | Set-Content -LiteralPath $outputPath -Encoding UTF8
    if ($runnerExit -ne 0) { throw 'Dry-run failed. Do not execute.' }
    $result = $json | ConvertFrom-Json
    $json
    Write-Host ('Saved: ' + $outputPath)
    if ($result.mode -ne 'dry_run' -or $result.provider_request_state -ne 'not_sent' -or
        $result.execution_allowed -ne $true -or $result.environment.backend.ready -ne $true -or
        $result.environment.sdk_checkout_match -ne $true -or $result.refusals.Count -ne 0 -or
        $result.plan.model -ne 'qwen/qwen3.8-27b' -or $result.comparison.project_id -ne 1) {
        throw 'Preflight did not pass all gates. No real request is authorized.'
    }
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

Report that the saved file is ready. Share no key values. The assistant will inspect the safe
metadata, then request approval of exactly one Qwen smoke if all gates pass. No batch approval
is implied. Other providers receive their own preflight and one-call smoke approval later.
For a future batch, also provide current shared daily usage/reset and split-token limits as
described by the quota template. No daily usage value has been guessed.

## One Qwen smoke execution (approval required first)

The 2026-11-12 16:37 UTC dry-run passed: execution eligible, backend ready, SDK matches,
model/Qwen endpoint and Project 1 correct, no refusals, `not_sent`, one request, zero retries,
one concurrency, input bound 685 and 1,024 output cap. Its exact metadata is saved in
`phase2-qwen-dry-run-20261112-163738.json`. This authorizes **no provider call by itself**.

Only after the assistant explicitly approves this one smoke request in chat, run the following
once in the same PowerShell session. It repeats the dry-run immediately before the send and
compares all gates. A timestamped result marker must be new; never delete it or rerun after
timeout, interruption, nonzero exit, `possibly_sent`, or missing/uncertain result. That case
requires investigation and renewed approval. The actual call is exactly one Qwen request for
`cmp-ticket-triage`, with no batch authorization.

```powershell
$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$resultPath = "docs/reports/phase2-qwen-smoke-$stamp.json"
$dryPath = "docs/reports/phase2-qwen-smoke-dry-$stamp.json"
try {
    New-Item -ItemType File -Path $resultPath -ErrorAction Stop | Out-Null
    $env:LLMI_LIVE_CONSENT = 'groq-free-plan-single-call'
    $dryText = & sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001
    $dryExit = $LASTEXITCODE
    $dryText | Set-Content -LiteralPath $dryPath -Encoding UTF8
    if ($dryExit -ne 0) { throw 'Fresh dry-run failed. No request was sent.' }
    $dry = $dryText | ConvertFrom-Json
    if ($dry.mode -ne 'dry_run' -or $dry.execution_allowed -ne $true -or
        $dry.environment.backend.ready -ne $true -or $dry.environment.sdk_checkout_match -ne $true -or
        $dry.refusals.Count -ne 0 -or $dry.plan.model -ne 'qwen/qwen3.8-27b' -or
        $dry.scenario.id -ne 'cmp-ticket-triage' -or $dry.comparison.project_id -ne 1 -or
        $dry.provider_request_state -ne 'not_sent') {
        throw 'Fresh preflight failed. No request was sent.'
    }
    $resultText = & sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001 --execute
    $resultExit = $LASTEXITCODE
    $resultText | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Host ('Saved smoke result: ' + $resultPath)
    Write-Host ('Runner exit code: ' + $resultExit + '. Do not repeat this block.')
    $resultText
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

The block is deliberately separated from the dry-run block. Do not run it until the specific
smoke approval has been given in chat. Report the saved filenames and runner exit code; never
share credentials.
