# Phase 8F — Gemini local preparation (execution remains locked)

> Historical preparation checkpoint. The operator subsequently authorized local activation without Git publication. Current state and credential-bearing preflight instructions: [local activation](phase-8f-local-activation.md). The earlier publication prerequisite below is superseded for that explicitly authorized local validation; runtime guards and individual-call approval remain required.

**Date:** 2026-11-12 UTC. **Status: NO-GO for real requests.**

The operator deferred commit, push, PR and merge to final publication. This changes the work order, not the release criteria: local Groq release `c0b4e5c` still has no verified release CI/merge, and the Gemini changes below are local and unreviewed. No Git publication or index mutation was performed in this continuation. The earlier staged Phase A documents are preserved.

## 1. Public evidence, independently checked

Checked at **2026-11-12 13:06:58 UTC**, using the official pages below. The old `ai.google.dev/gemini-api/docs/` URLs redirected to `aistudio.google.com/docs/`.

| Source | Observation | Boundary |
|---|---|---|
| [Model](https://aistudio.google.com/docs/models/gemini-3.1-flash-lite) | Stable model ID `gemini-3.1-flash-lite`; text output supported | No model substitution or scenario change |
| [Pricing](https://aistudio.google.com/docs/pricing#gemini-3.1-flash-lite) | Standard Free Tier lists input and output, including thinking, as free | Does not establish this account's tier or an actual billed amount; stored cost remains unknown |
| [Deprecations](https://aistudio.google.com/docs/deprecations) | Model released May 7, 2026; earliest shutdown May 7, 2027 | A scheduled date, not a guarantee beyond that date |
| [Rate limits](https://aistudio.google.com/docs/rate-limits) | Quotas are project/model/tier dependent; active limits are shown in AI Studio | Public documentation cannot attest account quotas |

`FREE_TIER_VERIFIED_AT` now records this actual **public-document check**, replacing the expired October 28 timestamp. The 14-day window ends **2026-11-26 13:06:58 UTC**. This timestamp is not an account attestation and does not enable execution.

## 2. Operator account evidence (separate from public evidence)

The operator supplied the display values `RPM 1/15`, `TPM 189/250K`, `RPD 3/500`, then confirmed the model, Billing Tier **Free**, and that the check was performed **during this session** on November 12. Confirmation recorded at **2026-11-12 13:10:13 UTC**; this is the recording time, not an invented exact screenshot timestamp.

| Account field | Operator report | Verification level |
|---|---|---|
| Model | `gemini-3.1-flash-lite` confirmed | Operator attestation |
| Billing Tier | Free | Operator attestation |
| RPM | 15 (displayed use: 1) | Operator-provided value |
| TPM | 250,000 (displayed use: 189) | Operator-provided value |
| RPD | 500 (displayed use: 3) | Operator-provided value |
| Check date | 2026-11-12 UTC, during this session | Operator confirmation; minute not separately supplied |

The numerator is usage, not the limit. These counters are only a snapshot and do not reserve quota. No screenshot or account backend was independently inspected. The key-to-project association must still be confirmed locally by the operator when entering the key; no credential was displayed, modified, saved or fetched from `.env`. The runtime attestation must remain at most one day old. No prior HTTP 200 is treated as proof of the Free Tier.

## 3. Separate, reviewable changes

**Evidence change, applied locally:** `gemini_live.py` updates the public timestamp/comment only. The evidence assertions in three unit-test files follow it. Future/stale/week-date fixtures derive from the evidence clock, and the three-provider integration fixture uses the same clock. The allowlisted model, 14-day evidence window, attestation requirement and all caps remain unchanged. The installed SDK was rebuilt from this checkout with the existing pinned build backend and `--no-deps`; runtime dependencies were not upgraded. All SDK Python source files match the non-editable installation byte for byte.

The standalone [evidence diff](../patches/phase-8f-evidence.patch) contains these five files, excluding Phase A documents. It is already represented in the working tree; do not apply it again.

**Execution change, NOT applied:** [proposed execution diff](../patches/phase-8f-execution-proposed.patch) contains only the Gemini constant and its gate tests. It is adapted from the previous session's patch against the current files. Its resulting Python syntax and patch applicability were checked; it is **not** a tested or approved execution release. `REAL_EXECUTION_ENABLED` remains **False** in the source and installed SDK. Apply only after evidence review, normal release validation and explicit approval of the execution change. Each call subsequently needs its own authorization.

The old evidence patch's prefilled `2026-11-12 06:00 UTC` and claim that account evidence was already documented were not reused. Both original patches remain untouched in the previous session's scratchpad.

**Preserved safeguards:** one request per process across comparison providers; one concurrent request; zero retries; 1024 input/output ceilings; no paid/model/provider fallback; local consent; metadata-only telemetry; fixed Developer API endpoint; credential binding; policy re-evaluation and independent reservation/send checks. Automatic continuation and function calling remain disabled. OpenRouter's switch remains False and R135's routing evidence remains absent. Pinned prompts, hashes and output validators are unchanged.

## 4. First Gemini dry run

Sanitized record: [phase-8f-dry-run.json](phase-8f-dry-run.json). Scenario: `cmp-ticket-triage`; dashboard `http://127.0.0.1:3001`; Project 1; configured OTLP endpoint `http://127.0.0.1:8010/v1/traces`.

The runner used the real current clock and unchanged guards, with the confirmed account date/tier and fixed bounds. No key file was loaded, no credential/consent was supplied by the assistant, and no `--execute` flag was used.

| Check | Result |
|---|---|
| Backend `/health/ready` | HTTP 200, ready |
| Installed SDK checkout match | true |
| Public evidence / reported tier-date policy | No stale-evidence or unverified-tier refusal |
| Ingest readiness | false: `telemetry_config_invalid`; the tool process lacks the operator's ingest configuration |
| Execution allowed | false |
| Refusals | `real_execution_disabled`, `consent_missing`, `api_key_missing`, `backend_not_ready` |
| Provider request state | `not_sent` |
| Runner exit | 0: dry-run reporting succeeded; this is not execution permission |

Backend health and ingest authorization are different checks. The runner's configured readiness check would send an empty OTLP body to the local backend, which creates no spans; it could not run here because telemetry configuration was incomplete. The tool process does not inherit variables entered later in the operator's separate PowerShell window.

**Independent release NO-GO:** the runner does not verify GitHub PR/CI/merge status. Even a future `execution_allowed=true` result cannot satisfy those external release requirements. No provider request is authorized while publication/release validation is deferred.

## 5. Operator-side dry run (no execution)

From the existing repository, enter credentials privately in your own PowerShell. `GEMINI_API_KEY` and `OTEL_EXPORTER_OTLP_TRACES_HEADERS` must already be set there, with the ingest header belonging to existing Project 1. Do not paste or print them. This block neither asks for nor sets a key, and does not supply consent:

```powershell
Set-Location C:\Users\PC\LLM-Insight-Development
$env:LLMI_PROVIDER_MODE = "real"
$env:LLMI_GEMINI_MODE = "free-tier"
$env:LLMI_GEMINI_BILLING_TIER = "free" # only while the verified project remains Free
$env:LLMI_GEMINI_TIER_CHECKED_ON = "2026-11-12" # actual attestation date; re-check if stale
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
sdk/.venv/Scripts/python -m demo_app.live_compare --provider gemini --scenario cmp-ticket-triage --dashboard-url http://127.0.0.1:3001
```

This should still refuse execution while the code switch is locked and consent is absent. Never add `--execute` to resolve a refusal. After the eventual release, separately authorize exactly one scenario only when `execution_allowed=true`, `environment.backend.ready=true`, `environment.sdk_checkout_match=true` and `refusals=[]`; clear consent in `finally`. A timeout, failure or `possibly_sent` needs investigation and renewed approval before another attempt.

## 6. Validation and remaining work

Validation completed:

- From `sdk/`: `.venv/Scripts/python -m pytest tests/unit/test_gemini_live_guard.py tests/unit/test_gemini_execution_gate.py tests/unit/test_compare_runner_gemini.py -q -p no:cacheprovider --tb=short`: **134 passed in 30.11 seconds**, no skipped or failed tests. Provider transport is synthetic and non-loopback networking is blocked by the runner/gate fixtures. Windows temporary-directory access required approved execution permissions, as in Phase A.
- Ruff check and format check: all five changed Python files pass.
- `sdk/.venv/Scripts/python -I scripts/verify_phase0.py --quiet`: **800 PASS, 0 FAIL**.
- `sdk/.venv/Scripts/python -m pip check`: no broken requirements.
- Installed SDK Python source comparison: zero mismatches; Gemini False, OpenRouter False.
- Proposed execution patch: applicability and Python syntax only; **not applied, execution candidate tests NOT RUN**.
- Database-backed integration tests and GitHub CI: **NOT RUN**; the integration fixture clock change still needs CI's isolated database.

No new provider request, trace, project, database, dataset approval or experiment is created here.

| Target scenario | Groq | Gemini |
|---|---|---|
| Ticket triage | Existing trace reverified in Phase A | Pending, NO-GO |
| JSON aggregate | Existing trace reverified in Phase A | Pending, NO-GO |
| Release summary | Pending, NO-GO | Pending, NO-GO |

Target progress remains **2/6 independently verified real comparison traces**. The prior direct Gemini call is not a comparison SDK trace. Phase A's API baseline remains valid evidence for the two existing records; this preparation does not claim a new browser verification. Datasets require a human review and explicit approval with independently supplied sanitized input. Experiments remain mock-only and synthetic. Costs remain unknown; one observation per scenario cannot establish model superiority.

Final publication checklist, explicitly deferred: review evidence and execution changes separately; commit/push/PR; verify all seven CI jobs for the actual release head; obtain merge approval and check installed SDK/release consistency; authorize each of the four remaining calls separately; independently verify each stored trace and dashboard; prepare the final comparison report; separately review the safe re-lock of enabled provider switches; final CI and local/main synchronization. This sequencing may block live work while Git publication is deferred; it is not silently waived.
