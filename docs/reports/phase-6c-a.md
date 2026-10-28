# Phase 6C-A — Gemini Free Tier integration and multi-provider observability — Report

**Date:** 2026-10-28 (UTC). **Branch:** `feature/phase-6c-gemini-free` (from `0190bf4`, not pushed, not merged, no tag). **Decisions:** ADR-33…36 (`ADR.md` §10, recorded within the Phase 6C-A instruction, open to the architectural review). **Design:** `PHASE6_LIVE_INTEGRATION_PLAN.md` Part II. **No Gemini or OpenAI request was sent, no API key was created, read or requested, nothing was spent.**

## 1. Recovered baseline

Phase 6C-A was started in an earlier session that stopped on context limits; this session resumed it without discarding anything (no reset, clean, checkout or history rewrite).

| Item | State found |
|---|---|
| Repository | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (`git rev-parse --show-toplevel`; the home-directory repository was not touched) |
| Branch / HEAD | `feature/phase-6c-gemini-free` at `0190bf4` (Phase 6B / R87); `feature/phase-6a-live-foundation` and `main` unchanged; no stash; nothing staged |
| Phase 5 release | `1a57d65` (tag `phase-5`), unchanged |
| Uncommitted work | 10 modified files (`pricing.py`, `test_pricing.py`, `live_openai.py`, `ADR.md`, `PRICING.md`, `SECURITY.md`, `SOURCES_AND_RISKS.md`, `TELEMETRY_CONTRACT.md`, `sdk/pyproject.toml`, `providers.py`) and 10 new files (`gemini_adapter.py`, `gemini_live.py`, `demo_app/live_gemini.py`, `demo_app/live_common.py`, `tests/gemini_fixtures.py`, four Gemini unit test modules, one integration module) |
| SDK venv | `google-genai` 2.30.1 and 11 new transitives installed, **not** in `sdk/requirements-dev.lock` |

## 2. Work that existed before resuming (reviewed and kept)

Progress checklist of the instruction (A–T) as recovered, then as delivered:

| Item | Recovered | Delivered |
|---|---|---|
| A. SDK dependency and pin | PARTIAL (`pyproject.toml` extra `gemini`; lock and `ARCHITECTURE.md` §6 missing) | COMPLETED |
| B. Free Tier model/API research | COMPLETED (sources recorded) | COMPLETED, independently re-verified (§5) |
| C–D. Adapter, provider-neutral integration | COMPLETED | kept |
| E–F. Usage normalization, thinking/cached tokens | COMPLETED | kept |
| G. Error and response classification | COMPLETED | kept |
| H. Endpoint and credential protection | COMPLETED | kept |
| I–K. Free Tier guard, zero retries, Free vs paid semantics | COMPLETED | kept |
| L. Unknown cost / NULL | COMPLETED (backend rule + tests) | kept |
| M. CLI runner | COMPLETED | kept |
| N. Offline unit tests | COMPLETED (not re-run) | re-run: pass |
| O–P. OTLP → PostgreSQL → `/api/v1` | COMPLETED (test written, not run) | run: pass |
| Q. Documentation and ADRs | PARTIAL (ADR-33…36, R89–R96, `SECURITY.md`, `PRICING.md`, `TELEMETRY_CONTRACT.md`; report, plan Part II, `ARCHITECTURE.md` §6, READMEs, `.env.example`, `PHASE_STATUS.md` missing) | COMPLETED |
| R–S. Security verification, full gates | NOT RUN | COMPLETED (§11) |
| T. Report and commit | NOT STARTED | COMPLETED |

The recovered code was reviewed against the instruction before reuse: the adapter refuses every unsupported feature before sending and forces `automatic_continuation=False` / AFC disabled; the endpoint check, the guard and the runner match ADR-34…36; no prompt, output, thought, key or provider message reaches a span, log or the runner output. No defect was found that needed a code change, so the reviewed code is unchanged.

## 3. Work completed in this session

