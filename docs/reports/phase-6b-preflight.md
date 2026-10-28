# Phase 6B — Live provider preflight and first-trace readiness — Report

- **Date:** 2026-10-28 (UTC). **Branch:** `feature/phase-6a-live-foundation` (base `b3e5b07`, Phase 6A; `main` = `phase-5` = `1a57d65` untouched). Not pushed, not merged, not tagged.
- **Environment:** Windows 11 Home 10.0.26200, Python 3.12.6, Node 22.23.4 (project-local), Docker Desktop (Engine 28.5.1), PostgreSQL 17 (compose, migration `0005`), `openai` 3.28.0, `httpx2` 2.13.1 (pinned, unchanged).
- **Spend:** **none.** No request was sent to OpenAI or any other provider. No API key was requested, read or configured: `OPENAI_API_KEY` is absent from the process, user and machine environment (presence checked only) and empty in `.env`. Every provider interaction was a SYNTHETIC fake transport with all non-loopback connections blocked. The only outbound traffic was two public GETs of the OpenAI pricing and model documentation pages (§6).
- **Verdict:** ready for **one** explicitly authorized real request **after** the reviewer accepts ADR-28…31 and the operator completes §11. Two concrete defects were found and fixed (R85, R86; ADR-31). **Stopped for architectural and explicit spending approval.**

## 1. Baseline and architectural verification

| Check | Result |
|---|---|
| Repository root | `git rev-parse --show-toplevel` = the project folder (home-directory repo untouched) |
| Working tree before changes | clean on `feature/phase-6a-live-foundation` at `b3e5b07` |
| Phase 6A commit audit | `git show --stat b3e5b07`: 22 files, +3156/−13; code in `sdk/src/llm_insight_sdk/{live,providers,openai_adapter,instrumentation}.py`, `demo_app/live_openai.py`; no backend, frontend, schema, lock or compose change (matches `reports/phase-6a.md` §3) |
| `main` / `phase-5` | not checked out, not modified |
| Documents read | `PHASE6_LIVE_INTEGRATION_PLAN.md`, `reports/phase-6a.md`, `ADR.md` §8, `SECURITY.md` §2/§6, `TELEMETRY_CONTRACT.md` §3, `SOURCES_AND_RISKS.md` R79–R84, `PRICING.md` §2–§4 |
| Installed library source read | `openai/_client.py` (credential and env handling), `openai/_base_client.py` (retry loop, header merge), `openai/resources/responses/responses.py` (`create` parameters), `httpx2/_config.py`, `httpx2/_utils.py` |

### ADR-28…30 verdict

| ADR | Claim | Verified in source / tests | Verdict |
|---|---|---|---|
| ADR-28 | Responses API in the existing adapter, operation `chat` + `openai.api.type=responses`, `attrmap.v1`, no migration | `create_response` reuses `_attempt_span`; DB row of the dry run (§5) shows `attributes_schema_version = llminsight.attrmap.v1`, `server_dropped_attributes_count = 0` | **Holds** |
| ADR-28 | Non-streaming text only; tools refused; `store=False` always sent | Named refusals held, but the list was a deny-list: `extra_body={"tools": …}`, `prompt`, `moderation`, `context_management`, `access_programs` reached the client (R86) | **Held only for named parameters → fixed by ADR-31** (allowlist) |
| ADR-28 | Every field type-checked; failed / malformed responses end the span `ERROR` | §3 matrix (6 response shapes, runner level) | **Holds** |
| ADR-28 | Only an unambiguous 429 may be retried | §2, `test_ambiguous_or_final_failures_are_never_retried_even_with_retries_allowed` (7 cases) | **Holds** |
| ADR-29 | Fail-closed guard; key alone enables nothing; consent phrase; allowlists; hard caps; 14-day evidence; Decimal reservation; conservative booking; halts | §2 | **Holds**, except that the credential and billing project could be swapped through `OPENAI_CUSTOM_HEADERS` / `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID`, which `openai` reads itself (R85) → **fixed by ADR-31** |
| ADR-30 | Only entry point; dry run default; no client in dry run; pins `https://api.openai.com/v1`; metadata-only output; exit codes 0/1/2 | `test_dry_run_is_the_default_and_never_creates_a_provider_client`, `test_the_real_client_factory_pins_the_endpoint_and_disables_sdk_retries`; no route, Server Action or endpoint in `backend/` or `frontend/` creates a client or accepts a key (`openai` is not even installed in the backend) | **Holds**; one additive output field (`incomplete_reason`) |

