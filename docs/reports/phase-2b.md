# Phase 2B — OpenAI Adapter, Model Pricing and Cost Engine

- **Date:** 2026-10-24 (UTC; the machine's local date was already 2026-10-25, UTC+3).
- **Baseline:** Phase 2A commit `169e6d0`, tag `phase-1` (backend 876 passed, SDK 247 passed).
- **Scope:** the `model_prices` migration and deferred FK, a small verified OpenAI price catalogue, a Decimal cost engine applied at ingest, and an OpenAI Chat Completions adapter in the SDK, tested offline only.
- **Not done (by instruction):** no real OpenAI call, no paid service, no frontend, no aggregates, no FastAPI demo, no automatic repricing, no `phase-2` tag, no Phase 2C.
- **Commit:** one project-only commit on `main` (the hash is in the closure message). The home-directory repository, the global Git config and other containers were not touched.
- All telemetry and every OpenAI response used in this phase is **SYNTHETIC**. The prices are real (verified); the token counts are made up.

## 1. Start-of-phase verification (Task A)

| Check | Result |
|---|---|
| `git rev-parse --show-toplevel` | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi`; branch `main`; clean tree at `169e6d0`; tags `phase-0`, `phase-1` |
| PostgreSQL | `llminsight-postgres-1` `Up (healthy)` on `127.0.0.1:55432`; dev DB `alembic current` = `0001 (head)`, 0 rows in every table |
| SDK packaging | `sdk/.venv` with `llm-insight-sdk` 0.1.0 installed non-editable |
| Baseline suites (before any edit) | backend `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` → **876 passed**, 1 warning (R20); SDK `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` → **247 passed** |
| Compose | `docker compose config --quiet` exit 0 |
| Contracts read | CLAUDE.md, MVP_MASTER_PLAN (pricing, §E, Phase 2), ADR, ARCHITECTURE §4.6–4.7/§4.13/§6/§7, TELEMETRY_CONTRACT (all), METRICS_CATALOG, SECURITY, TEST_MATRIX, PHASE_STATUS, phase prompts §3, `reports/phase-1-final.md`, `reports/phase-2a.md` |

## 2. Contract check and design decisions

**No conflict with ADR-01…21 required a stop, and no column was added or changed.** The locked §4.7 columns are created exactly; the migration test parses `ARCHITECTURE.md` and compares column by column.

**Contract-gap analysis (Task B).** The schema stores one price per `(provider, model, effective_from)` and has no column for a processing tier, a regional uplift, a long-context threshold or cache-write durations. Those dimensions cannot be represented, so a stop would have been needed *if* a catalogued model required them. Instead:
- the catalogue contains only models whose Standard text price is flat (no long-context row, no cache-write price; `gpt-5.4-mini` is capped at 272,000 input tokens);
- a stored price is defined as the **standard-tier, global-endpoint** price, and the engine applies it only when the span proves that (`openai.response.service_tier = default`, `server.address = api.openai.com`, both already allowlisted in `llminsight.attrmap.v1`); everything else is `price_missing`, as the instruction requires for unsupported modes and tiers.

This needs no schema change. Extending coverage to tiered models later needs new columns, a migration and an ADR note (R29).

**Validity periods** are succession-based: a row is valid from `effective_from` until the next row of the same `(provider, model)`. `UNIQUE(provider, model, effective_from)` (locked) makes overlap impossible. History is protected by triggers (append-only, no back-dating) and the FK `ON DELETE RESTRICT`.

**Refinements of the cost rules** (recorded in `TELEMETRY_CONTRACT.md` §4 and `PRICING.md`; no ADR change):
1. The `requested_model` fallback applies only when the span has **no** `response_model`. A served snapshot without a price row is `price_missing`, never priced at its alias's rate.
2. Only `usage_source = provider_reported` usage is priced. Estimated or unknown usage is `usage_missing` (T17). One Phase 1 unit expectation (`estimated` → `price_missing`) was updated to `usage_missing` on purpose.
3. A row is priced once, inside the ingest transaction that first inserts it, and never updated.

**Adapter decisions (Task E):**
- **Surface:** `chat.completions.create`, non-streaming (`openai.api.type = chat_completions`), the master plan's `chat` operation. Streaming, `stream_options`, Azure clients and non-`api.openai.com` base URLs are refused before any request (the backend prices only that endpoint).
- **Physical attempts:** the wrapped client is used through `with_options(max_retries=0)`, because the OpenAI SDK's own retries would hide several billable requests inside one span. The adapter retries 408/409/429/5xx/connection/timeout errors (the OpenAI SDK's rule), one span per request, sharing one UUIDv4 `call_id`.
- **Provenance is declared** by the caller (`real` | `synthetic`, no default). `real` with an `httpx2.MockTransport` is refused. The public `Telemetry.inference_span` still only emits `synthetic`; only the adapter passes `real`.
- **Isolation:** `openai` is an optional extra (`llm-insight-sdk[openai]`), imported only by `llm_insight_sdk.openai_adapter`; tests prove the core import never loads it and no other module mentions it.
- The SDK version is 0.2.0 (a new public module).

**Dependencies (pinned, two-week rule):** `openai` 3.28.0 (uploaded 2026-10-09) and its new transitives in `sdk/.venv` only (`ARCHITECTURE.md` §6 Phase 2B note). The backend gained no dependency.

## 3. What was built

**Backend**
- `backend/alembic/versions/0002_phase2_model_prices.py`: `model_prices` (§4.7, 11 checks incl. non-negative prices, `currency = 'USD'`, `https://` sources, identifier-format provider/model), `fk_usage_records_model_prices` (`ON DELETE RESTRICT`), trigger function `model_prices_append_only()` (rejects `UPDATE`, `DELETE`, `TRUNCATE … CASCADE` and back-dated inserts; advisory lock per `(provider, model)`), downgrade refused while any usage row references a price.
- `app/models/pricing.py` (`ModelPrice`), FK added to `app/models/usage.py`.
- `app/services/pricing.py`: the engine (`assess`, `usage_unknown_reason`, `price_applicable`, `priced_model`, `select_price`, `compute_cost`). Pure functions, `Decimal` only (floats raise `TypeError`).
- `app/services/pricing_catalog.py`: the verified catalogue `openai-2026-10-24` (6 rows).
- `app/repositories/pricing.py`: one-query price lookup per ingest batch; `seed()` (insert missing, report unchanged, refuse differing rows, `LOCK TABLE`).
- `app/services/ingestion/usage.py`, `app/repositories/telemetry.py`: new usage rows are priced in the ingest transaction before `INSERT … ON CONFLICT DO NOTHING`.
- `app/cli.py`: `seed-prices [--dry-run]`.

**SDK** (`llm-insight-sdk` 0.2.0)
- `src/llm_insight_sdk/openai_adapter.py`: `ObservedOpenAIClient`, `ObservedAsyncOpenAIClient`, `classify_openai_error`, `is_retryable`, `usage_from`, `UnsupportedRequestError`.
- `instrumentation.py`: `provenance` accepts `real` | `synthetic` (internal span helper only), a pluggable error classifier, and `InferenceRecorder.set_metadata()` restricted to allowlisted OpenAI/server keys with the backend's value rules. `_guards.py`: `HOST` and `FINGERPRINT` rules mirrored from the backend.
- `pyproject.toml`: extra `openai = ["openai==3.28.0"]`; `requirements-dev.lock` regenerated.

## 4. Tests: exact commands and results

Windows 11, Python 3.12.6, PostgreSQL 17 container. `LLMI_TEST_REQUIRE_DB=1` makes an unreachable database fail instead of skip. Final gate run after all changes:

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (backend) | **1024 passed, 0 failed, 0 skipped**, 1 warning (R20) | 0 |
| 2 | `ruff check .` / `ruff format --check .` (backend) | `All checks passed!` / `76 files already formatted` | 0 / 0 |
| 3 | `python -m pip check` (backend venv) | `No broken requirements found.` | 0 |
| 4 | `python -m pytest tests/unit -q -rs` (sdk) | **298 passed**, 0 skipped | 0 |
| 5 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration -q -rs` (sdk; live backend) | **21 passed**, 0 skipped | 0 |
| 6 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (sdk, complete) | First gate run: **1 failed, 318 passed** (intermittent, §4.3). After the fix and diagnostics: **319 passed, 0 failed, 0 skipped** in each of the last 7 complete runs (5 before, 2 after the final test edits) | 1 → 0 |
| 7 | `ruff check .` / `ruff format --check .` (sdk), same with `--config pyproject.toml ../demo_app` | all `All checks passed!` / already formatted | 0 |
| 8 | `python -m mypy src` (strict) / `python -m mypy --config-file pyproject.toml ../demo_app` | `Success: no issues found in 8 source files` / `… in 2 source files` | 0 / 0 |
| 9 | `python -m pip check` (sdk venv) | `No broken requirements found.` | 0 |
| 10 | `alembic upgrade head` / `alembic current` / `alembic check` (backend, dev DB) | `0001 -> 0002` / `0002 (head)` / `No new upgrade operations detected.` | 0 |
| 11 | Migration cycle (pytest, disposable DBs): `head → 0001 → base → head`, schema identical; upgrade `0001 → 0002` with existing telemetry; refused downgrade | pass (in #1) | — |
| 12 | `docker compose config --quiet`; `docker compose ps` | exit 0; `llminsight-postgres-1 Up (healthy)` | 0 |
| 13 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 711 PASS, 0 FAIL` after the contract-doc edits; **`717 PASS, 0 FAIL`** after the matrix, status and this report (each new `(phase-2b)` status is checked against this report) | 0 |
| 14 | `git diff --check` (root) | no whitespace errors (`core.autocrlf` notices only) | 0 |
| 15 | `python -m app.cli seed-prices --dry-run` (backend, dev DB) | `would insert 6, unchanged 0 (catalogue openai-2026-10-24)`; dev DB still 0 price rows | 0 |
| 16 | Mutation check (scratch script; each defect injected, guarding tests run, file restored) | **15 / 15 detected** (list below) | — |

**Mutations detected (15/15):** reasoning added to output; cached tokens not subtracted from input; binary-float arithmetic; half-up rounding; earliest instead of in-force price; alias fallback despite a response model; tier/endpoint rule ignored; estimated usage priced; missing cached price falling back to the full input rate; ingest not pricing at all; adapter keeping the client's own retries; adapter exporting message content; adapter inventing a 0 cache-read count; `real` provenance accepted on a mock transport; adapter recording the provider error message.

**New and changed test files:**
- backend `tests/unit/test_pricing.py` (72): hand-computed T19 costs for all three models, T20/T21 subset cases, cache-write subset, absent subsets, known zero, five half-even rounding cases, single rounding of the exact sum, overflow → `usage_inconsistent`, BIGINT exactness, Decimal-only prices, usage rules, `price_missing` cases, exact-model matching (7 near-miss names), event-time boundaries (inclusive start, closing successor, row order), applicability (10 tier/endpoint cases).
- backend `tests/unit/test_pricing_catalog.py` (6): catalogue = the `PRICING.md` table value for value; well-formed; only official sources, no synthetic row; snapshots cite their model page; tiered models absent; never dated in the future.
- backend `tests/integration/test_model_prices.py` (41): column rules (16), required columns (6), uniqueness, append-only (UPDATE ×3, DELETE, TRUNCATE CASCADE, plain TRUNCATE), later version allowed, back-dating refused, per-model scope, FK RESTRICT with the trigger disabled, seed CLI (dry run, exactly once, idempotent, conflicting row refused with nothing written, same-start clash, never touches `usage_records`).
- backend `tests/integration/test_ingest_cost.py` (25): through real `POST /v1/traces` (protobuf) → PostgreSQL → read API.
- backend `tests/integration/test_migrations.py`: table set, FK, unique constraints, 64 checks, triggers, upgrade with data, refused downgrade, dangling-FK rejection.
- backend Phase 1 files updated for intended changes: `test_models.py` (6 tables; FK now present), `test_constraints.py` (computed row references a real synthetic price), `test_normalization_hardening.py` (estimated → `usage_missing`).
- SDK `tests/unit/test_openai_adapter.py` (58) and `tests/openai_fixtures.py`; `tests/unit/test_install.py` (+3); `tests/integration/test_openai_live.py` (11); `tests/integration/test_live_backend.py` (cost assertion scoped to the mock provider, which is never priced); `tests/integration/conftest.py` (`log_tracebacks()` diagnostics, §4.3).

### 4.1 Real integration evidence (live `python -m app`, offline OpenAI)

`sdk/tests/integration/test_openai_live.py` runs against the Phase 2A harness (backend subprocess from `backend/.venv`, disposable `llminsight_sdk_it_test`, dropped afterwards), after `python -m app.cli seed-prices`. The OpenAI side is a SYNTHETIC fixture behind `httpx2.MockTransport`, with every non-loopback resolution and connection blocked.

| Scenario | Evidence (database / API) |
|---|---|
| Success, `gpt-5.4-mini` → `gpt-5.4-mini-2026-03-17`, 1200 in (1000 cached) / 300 out (200 reasoning), tier `default` | 1 transport request, 1 usage row; tokens exactly as reported, `cache_write_input_tokens` NULL (not reported); `cost_usd = 0.0015750000`, `computed`, `price_version = openai-2026-10-24`, `model_price_id` = the snapshot row; span `server_dropped_attributes_count = 0`, child of the root |
| Read API | detail: `"0.0015750000"` and `null` + `price_missing` for an uncatalogued `gpt-5.4` call; list: `known_cost_usd "0.0015750000"`, `unknown_cost_calls 1`, `provenance synthetic`, `provenance_verified false` |
| 429, 503, then success | 3 transport requests = 3 attempts = 3 rows, one `call_id`; failed attempts NULL tokens, `usage_missing`, `sdk_attempt` keys; the third computed; span `error_type` `429`, `503` |
| Same spans exported twice more through a second exporter | rows identical (ids, costs, `created_at`) |
| `gpt-5.4`, unknown snapshot, `flex`, Fast (`priority`), tier not reported | `price_missing`, cost NULL, tokens still stored |
| No usage object | NULL tokens, `unknown`, `usage_missing` |
| Leak check over DB rows of the project, the detail API and the backend log | no fake OpenAI key, no project key, no prompt/system/completion sentinel, no provider error text, no OpenAI request ID, no traceback |

**Usage versus transport versus cost:** each live test asserts the three separately: the number of physical requests the fake transport received; the provider-reported token counts copied from the fixture's `usage` object (never estimated); and the computed cost, an estimate from those tokens and the stored price row.

**Backend ingest evidence** (`test_ingest_cost.py`): the same worked example through the TestClient; a root span carrying usage-like attributes produces no usage row (no parent double count); re-export ×3 leaves the row identical; double instrumentation keeps the first cost; each retry attempt priced on its own; 9 `price_missing` paths (uncatalogued model, unknown snapshot, provider case, `flex`, `priority`, no tier, regional host, no host, cache writes without a price); events before the catalogue; usage problems never priced; event-time selection across two synthetic versions incl. the exact boundary; **T22**: a later version never rewrites a stored cost, and an unknown row is not repriced when a price appears (only new spans use it); NUMERIC text `0.0000000004` stored for a half-even case; 50 spans priced from one lookup.

### 4.2 Test IDs

| ID | Status | Evidence | Still missing |
|---|---|---|---|
| T09 | **PASS (phase-2b)** | OpenAI CLIENT span `chat gpt-5.4-mini` with the required `gen_ai.*`, `openai.*`, `server.*` and declared provenance (unit, exact attribute set; live rows) | Real smoke NOT RUN (no approval) |
| T10 | PARTIAL (phase-2b) | Adapter spans are children of the current span, sync and async (`gather`) | The FastAPI demo root span (Phase 2C) |
| T11 | **PASS (phase-2b)** | OpenAI `usage` → `gen_ai.usage.*` incl. cached/cache-write/reasoning; unreported fields absent; `provider_reported` in the DB | — |
| T12 | **PASS (phase-2b)** | 429, 5xx, timeout, connection error, 4xx: `ERROR`, `error.type`, attempt recorded, retry rule, the provider exception reaches the app unchanged | — |
| T15 | **PASS (phase-2b)** | Fake OpenAI key, `Authorization`, custom headers and the project key absent from raw OTLP bodies, the DB, the API and the SDK/backend logs | Re-verified in Phase 5 |
| T16 | **PASS (phase-2b)** | Messages, tools, completion text and provider error text absent everywhere (unit raw bodies + live DB/API/log); backend drop half from Phase 1 | Re-verified in Phase 5 |
| T17 | **PASS (phase-2b)** | Only provider-reported usage is priced; `estimated` → `usage_missing` (unit + ingest); the SDK never estimates | — |
| T18 | **PASS (phase-2b)** | Missing usage → NULL tokens, `unknown` + `usage_missing`; API `null` (unit, ingest, live) | UI in Phase 3 |
| T19 | **PASS (phase-2b)** | Hand-computed Decimal costs equal the stored values exactly (3 models; DB and API) | — |
| T20 | **PASS (phase-2b)** | Cached (and cache-write) subsets priced once, never added to input (unit; mutation detected) | — |
| T21 | **PASS (phase-2b)** | Reasoning tokens inside output, not added again (unit; mutation detected) | — |
| T22 | **PASS (phase-2b)** | `price_version` + `model_price_id` stored; a later version and a newly added price never rewrite history | — |
| T23 | **PASS (phase-2b)** | Re-export → one row, cost unchanged; T23-S1 double instrumentation keeps the first cost; T23-S2 key precedence with priced rows | Re-verified in Phase 5 |
| T31 | **PASS (phase-2b)** | `0002` cycle, schema = contract (§4.2–4.7), upgrade with existing data, refused downgrade, `alembic check` | Re-verified in Phase 5 |

Unchanged: T13 and T14 stay `PASS (phase-2a)` (also re-exercised by the adapter: backend-down fail-open, per-attempt rows). **Not run:** T24, T29, T32 (aggregates, Phase 2C); the real smoke run (no approval, no key used).

### 4.3 Intermittent failures found by the gate (reported, not hidden)

The first complete SDK gate run failed once (`1 failed, 318 passed`); the unit and live halves had passed separately just before. The gate command kept only the summary line, so that run's test name was not captured. Investigation over about 45 further runs found **two different intermittent failures**:

1. **Test bug, fixed.** `test_each_physical_attempt_is_one_ledger_row` ordered retry spans by `start_ns`. With a zero-latency fake transport, consecutive attempts can share one Windows clock tick, so the order is arbitrary (`['503', '429']`). Phase 2A hit the same pitfall. The test now orders by `llminsight.attempt`. 20 live runs and 7 complete runs passed afterwards.
2. **Backend-log traceback, root cause not confirmed (R32).** Once, a live leak check (`'Traceback' not in <backend log>`) failed: the backend's own log contained a traceback from an asyncio event-loop callback (`asyncio/events.py`, `_run`). The visible fragment is consistent with the known Windows Proactor message logged when a client resets a keep-alive connection (`_ProactorBasePipeTransport._call_connection_lost`), but the full text was lost (pytest's temp directory rotated before it could be read). It did not recur in ~45 runs. The check was **not** weakened: both leak tests now first assert `log_tracebacks() == []`, which prints the exact 40-line block if it happens again. No secret or content was visible in the fragment.

