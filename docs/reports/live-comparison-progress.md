# Live comparison progress — local validation

**2026-11-12 UTC. In progress: 5 observed real traces independently verified: 4 completed responses and 1 provider error. Successful comparison coverage remains 4/6.**

## Gemini release-summary failure — do not retry automatically

The authorized request reached the provider once and returned `DEADLINE_EXCEEDED`; the stored CLIENT span has error type `504`. Runner duration 29797 ms; stored root duration 29794.4605 ms, CLIENT duration 29779.4255 ms. This is a failed real request, not a successful release summary or a local validation failure. Validation was not evaluated because the provider did not complete. Returned model and token counts are unknown; stored cost is unknown (`usage_missing`). No response content is available to assess.

Trace `6f7261b074977c1b8a4804d43b103add` was independently verified under Project 1: two spans, valid parent relationship, attempt 1, zero dropped attributes, asserted real provenance, error status. Export was confirmed. Overview shows 5 real traces and 1 error; Models Compare exposes the failed release-summary group with null tokens and unknown cost. Dashboard trace-detail HTTP 200 includes its error marker. Evidence: [runner result](gemini-release-summary-result.json), [backend/dashboard verification](gemini-release-summary-verification.json), checked 2026-11-12 15:45:34 UTC.

The Gemini release-summary single-call approval is **consumed**. Its result file must be retained. No retry, timeout increase, model change or prompt change is authorized. Provider-side root cause beyond the returned deadline error is not established. Any retry needs investigation and renewed explicit approval; the successful six-response target is not reached. Groq release-summary remains unattempted and requires its own preflight/approval. Earlier approval/preflight notes below are historical.

This is operator-authorized local development validation, not a published release. Git publication and CI are deferred. Gemini and Groq remain locally enabled while separately authorized validation is in progress; OpenRouter remains locked. Re-lock Gemini and Groq, reinstall the SDK and run targeted security tests after validation concludes.

## Verified observations

| Provider / model | Scenario | Trace ID | Root duration / inference span | Input / output tokens | Local validation | Cost |
|---|---|---|---|---|---|---|
| Groq / `openai/gpt-oss-20b` | `cmp-ticket-triage` | `60a48d470e21b8d36f288f2bfc9329fe` | 532.0515 / 286.9199 ms | 222 / 96 | Not available from stored telemetry | unknown |
| Groq / `openai/gpt-oss-20b` | `cmp-json-aggregate` | `a3b64d4430992defa205c5b93a101fd4` | 872.3130 / 627.1528 ms | 244 / 363 | Not available from stored telemetry | unknown |
| Gemini / `gemini-3.1-flash-lite` | `cmp-ticket-triage` | `1890d7a6199574d9e98b378255ecbd11` | 9450.4087 / 9436.5312 ms | 158 / 36 | passed (7 deterministic rules) | unknown |
| Gemini / `gemini-3.1-flash-lite` | `cmp-json-aggregate` | `c2072abb44af2a52c7aa1b31ddd64f78` | 7731.6461 / 7715.5063 ms | 189 / 50 | passed (8 deterministic rules) | unknown |

All four are asserted real, not independently verified provider provenance. Each has one inference attempt; no stored span error. Groq reasoning output counts are 60 and 307 (subsets of output, not additional tokens). Gemini reasoning output is unreported (`null`), not asserted zero.

For the Gemini ticket-triage request, the runner reports completion, one physical attempt, 9453 ms wrapper duration, validation passed and confirmed export of two spans with zero failures. Wrapper, root and inference durations measure different boundaries; the comparison uses the CLIENT inference span. The stored requested/returned model identities match. The trace's input SHA-256 matches the pinned ticket-triage scenario.

The single-call approvals for Gemini ticket-triage and JSON aggregate are both **consumed**. Do not rerun either. No approval has been given for the remaining two release-summary calls.

## Independent verification

**JSON aggregate, independently checked at 2026-11-12 15:40:43 UTC:** [runner metadata](gemini-json-aggregate-result.json), [read-API verification](gemini-json-aggregate-verification.json), [dashboard content checks](gemini-json-aggregate-dashboard-check.json). Runner completed once (7734 ms wrapper duration), all 8 local rules passed, both spans exported with zero failures. All 15 backend checks passed, including identities, tokens, provenance, cost, parent/usage linkage, metadata keys and no span errors or dropped attributes. The real-only Overview now has **4 traces, 813 input / 545 output tokens, four unknown-cost calls**. JSON-aggregate Compare shows both providers, one observation each, with low-sample/no-ranking notices. Trace list/detail and Models Compare render 189/50 tokens; Compare renders 7.72 s. Dashboard validation is server-rendered HTTP content, not browser interaction testing.