## 2. Single-request cost safety

| Requirement | Proof (source → test) |
|---|---|
| Real mode defaults OFF | `evaluate_policy`: `LLMI_PROVIDER_MODE` must equal `real`; `.env.example` and this machine's `.env` leave every live key empty → `test_real_mode_is_off_by_default`; dry run on this machine without configuration lists every refusal |
| An API key alone cannot enable execution | key read for presence only → `test_an_api_key_alone_never_enables_execution` |
| Explicit consent phrase | exactly `openai-paid-calls`, case-sensitive → gate cases `consent_missing` |
| Provider and model allowlists | `LLMI_LIVE_PROVIDERS` ⊆ `{openai}`; every `LLMI_LIVE_MODELS` entry needs a verified price; `--model` must be in the list; `reserve` re-checks both |
| Input / output upper bounds | `LLMI_LIVE_MAX_INPUT_TOKENS` 1–8192 (input bound = UTF-8 bytes + 64); `LLMI_LIVE_MAX_OUTPUT_TOKENS` 16–4096, sent as `max_output_tokens` (provider-enforced, reasoning included) |
| Every billable attempt reserved before sending | `reserve` books `attempt_bound × (1 + retries)` into `outstanding_usd` under a lock before the client is created; settle books each physical attempt at its estimate or its bound |
| Unknown or stale prices fail closed | unpriced model → `model_price_unknown`; evidence > 14 days → `price_catalogue_stale`; clock before evidence → `price_not_yet_valid`; unknown post-call estimate → booked at the bound and session `halted=estimate_unknown` |
| Decimal throughout | prices, caps and budgets parsed with `Decimal` (NaN/Infinity refused); 60-digit context; bounds rounded `ROUND_CEILING`, estimates `ROUND_HALF_EVEN` (the backend's rule); output strings fixed-point (`0E-10` display fixed in this phase) |
| No tool charges | runner request is fixed (`model`, `input`, `max_output_tokens`, `service_tier`, `store`); **and** (ADR-31) `create_response` accepts only `RESPONSES_ALLOWED` → 12 new refusal cases incl. `extra_body` tools and `prompt` |
| Model / tier cannot silently become unpriced | request model from the allowlist; `service_tier="default"` always sent; a response model or tier outside the catalogue makes the estimate unknown → booked at the bound, session halted; the backend stores `price_missing` (never 0) |
| Session limits apply to all attempts | request bound multiplies by `max_attempts`; budget check counts booked + outstanding + this request; `test_retries_reserve_and_book_every_attempt`, `test_outstanding_reservations_count_against_the_budget` |
| No automatic retry on timeout or ambiguous outcome | `is_unambiguously_retryable` = non-quota 429 only; with `LLMI_LIVE_MAX_RETRIES=2` a timeout, connection error, 500, 503, 403, quota 429 or malformed body is still **one** physical request (7 new runner cases) |
| OpenAI SDK retries disabled | runner factory `max_retries=0`, and `ObservedOpenAIClient` re-applies `with_options(max_retries=0)`; in `openai/_base_client.py` the loop is `range(max_retries + 1)` for timeouts, connection errors and status errors alike; an offline probe sent exactly one request for a 500; `httpx2` transport retries default to 0 and the default client sets none |

### First-test requirements

| Requirement | Enforced by | Value in the §10 profile |
|---|---|---|
| Exactly one allowed request | runner sends one request per invocation; `LLMI_LIVE_MAX_REQUESTS=1` | 1 (`test_a_second_request_in_the_profile_session_is_refused`) |
| Zero retries at every layer | SDK `max_retries=0` (code); adapter `max_attempts = 1 + LLMI_LIVE_MAX_RETRIES` | `LLMI_LIVE_MAX_RETRIES=0` → dry-run plan `max_attempts: 1` |
| Concurrency one | `LLMI_LIVE_MAX_CONCURRENCY=1`; non-blocking semaphore; the runner is single-threaded | 1 |
| Max output tokens ≤ 64 | `LLMI_LIVE_MAX_OUTPUT_TOKENS` | 64 |
| Short fixed synthetic prompt | `INPUT_TEXT` constant, 69 UTF-8 bytes, no CLI or env override | "SYNTHETIC LLM Insight live smoke test. Reply with the single word: OK" |
| Session ceiling ≤ USD 0.10 | `LLMI_REAL_LLM_BUDGET_USD` (hard cap 5.00) | **0.02** (request cap 0.01) |
| Explicit per-run human authorization | the operator types `--execute` in their own shell after the dry run and the written approval (§11); Claude Code never runs it. The consent phrase stays set in that shell, so §11 removes it right after the run (R79) | — |

**Worst-case cost (selected model `gpt-5.4-mini`, catalogue `openai-2026-10-24`):**
- Input bound = 69 bytes + 64 framing = **133 tokens**. Attempt bound = (133 × 0.75 + 64 × 4.50) / 1 000 000 = **USD 0.0003877500** (rounded up). Attempts = 1 → request bound = **USD 0.0003877500**.
- A full-size request under the profile (256 input tokens) = **USD 0.0004800000**.
- Headroom: 25× below the request cap (0.01), 51× below the session ceiling (0.02), 257× below the USD 0.10 planning ceiling. Reaching the request cap would need about 12 900 input tokens for a 69-byte input.
- Proven by `test_phase6b_profile_is_one_attempt_and_far_below_every_ceiling` and by the dry run against the development stack (§5).
- **Not covered by the bound:** the bill itself. If the provider reports more input tokens than the bound (R81), the request has already been billed; the session halts and the overrun is visible in the output. Reasoning tokens are inside `max_output_tokens`; `gpt-5.4-mini` defaults to reasoning effort `none` (model page, §6).

## 3. Response integrity and telemetry (mock provider responses)

Runner level (`test_response_integrity_is_reported_as_it_is`, `test_provider_failures_are_reported_safely_and_booked_conservatively`, `test_ambiguous_or_final_failures_are_never_retried_even_with_retries_allowed`); adapter level (`test_openai_responses.py`); DB level (`test_live_responses.py`).

| Shape | Exit | `status` | `error_category` | Tokens | `estimated_cost_usd` | Booked | Span |
|---|---|---|---|---|---|---|---|
| completed | 0 | `completed` | — | reported | estimate | estimate | UNSET |
| incomplete (`max_output_tokens`) | 0 | **`incomplete`** + `incomplete_reason` | — | reported | estimate (billed) | estimate | UNSET, `finish_reasons=["max_output_tokens"]` |
| failed | 1 | `failed` | `response_failed` | reported | estimate | estimate | ERROR `openai.response.failed` |
| HTML body | 1 | `malformed` | `malformed_response` | `null` | `null` | bound | ERROR `openai.response.malformed` |
| truncated JSON | 1 | `error` | `malformed_response` | `null` | `null` | bound | ERROR |
| missing usage | 0 | `completed` | — | `null` (unknown) | `null` | bound | UNSET; DB `usage_missing`, cost NULL |
| missing response ID | 0 | `completed` | — | reported | estimate | estimate | no `gen_ai.response.id` |
| 401 / 403 / 429 / quota 429 / 5xx | 1 | `error` | `authentication_failed` / `permission_denied` / `rate_limited` / `quota_exhausted` / `provider_error` | `null` | `null` | bound | ERROR with the HTTP status |
| timeout / connection error | 1 | `error` | `timeout` / `connection_error` (ambiguous: may be billed) | `null` | `null` | bound | ERROR |

- **Incomplete is never a completed answer:** the runner reports `status: incomplete` with the provider's reason (ADR-31); the span keeps OpenAI's reason verbatim; the Trace Explorer shows the allowlisted `gen_ai.response.finish_reasons` attribute. Span status stays UNSET (semconv: a cut-off is not an error) and the trace list shows `ok`; diagnostics do not interpret the Responses vocabulary (R82, accepted). New DB test: `test_incomplete_runner_response_is_stored_as_incomplete_and_priced_once`.
- **No content or secret leakage:** every runner test asserts that the fake key, the ingest key, the input, the output sentinel, the provider error text and (new) the provider request ID are absent from the output; the wire, SDK log, backend log and read API are checked in `test_openai_responses.py` and `test_live_responses.py`; the development dry run was scanned the same way (§5).
- **Trace consistency / no duplicates:** one root span and one CLIENT child per attempt, same trace ID, correct parent, in every runner case (`len(spans) == 2`); the usage ledger key `resp:openai:{id}` makes a re-export idempotent; parent spans carry no usage, so nothing is double counted.
- **Actual vs reserved vs billed:** the output separates `estimated_cost_usd` (usage × verified prices, `null` when unknown), `booked_against_budget_usd` (estimate, or the reserved bound when unknown) and `plan.worst_case_request_usd` (the reservation), and states `cost_basis: "... not a billed amount"`. The billed amount exists only on the provider's usage page.

## 4. Legacy Chat Completions adapter isolation (R80)

- `demo_app/live_openai.py` imports `ObservedOpenAIClient` only to wrap the client for `OpenAIResponsesTextAdapter`, whose single provider call is `create_response`. The source contains neither `chat.completions` nor `create_chat_completion` (asserted).
- `test_the_runner_never_uses_the_legacy_chat_completions_path` replaces `create_chat_completion` with a failure and runs `--execute`: one `POST /v1/responses`, no Chat call.
- The only other caller of the Chat adapter, `demo_app/web.py`, uses an in-process `MockTransport` that answers only `/v1/chat/completions`, a fixed offline key and `synthetic` provenance; it reads no `OPENAI_API_KEY`. The backend has no `openai` package (static test since Phase 4C).
- The Chat path cannot bypass the guard, because the guard is not in it: it has no real entry point in the repository. **No release-blocking bypass exists, so its Phase 2B retry behaviour is unchanged.** R80 stays Accepted for paid Chat use by third-party applications.

## 5. Offline end-to-end dry run (development stack)

Docker Engine 28.5.1 running; `llminsight-postgres-1` started from its existing volume (`docker compose up -d postgres`; nothing reset or wiped); `alembic current` = `0005 (head)`, `alembic check` = no new operations; catalogue already seeded (`seed-prices --dry-run`: insert 0, unchanged 6). The host backend ran on **127.0.0.1:8010** because an unrelated container holds :8000. One project `phase6b-preflight` (id 141) and one ingest key (prefix `7gyWkAAK`) were **added**; nothing was deleted.

Driver: a scratch script outside the repository calling `demo_app.live_openai.main` (the CLI entry point) with the §10 profile, a SYNTHETIC fake transport, `provenance="synthetic"` and all non-loopback connections blocked (0 attempts recorded). Dry run first, then `--execute`.

| Stage | Evidence |
|---|---|
| Dry run | `execution_allowed: true`, `provider_request_sent: false`, `backend: ready`, `max_attempts 1`, `input_token_bound 133`, `worst_case_request_usd 0.0003877500`, `max_request_cost_usd 0.01`, `session_budget_usd 0.02` |
| Runner → adapter | exit 0; exactly 1 request, `POST /v1/responses` |
| Exporter flush | `telemetry.flushed: true`, `spans_exported: 2`, `spans_export_failed: 0` (flush and shutdown happen before the line is printed) |
| Trace ID | `58ff49ee017e9b108f160c362b649e7c` in the output, both DB spans, the `traces` row, the usage row and all API responses |
| Provider / model | `gen_ai.provider.name = openai`; request `gpt-5.4-mini`, response `gpt-5.4-mini-2026-03-17`; `openai.api.type = responses`; tier `default` / `default`; `server.address = api.openai.com` |
| Status | span UNSET, `error_type` NULL; `traces.status = ok`; runner `status: completed` |
| Tokens | input 31, output 2, cache read 0, cache write 0, reasoning 0; `usage_source = provider_reported` |
| Latency | CLIENT span `duration_ns` 127 675 300; root 208 316 000; runner `duration_ms` 219.0; dashboard trace page "208.316 ms" |
| Cost | DB `cost_usd` 0.0000322500, `cost_status computed`, `price_version openai-2026-10-24`, `model_price_id` 6 = runner `estimated_cost_usd` (31 × 0.75 + 2 × 4.50 = 32.25 per 1M) |
| Ledger | 1 usage row, `canonical_call_key resp:openai:resp_SYNTHETIC_phase6b_preflight`, `attempt_id 1` (no duplicate) |
| Commit | rows read back from PostgreSQL by a separate connection after the runner exited |
| API | `/api/v1/traces/{id}`: 2 spans, cost `0.0000322500`; `/api/v1/overview`: 1 synthetic attempt, `cost_complete: true`; `/api/v1/traces` lists the trace |
| Dashboard | production build on :3001 reading the :8010 backend: Overview, Trace Explorer and Trace Detail answer 200 and show the trace ID, both models, `responses`, token counts and "Exact: $0.00003225" |
| No content | input text, completion sentinel, fake provider key, ingest key, admin token and provider request ID absent from DB rows, API bodies, dashboard HTML and the backend log (also no `Authorization`/`Bearer` text and no full `lli_` key in the log) |

## 6. Selected model and verified pricing

- **Model:** `gpt-5.4-mini` (alias; default snapshot `gpt-5.4-mini-2026-03-17`, both in the catalogue). Reasons: verified flat Standard price, no deprecation notice, Responses API supported, reasoning effort default `none` (so 64 output tokens are not spent on hidden reasoning), max 272 000 input tokens (no long-context tier).
- **Catalogue:** `openai-2026-10-24`, `verified_at` / `effective_from` 2026-10-24T21:40:00Z, source `https://developers.openai.com/api/docs/pricing` (alias row) and `/api/docs/models/gpt-5.4-mini` (snapshot row).

| Source | Input | Cached input | Output (USD / 1M) |
|---|---|---|---|
| SDK mirror `live.VERIFIED_PRICES` | 0.75 | 0.075 | 4.50 |
| Backend `pricing_catalog.py` (seeded rows, drift test `test_sdk_price_mirror_equals_the_seeded_catalogue` passed) | 0.75 | 0.075 | 4.50 |
| Published, re-read 2026-10-28T10:56:21Z–10:56:22Z: pricing page (summarizer: Standard table; raw HTML 200, 583 260 bytes, SHA-256 `87658ee9…a5a0`, first data row `["gpt-5.4-mini", 0.75, 0.075, 4.5]`) and model page (raw HTML 200, 459 912 bytes, SHA-256 `7abc1cf1…b4ee`: $0.75 / $0.075 / $4.5, snapshot `gpt-5.4-mini-2026-03-17`, "none (default), low, medium, high and xhigh") | 0.75 | 0.075 | 4.50 |

The other two catalogue models were also unchanged on the raw page (`gpt-5-mini` 0.25/0.025/2, `gpt-4.1-mini` 0.4/0.1/1.6). Tiers listed: Standard, Batch/Flex, Fast, Ultrafast; only Standard (`service_tier=default`) is priced; the 10 % regional and FedRAMP uplifts do not apply on `api.openai.com`. **Nothing was changed** (no new version, no re-dated row).

**14-day rule:** `PRICE_MAX_AGE = 14 days` from `PRICE_VERIFIED_AT`, so the guard refuses real execution after **2026-11-07T21:40:00Z** (`price_catalogue_stale`; boundary tested at exactly 14 days). Today's re-check does **not** extend the window: that needs a new catalogue version in both copies (backend seed + SDK mirror), which is a separate, approved change.

**No automatic real call from the environment:** nothing imports the runner; the CLI defaults to a dry run without `--execute`; with `--execute` the guard needs nine explicit live settings besides the key, a valid telemetry configuration and a ready backend (§2); the backend's `LLMI_ALLOW_REAL_LLM` governs mock-only experiments and has no real executor.

## 7. Corrections made (ADR-31)

| # | Defect | Fix | Regression tests |
|---|---|---|---|
| R85 | `openai` 3.28.0 reads `OPENAI_CUSTOM_HEADERS` (an `Authorization` line replaces the checked key), `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` (billing routing) from the process environment; offline probe with fake values confirmed both effects | `evaluate_policy` refuses any of them (`client_env_override`) | 3 gate cases; 3 runner `--execute` refusals; `test_custom_headers_env_would_replace_the_guarded_key` pins the library behaviour |
| R86 | `create_response` deny-list let `extra_body`, `prompt`, `moderation`, `context_management`, `access_programs` (and any future parameter) through | allowlist `RESPONSES_ALLOWED` | 12 refusal cases; `test_allowlisted_text_settings_are_sent` |
| — | runner printed `incomplete` without the provider's reason | additive `TextOutcome.incomplete_reason`, printed by the runner | adapter, runner and DB tests |
| — | session snapshot printed `"0E-10"` | fixed-point formatting | runner assertion |

Not changed: backend, schema, migrations, frontend, dependencies, lock files, compose files, legacy Chat behaviour, exit codes, the price catalogue.

## 8. Local runtime readiness

| Item | State |
|---|---|
| Docker Engine | running (28.5.1) |
| PostgreSQL | `llminsight-postgres-1` healthy on 127.0.0.1:55432, `0005 (head)`, catalogue seeded (6 rows) |
| Port 8000 | held by the unrelated `ppe-split-main-dashboard-1`; the live run uses the host backend on **8010** (§11). For Playwright only, with the user's approval in this session, that container was stopped and restarted afterwards (nothing removed) |
| Ports 3001–3004, 8001, 8010 | free after the runs |
| Proxy / CA environment (R87) | `HTTP(S)_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE` unset in process, user and machine scope; WinINet proxy disabled, no auto-config URL; `urllib.request.getproxies()` empty |
| OpenAI variables | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, `OPENAI_ADMIN_KEY` unset in every scope |
| SDK venv | local SDK reinstalled non-editable (`pip install --no-deps ./sdk`, README procedure) so the installed copy equals the branch |

## 9. Tests and security results (exact commands)

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, real PostgreSQL) | **1726 passed**, 0 failed, 0 skipped (244.8 s; 1 warning: R20) |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk, live backend, after reinstall) | **539 passed**, 0 failed, 0 skipped (133.3 s) = 501 + 38 new |
| `npm test` (frontend) | **311 passed**, 0 failed, 0 skipped, 0 cancelled |
| `npx playwright test` (real backend on :8000, Edge) | **55 passed**, 0 failed, 0 skipped (2.4 min); teardown deleted its run projects 142–144, `remaining: []` |
| `npm run typecheck` / `npm run lint` | clean / 0 warnings |
| `npm run build` / `npm run check:bundle` | built / 632 files, 23 in the browser bundle, **0 findings** |
| `ruff check`, `ruff format --check` (sdk incl. `../demo_app`) | all passed; 39 files formatted |
| `python -m mypy src`; `python -m mypy --config-file pyproject.toml ../demo_app` (strict) | no issues (10 / 4 files) |
| `pip check` (backend; sdk) | no broken requirements |
| `alembic current` / `alembic check` | `0005 (head)` / no new upgrade operations |
| `docker compose config --quiet`; `docker compose --profile app config --quiet` | valid |
| `python -I scripts/verify_phase0.py` | **797 PASS, 0 FAIL** (before and after the documentation changes) |
| `pip-audit -r <lock> --no-deps --disable-pip -s pypi\|osv` (both locks; pip-audit 2.10.1, Phase 5B throwaway venv) | no known vulnerabilities (4/4) |
| `pip-audit --path <venv>/Lib/site-packages` (both venvs) | `pip` 24.2 only (12 advisories; R75, unchanged since 6A) |
| `npm audit` | 0 vulnerabilities |
| `bandit` 1.9.4 on `live.py`, `openai_adapter.py`, `providers.py`, `demo_app/live_openai.py` | 1 Medium B310 (the readiness probe's `urlopen`, accepted in 6A), 0 High |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` (staged tree) | 828 blobs, 30 messages, 464 working files, 9 local secret values (names only): **0 secret-value hits**; 37 pattern hits (exit 1), all `url_userinfo` (§9a) |
| `git diff --check` | clean (CRLF notices only) |

Dependency changes: none (diff of both locks, both `pyproject.toml`, `package.json`, `package-lock.json` is empty).

### 9a. Secret scan

Run on the staged tree immediately before the commit. All 37 pattern hits are `url_userinfo` at the Phase 5B F9 fixture locations (test redaction vectors and the `SECURITY.md` / `phase-2c.md` example lines); the one more than Phase 6A (36) is the same pre-existing `SECURITY.md` example line in the new blob of that edited file. A first run flagged `bearer_value` in a new test literal (`Bearer SYNTHETIC-…`, 26 characters); it was shortened below the pattern's length before the commit. No hit is in a Phase 6B addition.

## 10. Blockers before the first real request

1. **Architectural acceptance** of ADR-28…30 and the new ADR-31 (`ADR.md` §8–§9).
2. **Explicit written spending approval** for exactly this run: provider `openai`, model `gpt-5.4-mini`, one request, `max_output_tokens` 64, request cap USD 0.01, session ceiling USD 0.02, worst case USD 0.0003877500.
3. **Time window:** the run must start before **2026-11-07T21:40:00Z**; afterwards a new price catalogue version must be verified and released in both copies first.
4. **Operator-side account controls** (§11 steps 1–4) in place.
5. No other blocker found: every gate in §9 passed.

Phase 6B profile (placeholders only; set in the operator's own PowerShell window, never in a file):

| Variable | Value |
|---|---|
| `LLMI_PROVIDER_MODE` | `real` |
| `LLMI_LIVE_CONSENT` | `openai-paid-calls` |
| `LLMI_LIVE_PROVIDERS` | `openai` |
| `LLMI_LIVE_MODELS` | `gpt-5.4-mini` |
| `LLMI_LIVE_MAX_REQUESTS` | `1` |
| `LLMI_LIVE_MAX_INPUT_TOKENS` | `256` |
| `LLMI_LIVE_MAX_OUTPUT_TOKENS` | `64` |
| `LLMI_LIVE_MAX_REQUEST_COST_USD` | `0.01` |
| `LLMI_REAL_LLM_BUDGET_USD` | `0.02` |
| `LLMI_LIVE_TIMEOUT_SECONDS` | `30` |
| `LLMI_LIVE_MAX_RETRIES` | `0` |
| `LLMI_LIVE_MAX_CONCURRENCY` | `1` |

## 11. Exact operator steps for one live test

**No step below is authorized yet.** Do steps 1–9 in your own browser and PowerShell window, not in Claude Code. Never paste a key into Claude Code, a chat, a file or a document. Platform menu names can differ from these descriptions; where a control is missing or behaves differently, stop and report it.

**OpenAI account (platform.openai.com)**
1. **Dedicated project:** create a new project (for example `llm-insight-live-test`) used for nothing else.
2. **Spend controls:** keep the prepaid credit balance small and turn **auto-recharge off**. This is the hardest account-level ceiling. Set a monthly budget on the project (for example USD 1) and an email alert at a low threshold. Check whether the budget blocks requests or only alerts; unless the page says it blocks, treat it as an alert.
3. **Restrict model usage:** in the project's limits, allow only `gpt-5.4-mini` and lower its rate limits (for example a few requests per minute).
4. **Project-scoped key:** create a secret key **inside that project** (restricted permissions where offered: Responses / model requests only). Copy it once into your password manager, nowhere else. Do not create an admin key.

**Local session (a fresh PowerShell window, repository root)**

5. Start the stack (8000 belongs to another container, so use 8010):
   ```powershell
   docker compose up -d postgres
   $env:LLMI_PORT = "8010"; backend\.venv\Scripts\python -m app      # keep this window open
   ```
   In a second window, create a dedicated LLM Insight project; its ingest key is printed once:
   ```powershell
   backend\.venv\Scripts\python -m app.cli create-key --project live-openai
   ```
6. In a third, fresh window (the runner window), make sure nothing reroutes the client, then set the telemetry and the §10 profile:
   ```powershell
   Remove-Item Env:OPENAI_BASE_URL, Env:OPENAI_CUSTOM_HEADERS, Env:OPENAI_ORG_ID, Env:OPENAI_PROJECT_ID, Env:OPENAI_ADMIN_KEY, Env:HTTP_PROXY, Env:HTTPS_PROXY, Env:ALL_PROXY, Env:SSL_CERT_FILE, Env:SSL_CERT_DIR -ErrorAction SilentlyContinue
   $env:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = "http://127.0.0.1:8010/v1/traces"
   $env:OTEL_SERVICE_NAME = "live-openai-smoke"
   $ingest = Read-Host "LLM Insight ingest key" -AsSecureString
   $env:OTEL_EXPORTER_OTLP_TRACES_HEADERS = "X-Project-Key=" + [System.Net.NetworkCredential]::new("", $ingest).Password
   Remove-Variable ingest
   $env:LLMI_DASHBOARD_PROJECT_ID = "<project_id printed in step 5>"
   $env:LLMI_PROVIDER_MODE = "real";            $env:LLMI_LIVE_CONSENT = "openai-paid-calls"
   $env:LLMI_LIVE_PROVIDERS = "openai";         $env:LLMI_LIVE_MODELS = "gpt-5.4-mini"
   $env:LLMI_LIVE_MAX_REQUESTS = "1";           $env:LLMI_LIVE_MAX_INPUT_TOKENS = "256"
   $env:LLMI_LIVE_MAX_OUTPUT_TOKENS = "64";     $env:LLMI_LIVE_MAX_REQUEST_COST_USD = "0.01"
   $env:LLMI_REAL_LLM_BUDGET_USD = "0.02";      $env:LLMI_LIVE_TIMEOUT_SECONDS = "30"
   $env:LLMI_LIVE_MAX_RETRIES = "0";            $env:LLMI_LIVE_MAX_CONCURRENCY = "1"
   ```
7. **Set the OpenAI key for this window only** (`Read-Host` input is not written to PowerShell history):
   ```powershell
   $sec = Read-Host "OpenAI project key" -AsSecureString
   $env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new("", $sec).Password
   Remove-Variable sec
   ```
8. **Dry run (no request is sent):**
   ```powershell
   sdk\.venv\Scripts\python -m demo_app.live_openai --dashboard-url http://127.0.0.1:3001
   ```
   Confirm: `"execution_allowed": true`, `"refusals": []`, `"backend": {"ready": true, ...}`, `"max_attempts": 1`, `"max_requests": 1`, `"max_output_tokens": 64`, `"worst_case_request_usd": "0.0003877500"`, `"session_budget_usd": "0.02"`, `"model": "gpt-5.4-mini"`. Anything else: stop.
9. **Explicit authorization:** only after the reviewer's written approval of ADR-28…31 and of the spend in §10, run **once**:
   ```powershell
   sdk\.venv\Scripts\python -m demo_app.live_openai --execute --dashboard-url http://127.0.0.1:3001
   ```
   then **immediately** clear the authorization and the key from the window:
   ```powershell
   Remove-Item Env:OPENAI_API_KEY, Env:LLMI_LIVE_CONSENT, Env:LLMI_PROVIDER_MODE
   ```
   Do not re-run on a failure or timeout: an ambiguous outcome may already be billed.
10. **Record the result (no key, no output text):** the printed JSON line (trace ID, status, `incomplete_reason`, models, tokens, `duration_ms`, `estimated_cost_usd`, `booked_against_budget_usd`, `session.halted`); compare reported `input_tokens` with the bound 133 (R81); open the trace (dashboard on :3001 started with `$env:LLMI_API_BASE_URL = "http://127.0.0.1:8010"`, or `/api/v1/traces/{id}?project=…`); later compare the stored cost with the provider's usage page and say which is the estimate and which is the bill; finally revoke or delete the OpenAI project key if no further run is planned.

## 12. Verdict

The preflight acceptance gate is met: the live path is verified offline end to end on the development stack (CLI runner → Responses adapter → SDK span → OTLP → FastAPI → PostgreSQL → API → dashboard), every regression gate passed, the worst case for the first request is USD 0.0003877500 under a USD 0.02 ceiling, and the two defects found (R85, R86) are fixed with regression tests and recorded as ADR-31. **No real or paid request has been made. Stopped for architectural and explicit spending approval; the real run (§11 step 9) is not authorized by this report.**