## 5. Pricing sources, equations and rounding

Full contract: `docs/PRICING.md`.

| Model | Input | Cached input | Cache write | Output | Source |
|---|---|---|---|---|---|
| `gpt-5.4-mini`, `gpt-5.4-mini-2026-03-17` | 0.75 | 0.075 | — | 4.50 | pricing page; model page |
| `gpt-5-mini`, `gpt-5-mini-2025-08-07` | 0.25 | 0.025 | — | 2.00 | pricing page; model page |
| `gpt-4.1-mini`, `gpt-4.1-mini-2025-04-14` | 0.40 | 0.10 | — | 1.60 | pricing page; model page |

USD per 1M tokens, version `openai-2026-10-24`, `effective_from = verified_at = 2026-10-24T21:40:00Z`.

- **How it was verified:** the pricing page (`https://developers.openai.com/api/docs/pricing`) was read three times through the page summarizer; its tables are rendered client-side, so the raw HTML has no price rows (R31). The three model pages were read through the summarizer **and** downloaded raw: all three price triples and snapshot IDs match by grep. The deprecations page gives the shutdown dates (`gpt-5-mini` 2026-12-11, `gpt-4.1-mini` 2027-01-14).
- **Dating error found and fixed before any row was stored:** the first draft dated the catalogue `openai-2026-10-25` at 2026-10-25T00:00Z, from the machine's *local* date. That claimed hours without evidence. The live tests exposed it (calls made at 21:3x UTC were correctly `price_missing`). The prices were re-checked (unchanged, by 21:38:20Z) and the catalogue re-dated in UTC; `test_catalogue_is_never_dated_in_the_future` guards it.
- **Equation:** `((in − cache_read − cache_write)·p_in + cache_read·p_cached + cache_write·p_cache_write + out·p_out) / 10^6`. Reasoning is inside `out`. A non-zero term without a price → `price_missing`.
- **Rounding:** exact `Decimal` (60-digit context), divided once, rounded once **half-even** to 10 places (`NUMERIC(20,10)`). Costs above the column maximum → `usage_inconsistent`. With the verified catalogue every per-token price has ≤ 10 decimals, so real costs are exact.