The ticket-triage evidence below is the earlier 3-trace checkpoint:

- [Runner metadata](gemini-ticket-triage-result.json).
- [Backend read-API verification](gemini-ticket-triage-verification.json), checked at `2026-11-12T13:32:21Z`: exact trace detail under Project 1, trace listing, Overview and Models Compare. All 15 recorded checks passed. Two spans, valid root/child and usage linkage, one attempt, correct prompt version, no dropped attributes, no error and only expected metadata attribute keys. No prompt or answer content is stored.
- [Dashboard response checks](gemini-ticket-triage-dashboard-check.json): Overview, Traces, exact trace detail and ticket-filtered Models Compare returned HTTP 200; server-rendered text includes the new model, asserted provenance and unknown cost. Overview/Compare retain low-sample warnings; Compare retains the no-ranking notice. This is an HTTP/server-rendered-content check, **not a browser interaction or visual-layout test**. No frontend dependencies were installed for this check.
- In the explicit UTC day window, real-only Overview now reports 3 traces, 624 input / 495 output tokens, 3 unknown-cost calls and no synthetic traces. Ticket-triage Compare contains one Gemini and one Groq observation.

One observation per provider/scenario cannot establish a generally faster or better model. Costs are unknown, not zero or inferred from Free Tier eligibility. The prior direct Gemini HTTP call is not counted as a comparison SDK trace. Experiments remain deterministic mock runs labelled synthetic; Datasets need genuine human review and explicit approval with independently supplied sanitized input.

## Remaining calls

**Gemini release-summary approval:** the operator explicitly approved one `cmp-release-summary` request after the accepted preflight below. The approval covers neither retries nor another scenario and remains unconsumed until execution is observed. Use `gemini-release-summary-result.json` as the pre-created result reservation; no real send is claimed yet.

**Gemini release-summary preflight accepted (2026-11-12 15:43:35 UTC):** `gemini-release-summary-dry-run.json`, modified at 15:42:44 UTC, passes execution permission, backend readiness, SDK match, empty refusals, current Free Tier evidence/attestation, exact provider/model, Project 1, prompt version and pinned input hash. Request state is not sent. Input bound 659, output ceiling 1024, one request/concurrency, zero retries, no paid fallback. No execution result file exists. A new single-call approval for this scenario is pending.

**Historical JSON aggregate single-call approval (now consumed):** following the accepted preflight, the operator explicitly approved one Gemini `cmp-json-aggregate` request. This authorization covers no retry or other scenario. It was consumed by the verified successful execution above. Execute only in the same configured operator PowerShell, reserve `gemini-json-aggregate-result.json` before the command and clear consent afterward. Never delete an existing result/empty reservation to retry.

**Historical JSON aggregate preflight accepted (2026-11-12 15:38:26 UTC):** `gemini-json-aggregate-dry-run.json` was produced at 15:38:02 UTC. Execution allowed, backend ready and SDK checkout match are true; refusals are empty; no request sent. Provider/model, Project 1, scenario/prompt version and pinned input hash match. Current Free Tier evidence and November 12 attestation, one request, zero retries, concurrency one, input bound 725 and output ceiling 1024. No JSON-aggregate execution artifact exists. The subsequent explicit approval was obtained and consumed by the successful call above.

1. Gemini `cmp-json-aggregate` — fresh operator-shell dry run, then new single-call approval.
2. Gemini `cmp-release-summary` — fresh dry run and separate approval.
3. Groq `cmp-release-summary` — fresh Groq policy/attestation check, dry run and separate approval.

No batch or automatic retry. Preserve any failure/timeout/possibly-sent result and investigate before requesting renewed authorization. After each success independently verify the stored trace. The final report, safe re-lock and final publication checklist remain outstanding.

Next preflight, in the same operator PowerShell with the current Gemini/Project 1 settings and privately entered keys (no provider send):

```powershell
try {
    $env:LLMI_LIVE_CONSENT = "gemini-free-tier-single-call"
    $dryRunJson = & sdk/.venv/Scripts/python -m demo_app.live_compare --provider gemini --scenario cmp-release-summary --dashboard-url http://127.0.0.1:3001
    if ($LASTEXITCODE -ne 0) { throw "Dry-run failed; do not execute." }
    $dryRunJson | Set-Content docs/reports/gemini-release-summary-dry-run.json -Encoding UTF8
    $dryRunJson
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

Inspect the resulting preflight before requesting a new single-call approval. Neither consumed approval authorizes release summary.
