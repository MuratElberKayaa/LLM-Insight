# Gemini — operator-authorized local activation

> Current checkpoint: the approved Gemini ticket-triage call completed and was independently verified as trace `1890d7a6199574d9e98b378255ecbd11`. Its authorization is consumed; do not rerun the historical execution block below. Progress is **3/6**; see [live comparison progress](live-comparison-progress.md). The remaining scenarios require new preflights and separate approvals.

**2026-11-12 UTC. Local development validation only; not a published release.**

The operator explicitly authorized local activation while deferring GitHub CI, commit, push, PR and merge. This supersedes the publication prerequisite in the earlier Phase 8F preparation report for this local validation only. It does not waive runtime policy, per-call approval, privacy, credential, endpoint or usage limits. The operator subsequently approved **one Gemini `cmp-ticket-triage` request**, conditional on all dry-run acceptance checks passing. That authorization is unconsumed: no real call has been sent. It does not cover another scenario, a retry or a second attempt.

## Applied change and verification

- Reviewed `docs/patches/phase-8f-execution-proposed.patch` against the existing working tree and preserved prior work. Applied the Gemini execution constant and corresponding gate-test changes; comments clarify the authorized local scope and remove obsolete locked-state wording. No Git index/publication mutation.
- Source and installed SDK: Gemini **True**, Groq **True** (unchanged), OpenRouter **False** (unchanged). The old proposal patch is now historical: do not apply it again.
- Public evidence checked earlier this session at `2026-11-12T13:06:58Z`, expires `2026-11-26T13:06:58Z`. Account attestation is dated November 12: Free, 15 RPM / 250,000 TPM / 500 RPD. No timestamp was renewed merely to make a gate pass.
- Preserved: one request, zero retries, concurrency one, input/output caps of 1024 tokens, explicit consent, no paid fallback, fixed endpoint and credential binding, independent send/reservation checks, metadata-only telemetry. Scenarios and validators unchanged.
- Reinstalled the non-editable SDK from the actual working tree using pinned build dependencies and `--no-deps`. All SDK Python modules match checkout byte for byte; `pip check` passes.
- Targeted offline tests: **134 passed in 30.09 seconds**, covering Gemini policy, independent execution gate and comparison runner. Locked-path tests explicitly restore False; released-state test checks the actual True constant. No live provider transport is used.
- Ruff lint and format pass for both activation files. Database-backed tests and GitHub CI were not run; no developer database mutation.

## Agent-process dry run after activation

Record: [phase-8f-local-activation-dry-run.json](phase-8f-local-activation-dry-run.json).

| Check | Observed result |
|---|---|
| Backend health | HTTP 200, ready |
| Installed SDK checkout | match = true |
| Model / project | `gemini-3.1-flash-lite` / `1` |
| Configured OTLP endpoint | `http://127.0.0.1:8010/v1/traces` |
| Ingest readiness | false, `telemetry_config_invalid` |
| execution_allowed | false |
| Refusals | `consent_missing`, `api_key_missing`, `backend_not_ready` |
| Request state | `not_sent` |

The previous `real_execution_disabled` refusal is gone. Actual credential readiness remains unverified: the tool process does not inherit credentials entered in a separate operator PowerShell. Neither `.env` nor provider/ingest keys were displayed, edited or loaded by this dry run. The selected ingest key's Project 1 association must be confirmed locally by the operator; the runner labels its project ID `operator_env_unverified`.

## Operator PowerShell: credential-bearing dry run

Use the **same PowerShell window** where you privately set `GEMINI_API_KEY` and `OTEL_EXPORTER_OTLP_TRACES_HEADERS`. The latter must contain the ingest header for existing Project 1. Do not share either value. The commands below do not read or modify `.env` or either credential. They are interactive commands, not a `.ps1` script; Windows' execution policy is left unchanged.