**Limitations:** stale prices (R28); narrow coverage by design (R29); no discounts, credits, taxes or organisation pricing; client-asserted tier/endpoint/usage (R16); the catalogue is not seeded in the dev DB (run `seed-prices` when wanted).

## 6. Repricing and backfill

Nothing reprices automatically: usage rows are written once and never updated; `seed-prices` never touches them; computed rows keep their price, `unknown` rows stay unknown (both tested). A future, separately authorized workflow is specified in `PRICING.md` §6: explicit approval naming version, project, window and reason; an ADR and an append-only revision table; dry-run report first; per-change audit (old/new values, run id, approver, reason); rollback by run id; `usage_missing` / `usage_inconsistent` rows never repriced. Not implemented.

## 7. Requirement checklist

| Task | Requirement | Evidence |
|---|---|---|
| A | Baseline verified | §1 |
| B | §4.7 table, FK, nullable references kept, data preserved, NUMERIC, non-negative, validity, no overlap, history, no silent mutation, upgrade/downgrade/data tests | §2, §3; `test_migrations.py`, `test_model_prices.py` |
| C | Official sources, exact IDs, input/output/cached, cache write where applicable (none), dates, currency, source and verification date; no guesses; aliases ≠ snapshots without evidence; unknown for tiers/long context/tools; small catalogue; synthetic math fixtures | §5; `PRICING.md` §2–3, §7; `test_pricing_catalog.py` |
| D | Decimal only; provider usage; subsets once; validation; the three unknown reasons; rounding; exact `model_price_id`; event-time match; idempotent; no repricing; backfill documented | §2, §5, §6; `test_pricing.py`, `test_ingest_cost.py` |
| E | Real OpenAI SDK interface, fake transports; one surface; isolation; no content/keys/errors; attributes; UUID call IDs per attempt; response IDs only from completions; usage fields only when reported; success/failure/timeout/retry; context; fail-open; errors propagate; synthetic provenance | §2, §3; `test_openai_adapter.py` |
| F | SDK → genuine OTLP protobuf → PostgreSQL; verified-price cost; missing price; duplicates; per-attempt retries; read API; no network escape; transport vs provider usage vs cost | §4.1; `test_openai_live.py` |
| G | All listed regression areas and commands | §4 |
| H | Status, matrix, architecture/pricing docs, this report | `PHASE_STATUS.md`, `TEST_MATRIX.md`, `ARCHITECTURE.md`, `TELEMETRY_CONTRACT.md`, `METRICS_CATALOG.md`, `SECURITY.md`, `SOURCES_AND_RISKS.md`, `PRICING.md`, `sdk/README.md` |

