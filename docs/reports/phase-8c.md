# Phase 8C — Three-provider real comparison: readiness and preflight

**Date:** 2026-11-11 UTC. **Branch:** `feature/three-provider-readiness`, from `origin/main` at `e665877` (PR #11 merge), with upstream tracking removed; not pushed.

**Verdict:**
- **Offline implementation:** done. Gemini joined the comparison runner (ADR-49), and both R123 findings were fixed, for review.
- **Real requests:** **NO-GO for all three providers** (§F).
- **Provider requests made:** 0. No credential, environment file, developer database or Docker resource was read or changed.

## 0. Reconciled state

| Fact | Evidence (2026-11-11) |
|---|---|
| PR #11 | merged 20:21:36Z, head `26dea68`, merge commit `e665877` = `origin/main` |
| PR #11 CI | PR run 43095553239 and post-merge `main` run 43095567296 were both **queued, no job started**, when last read. **Not verified** |
| Worktrees | the owner's tree unchanged (`feature/phase-8a6-comparison-dashboard` at `980dfc6`, with its uncommitted 8A-7 files and three untracked user files); earlier scratch worktrees unchanged |
| Shipped locks (code on `e665877`) | Groq: `REAL_EXECUTION_ENABLED = False`, `FREE_PLAN_MODELS = {}`, evidence None. OpenRouter: `REAL_EXECUTION_ENABLED = False`, `FREE_MODELS = {}`, both evidence dates None. Gemini: **no execution flag**; its evidence `FREE_TIER_VERIFIED_AT = 2026-10-28 13:00 UTC` + 14 days **expired 2026-11-11 13:00 UTC** (`free_tier_evidence_stale`) |

## 1. What changed

| Change | Files |
|---|---|
| **R123 (1):** the Gemini attestation date must be `YYYY-MM-DD`. The ISO week date `2026-W44-3` (= the tests' "today") and seven other forms are refused | `sdk/src/llm_insight_sdk/gemini_live.py` |
| **R123 (2):** `SSLKEYLOGFILE`, `SSL_CERT_FILE` and `SSL_CERT_DIR` are refused by the policy. The runner client's TLS context never reads the environment. The client check refuses key logging in the sending, async and websocket contexts | `gemini_live.py`, `sdk/src/llm_insight_sdk/gemini_adapter.py`, `demo_app/live_gemini.py` |
| **Found while fixing:** google-genai 2.30.1 builds an async client and websocket TLS arguments of its own from the environment unless they are given (R141). The runner now gives its own contexts for all of them | `demo_app/live_gemini.py` |
| Gemini in `demo_app.live_compare` through the unchanged `live_gemini.run_single_request`: exactly one model, policy re-read immediately before, the shared one-request process slot, request states from the outcome only, and validation of non-thought text only. Lazy import, so Groq and OpenRouter runs never load google-genai | `demo_app/live_compare.py` |
| ADR-49; R123 fixed; R135 and R136 updated; R140 and R141 added | `docs/ADR.md`, `docs/SOURCES_AND_RISKS.md`, `SECURITY.md` §6, `README.md`, `sdk/README.md` |

## 2. Verification (local; nothing here is GitHub CI)

| Check | Result |
|---|---|
| R123 regressions and the Gemini unit suites (`test_gemini_live_guard`, `_endpoint`, `_runner`, `_adapter`, `_content_runner`, `_content_preview`, `_scenarios`) | **559 passed** before the runner change; included in the full run below |
| Gemini comparison runner (`test_compare_runner_gemini.py`, new) | **25 passed** |
| Groq and OpenRouter runner suites, with the registry tests updated for ADR-49 | **104 passed** |
| Three providers, offline end to end against the OTLP stub (same steps as the new PostgreSQL test) | Groq, Gemini and OpenRouter each exit 0, `sent`, validation `passed`, export `confirmed`; 3 SYNTHETIC requests, 0 network attempts; three client spans with `cmp-v1.ticket-triage` |
| `test_live_compare_three_providers.py` (PostgreSQL) | 1 database-free test passed; **1 database test NOT RUN locally**; it needs the CI `python` job |
| Full SDK suite (unit and integration) on a non-editable install, no database | **2472 passed, 0 failed, 112 skipped** (all skips are database-backed integration tests, NOT RUN) |
| Ruff check and format; strict mypy (`src`, `demo_app`) | clean; no issues (16 and 11 files) |
| Bandit baseline gate | **PASS, 44 = 44** |
| Docs (`verify_phase0.py`) | **799 PASS, 0 FAIL** |
| Secrets | the 11 raw detectors: 0 matches in the 16 changed files; history-secret gate in a throwaway clone (§2.1) |

Mutation testing was not run (the owner's default). Backend and frontend code are unchanged, so their suites were not re-run.

### 2.1 History-secret gate

A throwaway clone commits the same 16 blobs on `e665877` and adds a PR-style merge ref: **PASS, 40 findings = 40 baseline entries, 0 problems, 0 findings in the new blobs**.

## 3. Preflight for the first live comparison

### A. Chosen models (official pages re-read 2026-11-11)

| Provider | Model | What the pages say | Open point |
|---|---|---|---|
| Groq | `openai/gpt-oss-20b` | Production Models; not deprecated (named as the recommended replacement on the deprecations page); 131,072 context, 65,536 max completion | A reasoning model: reasoning counts against the output cap; `length` is possible and is recorded, never continued |
| OpenRouter | `google/gemma-4-26b-a4b-it:free` (alternate `nvidia/nemotron-3.5-lightning:free`) | Both listed on the free-models collection at "$0/M input tokens $0/M output tokens" | The model pages show **standard-variant prices** and request examples **without** the `:free` suffix (R136) |
| Gemini | `gemini-3.1-flash-lite` (the only allowlisted model) | Model page: "Stable: gemini-3.1-flash-lite", output limit 65,536 | The pricing page's Free Tier lines for this model were read **as a summary, not verbatim**. Free Tier content "used to improve our products" (synthetic prompts only) |

### B. Account and free-tier evidence

**Nothing is verified for any account.** No account was accessed, and no evidence is inferred from documentation.

| Provider | Required evidence | Status | Expiry once released |
|---|---|---|---|
| Groq | Free Plan limits for `openai/gpt-oss-20b` from the account's Limits page (B1). The public pages **conflict**: models page "250K TPM 1K RPM" (Developer plan) against the rate-limits table 30 RPM / 1K RPD / 8K TPM / 200K TPD ("base limits for the Developer plan"). Neither states Free Plan values | **Missing** (owner) | evidence 14 days; attestation `LLMI_GROQ_PLAN_CHECKED_ON` 1 day |
| OpenRouter | Exact model ID and $0 at the send date. Account: no purchased credits, no BYOK, no top-up. Documented caps: 20/min and 50/day below $10 of credits; "Failed attempts count toward the daily cap" | Model listed; **account missing** (owner) | evidence 7 days; attestation 1 day |
| OpenRouter routing | How `max_price: 0` behaves for a `:free` model, and the status when no endpoint qualifies (R135). **Still not documented** (provider-selection page re-read in full 2026-11-11) | **Blocker** | 7 days |
| Gemini | AI Studio Billing Tier of the project is `free`; the Free Tier limits shown in AI Studio for the model. The rate-limits page states limits "can be viewed in Google AI Studio" and lists no Flash-Lite figures | **Missing** (owner); shipped evidence **expired** | evidence 14 days; attestation 1 day |

### C. Security gates

| Gate | Groq | OpenRouter | Gemini |
|---|---|---|---|
| Fail-closed policy, consent phrase, attestation, refused environment overrides | pass (ADR-44) | pass (ADR-47) | pass (ADR-35 + ADR-49) |
| Strict attestation date | pass | pass | **pass since R123 (1)** |
| TLS key logging refused | pass (boundary) | pass (boundary) | **pass since R123 (2)**, all three contexts |
| Endpoint and credential binding | pass (ADR-43/45) | pass (ADR-46/47) | pass (ADR-34 endpoint check at construction and before send) |
| No retry, fallback or paid routing; one request per process (shared slot) | pass | pass, plus `max_price` 0 sent | pass |
| Execution lock | **closed** (flag and evidence) | **closed** (flag and two evidence records) | **closed** (expired evidence; no separate flag, R140) |
| Reviewed fixes outstanding | none | R135 | R123 fix needs the architectural review |

### D. Proposed requests and caps

- **Requests:** exactly **3** real requests in total, one per provider, each in its own process and under its own written authorization, all on `cmp-ticket-triage`:
  - pinned text of 621 bytes, SHA-256 `5d9040f3…c267ff`;
  - input bound 685 (`LLMI_LIVE_MAX_INPUT_TOKENS` ≥ 685, at most 1024).
- **Output ceiling:** 1024 tokens for every provider (`LLMI_LIVE_MAX_OUTPUT_TOKENS=1024`). It is sent as `max_completion_tokens` (Groq), `max_tokens` (OpenRouter) and `maxOutputTokens` (Gemini).
- **Controls:**
  - `LLMI_LIVE_MAX_REQUESTS=1`, retries 0, concurrency 1;
  - timeouts: 30 s fixed for Groq and OpenRouter, the Gemini default of 20 s;
  - no second attempt after a timeout (`possibly_sent` is final).
- **Recorded per run:**
  - latency, the input, output, cache and reasoning tokens, and the completion status;
  - the scenario validation, trace ID, provenance and request state;
  - cost `unknown` (never 0).
- **Suggested order:**
  1. Groq;
  2. Gemini;
  3. OpenRouter, the provider with the most open points (R135, R136).

  Each run is reviewed (§3.2 of `reports/phase-8b.md`) before the next is authorized.

### E. Local backend and comparison project

| Item | Observed 2026-11-11 (no credential used) |
|---|---|
| Backend `127.0.0.1:8000` | **not reachable**: `GET /health/live` and `/health/ready` got no connection |
| Dashboard `127.0.0.1:3000` | answers 200 |
| Dedicated comparison project (`create-key --project llm-insight-compare`) | **not created or verified** (it needs the backend and the admin CLI; owner action). The runner's dry run then has to show `execution_ready: true` |
| Developer database | not accessed |

### F. GO / NO-GO

| Provider | Verdict | Exact blockers to clear first |
|---|---|---|
| **Groq** | **NO-GO** | the account Limits evidence (B1); a reviewed release of `FREE_PLAN_MODELS`, the evidence date and `REAL_EXECUTION_ENABLED`; a current plan attestation; the backend and comparison project ready; the written authorization |
| **OpenRouter** | **NO-GO** | **R135**, zero-price routing undocumented: cannot be released on documentation; account-state evidence; a reviewed release of the model, both evidence dates and the flag; R136 to be accepted as a possible first-response halt; the backend and project; the written authorization |
| **Gemini** | **NO-GO** | the architectural review of the R123 fix (ADR-49); the AI Studio Billing Tier and limits check; a reviewed re-release of `FREE_TIER_VERIFIED_AT` (it re-enables Gemini, R140); a current attestation; the backend and project; the written authorization |

Common to all three: this branch's CI (not run), and PR #11's CI (queued when read).

Nothing is unlocked, pushed or executed. The next step is the owner's evidence and a separate authorization per provider.
