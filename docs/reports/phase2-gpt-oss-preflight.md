# Groq GPT-OSS smoke preflight

This is a dry-run only. It does not send an inference request and gives no batch approval.
Use the existing operator PowerShell session with its Groq and Project 1 ingest keys. If those
variables are absent, use masked `Read-Host -AsSecureString` entry from
[phase2-operator-preflight.md](phase2-operator-preflight.md); never paste a key into chat.

The account remains the operator-confirmed Free plan, with 30 RPM / 1,000 RPD / 8,000 TPM /
200,000 TPD. Current daily consumption and reset time are unavailable in Organization Usage.
That makes a 20-request batch **NO-GO** until adequate headroom is verified. It does not by
itself prevent a single smoke request after separate explicit approval, but check Groq's Limits
page first for an active rate restriction.

In the same PowerShell session, with the endpoint, Project 1, fixed single-call caps, and Free
attestation from the Qwen preflight still configured:

```powershell
$env:LLMI_LIVE_MODELS = 'openai/gpt-oss-20b'
$env:LLMI_LIVE_PROVIDERS = 'groq'
$env:LLMI_LIVE_CONSENT = 'groq-free-plan-single-call'
$outputPath = 'docs/reports/phase2-gpt-oss-dry-run-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '.json'
try {
    New-Item -ItemType File -Path $outputPath -ErrorAction Stop | Out-Null
    $json = & sdk/.venv/Scripts/python -m demo_app.live_compare --provider groq --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001
    $runnerExit = $LASTEXITCODE
    $json | Set-Content -LiteralPath $outputPath -Encoding UTF8
    $result = $json | ConvertFrom-Json
    $json
    Write-Host ('Saved: ' + $outputPath)
    if ($runnerExit -ne 0 -or $result.mode -ne 'dry_run' -or
        $result.provider_request_state -ne 'not_sent' -or $result.execution_allowed -ne $true -or
        $result.environment.backend.ready -ne $true -or $result.environment.sdk_checkout_match -ne $true -or
        $result.refusals.Count -ne 0 -or $result.plan.model -ne 'openai/gpt-oss-20b' -or
        $result.comparison.project_id -ne 1) {
        throw 'Preflight failed. No request is authorized.'
    }
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

Report the saved filename and safe dry-run result. The assistant will inspect it before asking
for approval of one GPT-OSS smoke. No retries or automatic next calls.

## One GPT-OSS smoke execution (approval required first)

The operator dry-run at 2026-11-12 16:50 UTC passed: execution eligible, backend ready, SDK
matches, model/endpoint and Project 1 correct, no refusals, `not_sent`, one request, zero retries,
one concurrency, input bound 685 and 1,024 output cap. Result:
`phase2-gpt-oss-dry-run-20261112-165059.json`. This does not authorize a provider call.

Only after explicit approval in chat, run this once in the same PowerShell session. It performs
a fresh dry-run before sending exactly one `cmp-ticket-triage` request. The unique result marker
prevents a second launch from this block. Preserve all outcomes; timeout, interruption, nonzero
exit or uncertain send requires investigation and renewed approval. No batch is approved.

```powershell
$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$resultPath = "docs/reports/phase2-gpt-oss-smoke-$stamp.json"
$dryPath = "docs/reports/phase2-gpt-oss-smoke-dry-$stamp.json"
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
        $dry.refusals.Count -ne 0 -or $dry.plan.model -ne 'openai/gpt-oss-20b' -or
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

After an approved run, share the saved filename and exit code, never credentials.
