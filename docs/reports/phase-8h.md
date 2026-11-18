# Phase 8H — Live comparison expansion: verification, readiness and proposals

**Date:** 2026-11-12 UTC.

**Original local checkpoint:** `release/groq-first-observed-request` at `c0b4e5c` (the Groq execution release). At the continuation inspection, this report was untracked; tracked files and the index were clean. See §9 for the release reconciliation.

**`origin/main`:** `9545936` (PR #15 merge); its CI passed, 7/7.

**Original observation session (before the §9 release reconciliation):**
- no code, lock, evidence, `.env`, project or database change;
- 0 provider requests made by this session.

## 1. State and a process gap

| Fact | Evidence |
|---|---|
| `c0b4e5c` (Groq lock released) | **not pushed**: no PR, no CI run, not in `main` |
| Real Groq requests | two observed traces in project 1 (§2), before publication of the local release commit; stored telemetry alone does not prove the originating Git SHA |
| Planned order (`reports/phase-8g.md` §6) | CI green, then merge, then the request |

**Gap:** the requests ran before CI on the release commit. Before any further real request:
1. push the branch;
2. confirm 7/7 CI;
3. decide to merge, or revert the lock.

## 2. Real Groq traces (verified 2026-11-12)

Read from the local dashboard, which reads `GET /api/v1/...` server-side with its own token. No token was handled by this session. Project 1, default 24-hour window:

| Trace | Scenario (`prompt_version`) | Started (UTC) | Trace / LLM span | Tokens in / out (reasoning) | Status | Provenance | Cost |
|---|---|---|---|---|---|---|---|
| `60a48d470e21b8d36f288f2bfc9329fe` | `cmp-v1.ticket-triage` | 2026-11-12 12:01:25 | 532.1 ms / 286.9 ms | 222 / 96 (60) | no span error | real (asserted) | unknown (no price) |
| `a3b64d4430992defa205c5b93a101fd4` | `cmp-v1.json-aggregate` | 2026-11-12 12:12:42 | 872.3 ms / 627.2 ms | 244 / 363 (307) | no span error | real (asserted) | unknown (no price) |

**Both traces have:**
- root span `demo.live-compare` and one CLIENT span `chat openai/gpt-oss-20b`, attempt 1;
- requested model = response model = `openai/gpt-oss-20b`;
- 0 attributes dropped by the server; `llminsight.attrmap.v1`.

**Not verified here** (the runner prints these, but they are not stored):
- each run's local validation result;
- the runner's exit code.

**Independent check with the read API**, run in your own shell (the token is typed, never echoed):

```powershell
$s = Read-Host "LLMI_API_TOKEN" -AsSecureString
$env:LLMI_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)); Remove-Variable s
$h = @{ Authorization = "Bearer $env:LLMI_API_TOKEN" }; $b = "http://127.0.0.1:8010/api/v1"
$q = "project=1&from=2026-11-12T00:00:00Z"
Invoke-RestMethod "$b/traces/60a48d470e21b8d36f288f2bfc9329fe?project=1" -Headers $h | ConvertTo-Json -Depth 8
Invoke-RestMethod "$b/traces/a3b64d4430992defa205c5b93a101fd4?project=1" -Headers $h | ConvertTo-Json -Depth 8
Invoke-RestMethod "$b/traces?$q&provider=groq" -Headers $h | ConvertTo-Json -Depth 4
Invoke-RestMethod "$b/models/compare?$q" -Headers $h | ConvertTo-Json -Depth 6
Invoke-RestMethod "$b/overview?$q&prompt_version=cmp-v1.ticket-triage" -Headers $h | ConvertTo-Json -Depth 6
Remove-Item Env:LLMI_API_TOKEN
```

## 3. GO / NO-GO

| Provider | Verdict | Condition |
|---|---|---|
| **Groq** `openai/gpt-oss-20b` | **Conditional GO** for manual, separately authorized single requests | First CI 7/7 on the release commit (§1). Evidence valid until **2026-11-26 06:00 UTC**; your attestation must be at most 1 day old. Free Plan limits recorded: 30 RPM, 1000 RPD, 8000 TPM, 200000 TPD; one run uses about 1.7K tokens at most |
| **Gemini** `gemini-3.1-flash-lite` | **NO-GO** | AI Studio evidence still missing (Billing Tier, RPM/RPD/TPM, date). Two prepared, separate reviewed changes (evidence, then the switch) wait for it. Official pages verified 2026-11-12 |
| **OpenRouter** `google/gemma-4-26b-a4b-it:free` | **NO-GO**, release guard intact | R135 (§5) |

## 4. Manual single-request runs, all three pinned scenarios

The scenarios and prompt versions are unchanged:
- `cmp-ticket-triage` / `cmp-v1.ticket-triage` (done for Groq);
- `cmp-json-aggregate` / `cmp-v1.json-aggregate` (done for Groq);
- `cmp-release-summary` / `cmp-v1.release-summary` (not yet run).

All three share the 1024-token output cap.

**One run = one process = one authorization.** The runner has no batch, loop or retry option, and the one-request slot is per process. Never run several in a script, and never re-run after a failure, timeout or `possibly_sent`.

Groq, remaining scenario, from the repository root in a fresh PowerShell:

```powershell
cd C:\Users\PC\LLM-Insight-Development
$env:LLMI_PROVIDER_MODE = "real"; $env:LLMI_GROQ_MODE = "free-plan"; $env:LLMI_GROQ_PLAN = "free"
$env:LLMI_GROQ_PLAN_CHECKED_ON = Read-Host "UTC date you actually checked this account's Free Plan (yyyy-MM-dd)"
$env:LLMI_LIVE_PROVIDERS = "groq"; $env:LLMI_LIVE_MODELS = "openai/gpt-oss-20b"
$env:LLMI_LIVE_MAX_REQUESTS = "1"; $env:LLMI_LIVE_MAX_INPUT_TOKENS = "1024"; $env:LLMI_LIVE_MAX_OUTPUT_TOKENS = "1024"
$env:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = "http://127.0.0.1:8010/v1/traces"
$env:OTEL_SERVICE_NAME = "llm-insight-live-compare"; $env:LLMI_DASHBOARD_PROJECT_ID = "1"
# GROQ_API_KEY and OTEL_EXPORTER_OTLP_TRACES_HEADERS (X-Project-Key=<project 1 ingest key>): set without echoing
# Consent is an operator-controlled prerequisite; this block only performs the dry run.
try {
    $env:LLMI_LIVE_CONSENT = "groq-free-plan-single-call"
    sdk\.venv\Scripts\python -m demo_app.live_compare --provider groq --scenario cmp-release-summary --dashboard-url http://127.0.0.1:3001
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

Before executing: verify the merged release, matching installed SDK, current evidence and account attestation, Project 1 ingest key and loopback OTLP endpoint. Inspect the dry-run JSON: `execution_allowed=true`, `environment.backend.ready=true`, `environment.sdk_checkout_match=true`, and `refusals=[]` are all required. Then obtain explicit approval for **this single Groq release-summary call**. A successful dry run alone is not authorization.

Only after that approval, run this separate block once:

```powershell
try {
    $env:LLMI_LIVE_CONSENT = "groq-free-plan-single-call"
    sdk\.venv\Scripts\python -m demo_app.live_compare --provider groq --scenario cmp-release-summary --dashboard-url http://127.0.0.1:3001 --execute
} finally {
    Remove-Item Env:LLMI_LIVE_CONSENT -ErrorAction SilentlyContinue
}
```

Keep the one JSON line. Its `validation.status`, `provider.outcome`, `usage.tokens` and `correlation.trace_id` are the run record.

Gemini and OpenRouter use the same pattern with their own settings (`reports/phase-8d.md` §6), only after their releases.

## 5. OpenRouter R135 (investigated; stays NO-GO)

Re-read 2026-11-12 (`reports/phase-8d.md` §2–§3).

**Not documented:**
- what `max_price: 0` does for a `:free` model, and the status it returns;
- that a `:free` request can never be charged;
- which endpoints serve the free variant, and whether any is eligible under `data_collection: "deny"`.

The FAQ says a request whose routing matches no allowed provider fails with an error.

**Ways to resolve it.** Each one is a separate reviewed decision; none is made here:
1. **A written statement from OpenRouter support** on these points for the chosen model. This is the cleanest route, and would let `ZERO_PRICE_ROUTING_VERIFIED_AT` be released on evidence.
2. **Your reviewed acceptance that the zero cost is only checked after the request.** The guard already halts on any charge indicator, and the account has no credits. The FAQ also mentions negative balances, so this is a risk you would be accepting, not a guarantee.
3. **A different free model** whose endpoint pages publish a non-logging policy. This needs new evidence (7-day window).

Never relax `max_price`, enable fallbacks or substitute a non-free model.

## 6. Dashboard data-quality checklist (project 1)

Observed 2026-11-12 = ✓. To check after each new run = ☐.

| Check | Where | Expected | Now |
|---|---|---|---|
| One trace per run, root `demo.live-compare`, one CLIENT span | Traces → trace detail | 2 spans, attempt 1 | ✓ ×2 |
| Provenance | detail, Overview | "Real (asserted)", 0 synthetic in the window | ✓ |
| Models | detail, Compare | requested = response model; a mismatch would show as a failure | ✓ |
| Tokens | detail, Compare | provider-reported in/out; reasoning shown as a subset, never added | ✓ 222/96, 244/363 |
| Latency | Compare | p50 from the CLIENT span; p95 flagged low-sample (n < 20) | ✓ 286.9 ms, 627.2 ms |
| Cost | everywhere | "unknown (no price)", never $0 | ✓ |
| Dropped attributes | detail | 0 | ✓ |
| Content | detail | no prompt or answer stored | ✓ |
| Scenario separation | Compare | one row per `prompt_version` | ✓ 2 rows |
| No ranking | Compare | "Groups are not ranked" and the real-data note | ✓ |
| Trace mixing (project 1) | Overview | other project-1 data outside the window; widen the range only knowingly | ✓ none in 24 h |
| Cross-provider comparison | Compare | rows for more than one provider on the same `prompt_version` | ☐ needs Gemini or OpenRouter |

## 7. Trace → human review → approved dataset case (existing, Phase 4B / ADR-24)

1. **Trace:** open the trace detail. It shows "Human review: not reviewed yet" with a link to the review.
2. **Review:** create one with `POST /api/v1/failure-reviews` (dashboard form, ADR-22 write): an evidence reference to the trace, a hypothesis and a status. The server masks free text (`redaction.applied`).
3. **Decision:** `PATCH /api/v1/failure-reviews/{id}` with `hypothesis_supported`, or `hypothesis_rejected` plus an independently supplied `expected_output`. `pending` and `inconclusive` reviews cannot be converted. A cited review's decision is frozen (`409 review_decision_frozen`).
4. **Dataset case:**
   - `POST /api/v1/datasets` creates a dataset if needed;
   - `POST /api/v1/datasets/{id}/cases` adds the case with `approved: true` (the JSON boolean), `approved_by`, a non-empty `sanitized_input`, an optional `rule` (`llminsight.rule.v1`) or `expected_output`, and `origin_trace_id` with `origin_review_id`;
   - at most one case per review per dataset;
   - the input is masked by the server (`redaction_applied`).
5. **Content:** LLM Insight never stores prompts. For a comparison trace, the case input is supplied by the reviewer, for example the pinned scenario text, whose SHA-256 is in the runner output, after masking.

## 8. Proposal: real-provider experiments (not implemented)

**Today:** Experiments are mock-only (ADR-25, ADR-26):
- `mode: "real"` is validated, then refused (`409`);
- the triggers force `synthetic` provenance on results;
- the dashboard can only submit `mock`.

Nothing mock may be presented as real.

**Minimum separate change** (a new ADR and a security review):
1. **Runner in the demo app, not the backend.** An operator CLI (`demo_app.live_experiment`) keeps provider keys out of the backend. For each (case, prompt version) it makes one physical request through the existing guarded single-request path of that provider.
2. **Authorization by count.** The operator authorizes exactly N requests up front (N = cases × 2). The run is sequential, with zero retries, the existing per-request caps, and a stop at the first refusal, halt or `possibly_sent`. The per-process one-request slot would need an ADR-approved bounded generalization ("N authorized, never more"); otherwise each request stays a separate process.
3. **Evidence stays telemetry.** Each request is a normal real trace, with `prompt_version` = the variant and the case ID kept locally. The CLI validates answers with the dataset's `llminsight.rule.v1` rules locally and stores only rule codes; no model output is stored.
4. **Report from traces.** A read-only report joins the experiment's trace IDs. Alternatively, a migration lets `experiment_results` accept `real` provenance, with its own trigger, and is never mixed with mock runs.
5. **Cost** stays `unknown` for free providers. Quality is compared only on cases evaluated under both versions (ADR-25 rule), and no winner is named on incomplete evidence.

This is a proposal only; no code, schema or UI change is made in this phase.

## 9. Phase A — release reconciliation (2026-11-12 UTC)

The continuation inspected `git status`, `git branch -vv`, the last eight commits, both diffs, remotes and the complete `c0b4e5c` diff before making changes. No reset, branch switch, rebase or database action was needed.

- GitHub `main` independently resolved to `9545936bfcb3a4281cd0069a7917e96533fae10c`; it is the direct parent of local release commit `c0b4e5c07d8cf431e3e8bf59f0ac8cb19791e73d`. The release branch did not yet exist on GitHub and no PR was open at this inspection.
- Main CI [run 43206926043](https://github.com/MuratElberKayaa/LLM-Insight/actions/runs/43206926043) completed successfully: `docs`, `python`, `frontend`, `security-python`, `security-history`, `e2e`, `image-build` (7/7). This is **not** CI evidence for the release commit.
- The release diff has eight files: the Groq execution constant/comment, five test files covering released and explicitly locked paths, and two documents. Gemini and OpenRouter remain disabled. Scenario identities, prompts, hashes, validators, dependencies, workflows and database schema are unchanged.
- All installed SDK Python modules match `sdk/src/llm_insight_sdk` byte for byte; the installed package is non-editable in `sdk/.venv/Lib/site-packages`.
- Local checks completed at this review checkpoint: documentation consistency **800 PASS, 0 FAIL**; Ruff lint and format pass (67 files); strict mypy passes for SDK (16 source files) and demo app (11 source files); both changed documents have zero secret-pattern or exact-local-secret matches. The four unit-test files changed by the release passed **933 tests, 0 failures** (351.97 seconds), using the matching installed SDK. Database-backed tests are reserved for CI's isolated database; none are authorized against the developer database.
- This separate documentation change preserves the original trace evidence and process-gap record. The manual command now separates dry run from execution, requires all four readiness conditions and explicit per-call approval, and removes consent in `finally`. Account attestation dates must reflect an actual check, not an automatically substituted current date.

Test commands (from `sdk/`):

```powershell
.venv/Scripts/python -m pytest tests/unit/test_compare_runner.py tests/unit/test_gemini_execution_gate.py tests/unit/test_groq_adapter.py tests/unit/test_groq_live_guard.py -q -p no:cacheprovider --tb=short
```

The initial full `tests/unit` run was interrupted after fixture setup errors caused by `PermissionError: [WinError 5]` accessing the existing Windows `pytest-of-PC` temporary directory; **the full suite is not claimed to have passed in this continuation**. A focused reproduction of six Gemini content-runner temporary-directory tests failed at setup under the sandbox, then passed **6/6** with approved filesystem access. The 933-test release check above used that approved access and passed. The earlier report's 2461-test result remains historical evidence, not a fresh result.

**Release gate:** commit the reviewed documents, push this branch without force, open its PR, verify all seven jobs for its exact head, then request the operator's merge decision. Neither a push nor green CI authorizes a provider request. The environment permission request for `git commit` was rejected by the operator, so **no documentation commit, push or PR was created**. The prepared files are retained for review. Resume Git publication only after that restriction is resolved; release CI remains unverified.

**Independent local read-API recheck:** `/health/ready` returned HTTP 200 / `ready`. With Project 1 and the explicit UTC window `[2026-11-12, 2026-11-13)`, `/api/v1/traces`, both exact trace-detail endpoints from §2, `/api/v1/overview` and `/api/v1/models/compare` all returned HTTP 200. The real-only list contains exactly the two §2 trace IDs with no further page. Both have one root, one CLIENT child, a valid parent and usage-span link, attempt 1, zero dropped attributes, no span error and no truncated detail. Attribute keys contain metadata only, with no prompt or completion content fields. Each trace has zero human reviews; neither is currently evidence of a human-approved dataset case.

Overview reports two real inference attempts, 466 input tokens, 459 output tokens and 367 reasoning tokens as an output subset; two unknown-cost calls, `known_cost_usd=null`, and low-sample warnings. Compare has exactly the two pinned prompt versions from §2, one observation each, with model-call latency 286.9199 ms and 627.1528 ms. Provider/model identities, token counts and reasoning subsets match §2. Usage cost is `unknown` / `price_missing`, and `provenance_verified=false`. Root-span provenance is `unknown`, while each inference span, usage record and aggregate trace is asserted `real`. These read-only checks reconfirm storage and API behavior, not local runner validation or a fresh browser-rendering check.

**Next phase, not released here:** `docs/reports/phase-8f.md` does not yet exist. Both prepared Gemini patches were located in the previous session's scratchpad (`8f-patch1-gemini-evidence-refresh.patch` and `8f-patch2-gemini-execution-release.patch`); `git apply --check` succeeded separately for each against this checkout, but neither was applied. Patch 1 contains a prefilled `2026-11-12 06:00 UTC` evidence timestamp and a reference to the missing report: clean applicability is not evidence that this timestamp or account eligibility is valid. Gemini account/project Billing Tier, exact-model RPM/TPM/RPD and a legitimate UTC evidence check time remain prerequisites. A prior direct Gemini HTTP 200 is not one of the six target comparison SDK traces and does not establish Free Tier eligibility.

**Completion boundary:** the target currently has two independently reverified Groq comparison traces, not six. Their runner validation results remain unverified. Experiments remain deterministic and synthetic; this release implements no real experiment executor. No provider request, project creation, database mutation, secret-file edit or automatic merge was performed by this continuation. Continue in this session after Git publication is authorized and the merge decision is reached; preserve this report as the handoff if a new session is needed.