## 8. ADR compliance

- **ADR-01:** the SDK never imports the backend (`test_source_never_imports_the_backend`); the catalogue lives in the backend only.
- **ADR-02 / ADR-21:** Alembic only; §4.7 column for column; no column change; extra checks and triggers documented in `ARCHITECTURE.md` §4.7.
- **ADR-03 / ADR-04:** adapter spans travel over the unchanged OTLP/HTTP protobuf path.
- **ADR-05:** one provider adapter (OpenAI), isolated in one module behind an optional extra; `provider` and `model` stay data.
- **ADR-06 / ADR-09:** fail-open adapter; export failures never affect the call.
- **ADR-07 / ADR-19:** metadata only; content, keys and error text never exported (tested).
- **ADR-11:** versioned prices, `Decimal`, unknown = `NULL` + reason.
- **ADR-13 / ADR-14 / ADR-15:** `chat` operation, `gen_ai.provider.name = openai`, semconv pin `9ffc7e00c639` unchanged (OpenAI attribute names re-read at the pin), `attrmap.v1` unchanged.
- **CR-09:** pins recorded in `ARCHITECTURE.md` §6; two-week rule kept.

## 9. Risks and limitations

- **R28** stale catalogue; **R29** narrow coverage; **R30** the `openai` library's own DEBUG logs print request details; **R31** summarizer-based evidence for the pricing page (mitigated by raw model pages) — all in `SOURCES_AND_RISKS.md`.
- **Late catalogue additions are not retroactive** by design: a span ingested before a price row exists stays `price_missing`, even if the new row's `effective_from` covers its event time. Only the authorized backfill (§6) could change that.
- **OpenAI response `service_tier`** is reliable only when the request sets it; callers who want a computed cost must pass `service_tier="default"` (documented).
- **Retries:** the adapter's back-off is linear and ignores `Retry-After`.
- **Trigger bypass:** the table owner can disable the triggers; FK `RESTRICT` still protects applied prices (tested).
- **Unchanged:** R08 residual, R16, R23, R24, R26, R27. Public deployment remains unauthorized. Costs are estimates, not invoices.
- **R32 intermittent backend-log traceback** (§4.3): one occurrence in about 45 live runs, root cause unconfirmed, diagnostics added; the leak checks stay strict.
- **Test-output hygiene:** a failing pytest assertion once printed a disposable DB URL including the local generated password to this terminal (not to any file or commit). The new pricing tests wrap their URL fixture so its repr is redacted; older tests that take URL fixtures still could. The password is the local `.env` value.

## 10. Phase 2C prerequisites

1. Your review of Phase 2B, including the catalogue choice (§5) and the refinements in §2.
2. Decide whether to seed the dev database (`python -m app.cli seed-prices`).
3. Aggregate services M01–M15 with provenance rules and `n`, then T24, T29, T32.
4. The FastAPI demo root span (completes T10); optionally an OpenAI mode in the demo.
5. A real smoke run only with your explicit approval, an `OPENAI_API_KEY` you provide and a budget; otherwise it stays NOT RUN.
6. Before any cost-relevant demo: re-verify the catalogue (R28), ideally with a human look at the pricing page (R31).

## 11. Verdict

**Phase 2B: complete, pending your review.** The final acceptance runs listed in §4 pass with 0 failures and 0 skips; the first complete SDK run had one intermittent failure, explained in §4.3 (one test bug fixed, one unconfirmed backend-log traceback tracked as R32). No real OpenAI request was made, no paid service was used, no historical row was repriced, and the dev database holds the new schema but no prices. Phase 2C starts only on your instruction.