- `sdk/requirements-dev.lock`: the 12 installed but unlocked packages added with a dated header note; the lock equals `pip freeze` (60 pins). Upload dates re-read on the PyPI JSON API (§5).
- `ARCHITECTURE.md` §6: provider pin row and the Phase 6C-A install paragraph.
- `.env.example`: empty (fail-closed) `LLMI_GEMINI_MODE`, `LLMI_GEMINI_BILLING_TIER`, `LLMI_GEMINI_TIER_CHECKED_ON`, `GEMINI_API_KEY` with the routing variables that must stay unset.
- `PHASE6_LIVE_INTEGRATION_PLAN.md` Part II (§6–§13); `README.md` (configuration row, known limitations); `sdk/README.md` (extra, adapter, boundary, guard, runner); `PHASE_STATUS.md`; this report.
- `frontend/tests/e2e/e2e-env.ts` (test harness only): the E2E specs now take `LLMI_API_BASE_URL` from the process environment first, then `frontend/.env.local`, as Next.js and the dashboards started by `playwright.config.ts` already do. Reason: an unrelated container (`ppe-split-dashboard-1`) occupies `127.0.0.1:8000`; it was left running and untouched, and the suite ran against an LLM Insight backend on `127.0.0.1:8010`. No dashboard code changed.
- Independent re-verification of the model, Free Tier and semconv evidence (§5); all quality gates (§11).

## 4. Endpoint and credential review (google-genai 2.30.1, httpx 0.28.1)

Source read in the installed package (`google/genai/{_api_client,client,_base_url,models,_extra_utils,errors}.py`) by the earlier session and re-checked here against the tests:

| Input | Library behaviour | Control (ADR-34/35) | Test |
|---|---|---|---|
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | `GOOGLE_API_KEY` wins | guard refuses `GOOGLE_API_KEY`; runner passes `api_key` explicitly from `GEMINI_API_KEY` | `test_every_client_environment_override_is_refused`, `test_the_environment_cannot_move_the_runner_client` |
| `GOOGLE_GENAI_USE_VERTEXAI` / `_ENTERPRISE`, `GOOGLE_CLOUD_PROJECT` / `_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` | switch to Vertex AI / add project, location, credentials | refused by the guard; `vertexai=False` explicit; client check refuses project, location, credentials, Vertex | guard + `REFUSED["vertex ai client"/"project"/"credentials"/"vertex flag"]`, `test_a_google_cloud_project_in_the_environment_is_caught_by_the_client_check` |
| `GOOGLE_GEMINI_BASE_URL` / `GOOGLE_VERTEX_BASE_URL` | replace the base URL | refused; `base_url` explicit; check requires exactly `https://generativelanguage.googleapis.com/` + `v1beta` | `foreign/lookalike base url`, `plain http`, `other api version`, `base url changed later` |
| `GOOGLE_GENAI_CLIENT_MODE`, `…_REPLAYS_DIRECTORY`, `…_REPLAY_ID` | record/replay client | refused; `DebugConfig(client_mode=None)`; check requires the plain `BaseApiClient` | `replay client mode` |
| `GOOGLE_API_USE_MTLS_ENDPOINT` / `…_CLIENT_CERTIFICATE` | mTLS endpoint | refused; check requires the mTLS switch off and no google-auth session | guard |
| `HTTP(S)_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` / `SSL_CERT_DIR` | default `httpx` client trusts the environment | own `httpx.Client(trust_env=False, verify=<pinned certifi context>)`; check requires `_trust_env is False`, no mounts, direct `HTTPTransport`, `CERT_REQUIRED` + hostname check | `trust_env`, `explicit proxy`, `transport mount`, `tls verification off`; an unreadable `SSL_CERT_FILE` refuses before sending (`test_an_unreadable_ca_setting_refuses_before_sending`) |
| Redirects | default client follows them; `httpx` strips only `Authorization`, so `x-goog-api-key` would be forwarded (R94) | `follow_redirects=False`, checked | `redirects followed`; 301/302/307/308 → one request, `http_error`, nothing re-sent |
| Custom transports, hooks, auth, headers, `extra_body`, retry options, ephemeral tokens, subclasses | all possible through `HttpOptions` / `httpx.Client` | each refused | 30 parametrized refusals in `test_gemini_endpoint.py` |
| Library version | checks read private attributes | other versions fail closed (`client_library_not_audited`) | `test_a_library_version_change_fails_closed` |