```powershell
Set-Location C:\Users\PC\LLM-Insight-Development
$env:LLMI_PROVIDER_MODE = "real"
$env:LLMI_GEMINI_MODE = "free-tier"
$env:LLMI_GEMINI_BILLING_TIER = "free"
$env:LLMI_GEMINI_TIER_CHECKED_ON = "2026-11-12" # Actual confirmed date; re-check if stale.
$env:LLMI_LIVE_PROVIDERS = "gcp.gemini"
$env:LLMI_LIVE_MODELS = "gemini-3.1-flash-lite"
$env:LLMI_LIVE_MAX_REQUESTS = "1"
$env:LLMI_LIVE_MAX_INPUT_TOKENS = "1024"
$env:LLMI_LIVE_MAX_OUTPUT_TOKENS = "1024"
$env:LLMI_LIVE_MAX_RETRIES = "0"
$env:LLMI_LIVE_MAX_CONCURRENCY = "1"
$env:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = "http://127.0.0.1:8010/v1/traces"
$env:OTEL_SERVICE_NAME = "llm-insight-live-compare"
$env:LLMI_DASHBOARD_PROJECT_ID = "1"
try {
    # This block only authorizes and performs preflight. It contains no --execute.
    $env:LLMI_LIVE_CONSENT = "gemini-free-tier-single-call"
    $dryRunJson = & sdk/.venv/Scripts/python -m demo_app.live_compare --provider gemini --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001
    $dryRunExit = $LASTEXITCODE
    if ($dryRunExit -ne 0) { throw "Dry-run failed (exit $dryRunExit); do not execute." }
    $dryRun = $dryRunJson | ConvertFrom-Json
    if ($dryRun.mode -ne "dry_run" -or $dryRun.provider_request_state -ne "not_sent") {
        throw "Unexpected runner result; stop and investigate."
    }
    $dryRunJson | Set-Content -LiteralPath docs/reports/phase-8f-operator-dry-run.json -Encoding UTF8
    $dryRunJson
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

The result file is sanitized runner metadata, not a credential file. A successful runner exit only means the dry-run report was produced. Before any real request, all of these must pass: `execution_allowed=true`, `environment.backend.ready=true`, `environment.sdk_checkout_match=true`, `refusals=[]`, correct model/project/endpoint, valid evidence and operator-confirmed key association. Ingest readiness uses an empty local OTLP request, not a provider request or new trace. No provider endpoint is contacted in dry-run mode; a key's acceptance by Gemini cannot be independently proven without a separately authorized real request.

Tell the assistant when this sanitized result file is ready. It can inspect the file without reading your shell credentials. The subsequent single-call approval for **one Gemini ticket-triage request** has been received and remains conditional on a passing preflight; do not request that approval again merely because the preflight was initially incomplete. Do not add `--execute` while preflight fails. On the latest inspection the operator result file was still absent, and the agent's earlier result still reported `execution_allowed=false` with missing consent/API key/ingest configuration.

## Remaining work and shutdown

**Preflight accepted, 2026-11-12 13:29:45 UTC:** the operator result file, modified at `13:29:22 UTC`, reports `execution_allowed=true`, `environment.backend.ready=true`, `environment.sdk_checkout_match=true`, `refusals=[]`, `mode=dry_run`, `provider_request_sent=false`, and `provider_request_state=not_sent`. Model `gemini-3.1-flash-lite`, provider `gcp.gemini`, Project 1, scenario `cmp-ticket-triage` and pinned input SHA-256 `5d9040f3a67d37e5edaf4dab90a9a59ff236061c729c7b62dcd0a39812c267ff` match. The November 12 account attestation/public evidence are current. The existing single-call authorization may now be used for this scenario only in the same operator PowerShell session. No actual send is claimed until the execution result is inspected.

Run the following block **once**, in that same configured shell. It reserves the output file before execution so accidentally re-running the block refuses before a second send. Never delete that marker to retry; an empty file or missing result after a launch means the outcome needs investigation.

```powershell
$resultPath = "C:\Users\PC\LLM-Insight-Development\docs\reports\gemini-ticket-triage-result.json"
try {
    New-Item -ItemType File -Path $resultPath -ErrorAction Stop | Out-Null
    $env:LLMI_LIVE_CONSENT = "gemini-free-tier-single-call"
    $result = & C:/Users/PC/LLM-Insight-Development/sdk/.venv/Scripts/python -m demo_app.live_compare --provider gemini --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001 --execute
    $callExit = $LASTEXITCODE
    $result | Set-Content -LiteralPath $resultPath -Encoding UTF8
    $result
    Write-Host "Runner exit code: $callExit. Do not repeat this block."
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

The command runs from the existing repository working directory used for preflight. Preserve nonzero exits and failure results as evidence too. No next scenario or automatic retry is authorized by this block.

**Earlier operator preflight (superseded by the accepted result above):** the supplied PowerShell transcript shows `environment.backend.ready=true`, `environment.sdk_checkout_match=true`, the intended model and Project 1, and only `api_key_missing` for `GEMINI_API_KEY`. `execution_allowed=false` and `provider_request_state=not_sent`. This supersedes the earlier missing-ingest/consent diagnosis for the operator's shell. Set the Gemini key privately in that same PowerShell process and repeat the dry run; the unconsumed single-call approval remains conditional on all checks passing.

Existing Groq ticket-triage and JSON-aggregate traces are retained, not recreated. Target remains **2/6**. Pending: three Gemini scenarios and Groq release-summary, each requiring its own fresh preflight and explicit approval. No batches/retries; timeout or `possibly_sent` requires investigation and renewed authorization. Verify each new trace via backend read API and dashboard, retaining unknown costs and asserted provenance.

After authorized validation completes, separately set Gemini and Groq execution constants back to False, update their shipped-state tests, reinstall the SDK and run the targeted security tests. Keep OpenRouter locked. Record actual trace results, mock-only Experiments and human-approved Datasets limitations in the final report. Git publication, CI and final main synchronization remain deferred and must not be represented as completed.