The check runs after the reservation and before telemetry (`client_not_allowed`, exit 2, nothing sent), when `ObservedGeminiClient` is built, and immediately before the send (`test_the_client_is_checked_again_immediately_before_the_send`). The final request was captured on the real `httpx.HTTPTransport` class with only `handle_request` replaced: `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent`, the key only in `x-goog-api-key`, no `Authorization` / `x-goog-user-project` / proxy header, body exactly `{"contents":[{"parts":[{"text":<fixed SYNTHETIC sentence>}],"role":"user"}],"generationConfig":{"maxOutputTokens":256}}`, timeout 20 s on every phase. Endpoint integrity says nothing about the billing tier (§6).

## 5. Selected model, SDK version and evidence

**Model: `gemini-3.1-flash-lite`** (the only allowlist entry; evidence time `FREE_TIER_VERIFIED_AT = 2026-10-28T13:00Z`, refused after 14 days, i.e. after 2026-11-11T13:00Z).

Re-verified independently in this session (2026-10-28):
- Pricing page (https://ai.google.dev/gemini-api/docs/pricing, "Last updated 2026-10-27 UTC"): `gemini-3.1-flash-lite` Free Tier input "Free of charge", output "Free of charge"; paid Standard USD 0.25 (text/image/video) / 1.50 per 1M tokens; "Used to improve our products": Yes on the Free Tier, No on paid.
- Model page (https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite, "Last updated 2026-10-26 UTC"): model code `gemini-3.1-flash-lite`, **Stable**, text output, input 1,048,576 / output 65,536 tokens, thinking supported, no deprecation or shutdown date.
- Semconv at the ADR-15 pin (`9ffc7e00c639…/model/gen-ai/registry.yaml`): SHA-256 `63b40577e4b5805eec0e1a21659e5e4bcad903acc77de55c9f1e5a75488a4937` (matches ADR-33), `gcp.gemini` and `generate_content` present.

What this does **not** confirm: that the operator's own project is on the Free Tier, or its actual limits. That is a Phase 6C-B operator check (§10).

**SDK:** `google-genai` **2.30.1** (first uploaded 2026-10-13T14:11Z; 2.31.0 was uploaded 2026-10-14T23:52Z and is not yet two weeks old). Transitives and their dates: `ARCHITECTURE.md` §6. `pip-audit` (PyPI and OSV) finds no known vulnerability in either lock.

## 6. Free Tier safety design (ADR-35, ADR-36)

Real execution is off by default. `python -m demo_app.live_gemini` without `--execute` never creates a Gemini client. With `--execute`, one request is sent only if **all** of the following hold; any failure is exit 2, nothing sent:

| Gate | Requirement |
|---|---|
| Provider mode | `LLMI_PROVIDER_MODE=real` |
| Gemini mode | `LLMI_GEMINI_MODE=free-tier` (no paid mode exists) |
| Consent | `LLMI_LIVE_CONSENT=gemini-free-tier-single-call` (the OpenAI phrase never enables Gemini, and vice versa; tested) |
| Key | `GEMINI_API_KEY` present (presence only; never an argument, never printed); a key alone enables nothing (tested) |
| Routing | none of the 13 google-genai routing/credential variables set |
| Free Tier | operator attestation `LLMI_GEMINI_BILLING_TIER=free` and `LLMI_GEMINI_TIER_CHECKED_ON` (UTC date, today or yesterday, not future) |
| Allowlists | provider `gcp.gemini` only; model on the verified Free Tier list; evidence ≤ 14 days old |
| Limits (hard caps) | 1 request, concurrency 1 (refused, never queued), 0 retries (adapter and library), timeout ≤ 30 s (default 20), output 16–1024 tokens (thinking counts against it), input bound ≤ 1024 |
| Backend | telemetry config valid and the backend accepts the project key (empty OTLP request) |
| Client | `live_gemini_client_problems` empty (§4), audited library versions |

**The Free Tier statement is an operator attestation, not a proof from Google's billing backend** (R90): no Google API reports a project's billing tier to a client, and endpoint integrity does not prove it either. The account-level boundary is provider-side: a project with no billing account linked cannot be billed.

**Cost semantics.** No stored price ever applies to `gcp.gemini` (backend rule `_gemini_tier_unprovable`, `PRICING.md` §3): every Gemini usage row is `cost_status = unknown`, `cost_usd = NULL`, reason `price_missing` (or `usage_missing` / `usage_inconsistent`) — never 0, never a paid-tier list price, even if OpenAI or Gemini rows are seeded. The runner prints `billed_amount_usd: null`, `paid_tier_list_price_estimate_usd: null`, the attested tier and its basis. No migration, no new `cost_unknown_reason` value; accepted Phase 5 cost data are untouched.

## 7. Token mapping and outcomes (ADR-33)

| Normalized (semconv) | Gemini `usageMetadata` |
|---|---|
| input | `promptTokenCount + toolUsePromptTokenCount` (prompt already includes cached content) |
| output | `candidatesTokenCount + thoughtsTokenCount` (Google bills output as candidates + thinking) |
| cache read (subset of input) | `cachedContentTokenCount` |
| reasoning (subset of output) | `thoughtsTokenCount` |

Absent counts are 0 only when `totalTokenCount` equals the sum; a missing prompt or total → unknown (`missing`); a mismatched sum or cached > prompt → unknown (`inconsistent`); nothing is estimated from text. Example (integration fixture): prompt 20 (4 cached), candidates 2, thoughts 30, total 52 → input 20, cache read 4, output 32, reasoning 30, stored once, the parent span carries no usage. The runner prints the provider's own counts next to the normalized ones and the check result.

Outcomes: `STOP` → completed; `MAX_TOKENS` / `CONTINUATION` → incomplete (reason kept, never followed: R93); blocked prompt → `blocked` + span ERROR `gcp.gemini.prompt_blocked`; safety-type finish → `blocked`, span UNSET with the finish reason; no candidate / other / missing finish → failed; unreadable body → error `malformed_response`. HTTP errors: 401 `authentication_failed`, 402 `payment_required`, 400 `FAILED_PRECONDITION` `precondition_failed`, 403, 404, 429 `rate_limited` (never retried), 5xx `provider_error`, timeout, connection. Classification reads the class, code and status keyword only, never the message (google-genai puts the error body into `str(exc)`).

## 8. Metadata and privacy guarantees

- Sent to Google: only the fixed SYNTHETIC sentence (`INPUT_TEXT`); no CLI or environment override exists (R91: Free Tier content may be used by Google).
- Recorded: model, response model and ID, finish reason, token counts, duration, status, error type, `server.address`/`port`, numeric settings. Never: contents, output, thoughts, safety ratings, headers, the API key, the project key, provider error messages or bodies.
- Verified in tests on the wire, the SDK log, the runner output, the backend log, the stored spans and usage rows and every `/api/v1` response: the fake key, prompt, output, thought and error sentinels and `INPUT_TEXT` are absent (`test_runner_trace_reaches_postgresql_and_the_read_api`, `test_no_content_or_secret_reaches_the_wire_the_log_or_the_outcome`).
- Every Gemini span attribute is on the `attrmap.v1` allowlist (`server_dropped_attributes_count = 0`): no backend allowlist change.
- No dashboard route, Server Action or endpoint can trigger a Gemini call or accept a key; the frontend has no provider-specific code and needed no change.

## 9. Offline end-to-end evidence

`sdk/tests/integration/test_live_gemini.py` (7 tests, every non-loopback connection blocked, SYNTHETIC fixtures behind `httpx.MockTransport`, `synthetic` provenance, the session's disposable `*_test` database with the OpenAI catalogue seeded):

Fake Gemini response → `GeminiTextAdapter` / runner → CLIENT span (child of `demo.live-gemini`) → official OTLP/HTTP protobuf exporter → live `python -m app` `POST /v1/traces` → PostgreSQL → `/api/v1/traces/{id}`, `/api/v1/traces`, `/api/v1/overview`, `/api/v1/models/compare`.

Checked: one trace ID from the runner output to the DB and API; 2 spans (root + one CLIENT, correct parent); provider `gcp.gemini`, operation `generate_content`, request and response model; finish reason `["STOP"]`; usage row (input 20, cached 4, output 32, reasoning 30, `provider_reported`, attempt 1, key `resp:gcp.gemini:SYNTHETICgeminiIT01`); cost `NULL` / `unknown` / `price_missing` with no `price_version` (an OpenAI price never applies); overview `cost_complete = false`; `models/compare` lists `gcp.gemini`; 429, timeout, `MAX_TOKENS`, blocked prompt and missing usage each stored once with the right status, `error.type` and unknown-cost reason; re-exporting the same spans twice leaves 2 spans and 1 usage row (no duplicate span or cost); no secret or content anywhere.

## 10. Operator guide for Phase 6C-B (one authorized Free Tier request)

**Do not start before** the architectural review accepted ADR-33…36 **and** the user explicitly authorized this one run. Nothing below may be pasted into a chat, file, document or command-line argument.

1. **Google side (in the browser):**
   1. Use a **dedicated** Google Cloud project created in Google AI Studio with **no billing account linked**.
   2. On the AI Studio projects / API keys page, read the project's **Billing Tier** column: it must say **Free**. If it shows a paid tier, stop.
   3. On `https://aistudio.google.com/rate-limit`, confirm the project has a Free Tier limit for `gemini-3.1-flash-lite`.
   4. Create an API key in that project and restrict it to the Gemini API only ("Restrict to Gemini API only").
2. **Local stack:** start PostgreSQL (`docker compose up -d --wait postgres`) and the backend (`backend/.venv/Scripts/python -m app`; if port 8000 is taken, set `LLMI_PORT` and use that port below). Create a project key: `python -m app.cli create-key --project gemini-live` (backend venv); note the printed key and project id.
3. **In your own PowerShell window** (never in a file), from the repository root:
   ```powershell
   # remove any google-genai routing variable (the runner refuses them)
   Get-ChildItem Env: | Where-Object { $_.Name -like 'GOOGLE_*' } | ForEach-Object { Remove-Item "Env:$($_.Name)" }
   $env:LLMI_PROVIDER_MODE = 'real'
   $env:LLMI_GEMINI_MODE = 'free-tier'
   $env:LLMI_LIVE_CONSENT = 'gemini-free-tier-single-call'
   $env:LLMI_GEMINI_BILLING_TIER = 'free'
   $env:LLMI_GEMINI_TIER_CHECKED_ON = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
   $env:LLMI_LIVE_PROVIDERS = 'gcp.gemini'
   $env:LLMI_LIVE_MODELS = 'gemini-3.1-flash-lite'
   $env:LLMI_LIVE_MAX_REQUESTS = '1'
   $env:LLMI_LIVE_MAX_INPUT_TOKENS = '256'
   $env:LLMI_LIVE_MAX_OUTPUT_TOKENS = '256'
   $env:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = 'http://127.0.0.1:8000/v1/traces'
   $env:OTEL_SERVICE_NAME = 'live-gemini'
   $env:LLMI_DASHBOARD_PROJECT_ID = '<project id>'
   $p = Read-Host -AsSecureString 'LLM Insight project key'
   $env:OTEL_EXPORTER_OTLP_TRACES_HEADERS = 'X-Project-Key=' + [System.Net.NetworkCredential]::new('', $p).Password
   $k = Read-Host -AsSecureString 'Gemini API key'
   $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $k).Password
   Remove-Variable p, k
   ```
   (Only set `LLMI_GEMINI_TIER_CHECKED_ON` after doing step 1.2 that day: it is your statement that you checked.)
4. **Dry run:** `sdk\.venv\Scripts\python -m demo_app.live_gemini`. Require `"execution_allowed": true`, `"refusals": []`, `"backend": {"ready": true, ...}`, and review the plan (model, endpoint, 256 tokens, 1 attempt, `paid_fallback: false`).
5. **One request:** `sdk\.venv\Scripts\python -m demo_app.live_gemini --execute`. Exit 0 = completed/incomplete, 1 = provider failure or blocked, 2 = refused (nothing sent). **Never re-run automatically**; a timeout may still have been processed.
6. **Immediately afterwards:** `Remove-Item Env:GEMINI_API_KEY, Env:OTEL_EXPORTER_OTLP_TRACES_HEADERS`; close the window.
7. **Check and record (Phase 6C-B report):** open the printed `dashboard_url`; confirm one CLIENT span `generate_content gemini-3.1-flash-lite`, the usage row's `cost_status = unknown`, `tokens` vs `provider_usage` and `usage_check = consistent`, `input` ≤ 133 (the bound); compare with the AI Studio usage page; confirm the project still shows the Free tier.
8. **If it failed:** `429` → a per-minute or daily quota (RPD resets at midnight Pacific); wait, do not loop. `402` or `400 FAILED_PRECONDITION` → the project is linked to billing or not eligible: **stop**, do not retry, re-check step 1. `403` → the project or region is restricted from the Free Tier. `401` → wrong key. Revoke the key in AI Studio if it may have been exposed.

## 11. Quality gates (exact commands, this session)

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, real PostgreSQL) | **1730 passed**, 0 failed, 0 skipped (195.7 s; 1 warning: R20) = 1726 + 4 Gemini pricing cases |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk, live backend, after reinstall) | **817 passed**, 0 failed, 0 skipped (286.5 s) = 591 (Phase 6B) + 226 Gemini |
| — of which Gemini | 219 unit (adapter 75, endpoint 41, guard 66, runner 37) + 7 integration |
| `npm test` (frontend, Node 22.23.4) | **311 passed**, 0 failed, 0 skipped, 0 cancelled |
| `npx playwright test` (Edge; `LLMI_API_BASE_URL` / `LLMI_SEED_ENDPOINT` → backend on `127.0.0.1:8010`) | **55 passed**, 0 failed, 0 skipped (2.0 min); teardown deleted its run projects 145–147, `remaining: []` |
| `npm run typecheck` / `npm run lint` | clean / 0 warnings |
| `npm run build` / `npm run check:bundle` | built / 632 files, 23 in the browser bundle, **0 findings** |
| `npm audit` | 0 vulnerabilities |
| `ruff check . ../demo_app`, `ruff format --check . ../demo_app` (sdk) | all passed; 50 files formatted |
| `ruff check .`, `ruff format --check .` (backend) | all passed; 140 files formatted |
| `python -m mypy src`; `python -m mypy --config-file pyproject.toml ../demo_app` (strict) | no issues (12 / 6 files) |
| `pip check` (backend; sdk) | no broken requirements |
| `sdk/requirements-dev.lock` vs `pip freeze` | identical (60 pins) |
| `alembic current` / `alembic check` | `0005 (head)` / no new upgrade operations |
| `docker compose config --quiet`; `docker compose --profile app config --quiet` | valid |
| `python -I scripts/verify_phase0.py` | **797 PASS, 0 FAIL** (before and after the documentation changes) |
| `pip-audit -r <lock> --no-deps --disable-pip -s pypi\|osv` (both locks; pip-audit 2.10.1, Phase 5B throwaway venv) | no known vulnerabilities (4/4) |
| `pip-audit --path <venv>/Lib/site-packages` (both venvs) | `pip` 24.2 only (12 advisories; R75, unchanged) |
| `bandit` 1.9.4 on `gemini_adapter.py`, `gemini_live.py`, `providers.py`, `demo_app/live_{gemini,common,openai}.py`, `backend/app/services/pricing.py` | 1 Medium B310 (the readiness probe's `urlopen`, moved unchanged from `live_openai.py` to `live_common.py`; accepted in 6A), 0 High |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` (staged tree) | 854 blobs, 32 messages, 476 working files, 9 local secret values (names only): **0 secret-value hits**; 39 pattern hits (exit 1), all `url_userinfo` at the Phase 5B F9 fixture locations (redaction test vectors, the `SECURITY.md` / `phase-2c.md` example lines). 37 at the 6B preflight; +1 for the `SECURITY.md` blob of `0190bf4` and +1 for this phase's `SECURITY.md` blob, each carrying the same single pre-existing example line. No hit is in a Phase 6C-A addition |
| `git diff --check` | `git diff --cached --check`: clean |

## 12. OpenAI regression

The OpenAI adapter, guard, endpoint check and runner behaviour are unchanged: `live_openai.py` only imports the moved readiness/link/output helpers from `live_common.py` (identical code). `test_live_runner.py`, `test_live_endpoint.py`, `test_live_guard.py`, `test_openai_responses.py` and the OpenAI integration tests pass in the SDK run above; the backend's OpenAI applicability and price tests pass; the additive `blocked` status, `provider_reason` field and four error categories change no OpenAI outcome.

## 13. Known limitations and risks

- R89 diagnostics do not interpret Gemini finish reasons (stored verbatim). R90 the Free Tier is an operator attestation. R91 Free Tier content may be used by Google (only the SYNTHETIC sentence is sent). R92 model and tier lifecycle; thinking counts against the 256-token ceiling (a `MAX_TOKENS` result is reported as incomplete). R95 `responseId` uniqueness is assumed. R96 the TLS trust anchor is the pinned `certifi` bundle. R93, R94 resolved in code.
- Gemini cost is never computed, by design; pricing it needs a tier-aware rule, a verified catalogue and a new ADR.
- The endpoint check reads private attributes of the audited library versions; any upgrade fails closed until re-audited.
- Per-process limits only (as R79): the session counter does not span processes; the provider-side boundary is the unlinked billing account.
- A proxy-only network cannot run the live call (no environment proxy is used).
- Linux CI was not run for this branch (not pushed).

## 14. Phase 6C-B prerequisites

1. Architectural review and acceptance of ADR-33…36 (ADR-33 amends ADR-05 for Phase 6).
2. Explicit user authorization of the single Free Tier request, given for that run.
3. Operator steps of §10, including the AI Studio Billing Tier check on the day of the run and a key restricted to the Gemini API in a project with no billing account.
4. Evidence window: after 2026-11-11T13:00Z the guard refuses until the pricing and model pages are re-read and `FREE_TIER_VERIFIED_AT` is re-released.
5. Afterwards: record the trace, token counts vs the bound, the AI Studio usage page and the unchanged Free tier in a Phase 6C-B report. Phase 6C-C (dashboard work) is not started.

## 15. Verdict

Phase 6C-A is complete offline: the Gemini Developer API adapter, the Free Tier guard, the endpoint boundary and the runner are implemented and tested, the data path to PostgreSQL and the read API is verified with the network blocked, Gemini cost is always unknown (never 0, never a paid price), and OpenAI and Phase 5 behaviour is unchanged. **Stopped for architectural review.** No provider request was sent and nothing was spent.
