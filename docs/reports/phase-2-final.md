# Phase 2 — Final Acceptance and Release Closure

- **Date:** 2026-10-25 (UTC; the machine's local clock is UTC+3).
- **Decision (user, Phase 2 final instruction):** Phases 2A (`169e6d0`), 2B (`720fedc`) and 2C (`7b3fa28`) are technically accepted **for the OFFLINE MVP scope**. This task is final verification and closure only.
- **Scope:** baseline verification, pre-seed price re-verification, seeding the **local development database** (approval limited to it), final regression, recording the remaining limitations, closure documentation, the annotated `phase-2` tag.
- **Not done (by instruction):** no real OpenAI request, no paid API usage, no code change, no schema change, no push, no Phase 3 work.
- **Untouched:** the home-directory repository, the global Git config, the Windows PostgreSQL service on port 5432, other containers and volumes. No database was dropped.

## 1. Task 1 — baseline

| Check | Command | Result |
|---|---|---|
| Independent Git root | `git rev-parse --show-toplevel` | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` |
| Branch | `git branch --show-current` | `main` |
| Phase 2C commit | `git rev-parse HEAD` | `7b3fa2874422698f0407acb140e1e02ea963626e` (`7b3fa28`) |
| Clean tree | `git status --porcelain` | empty |
| Tags | `git tag -l -n1` | `phase-0`, `phase-1` present; no `phase-2` |
| PostgreSQL | `docker compose ps` | `llminsight-postgres-1` `Up (healthy)`, `127.0.0.1:55432->5432/tcp` |
| Compose | `docker compose config --quiet` | exit 0 |
| Alembic | `alembic current` (backend) | `0002 (head)` |

## 2. Task 2a — price re-verification before seeding

The six rows of catalogue `openai-2026-10-24` (`backend/app/services/pricing_catalog.py`, identical to `PRICING.md` §2) were checked against the documented sources at **2026-10-25T06:38Z** in two independent ways.

1. **Page summarizer** for the pricing page, the three model pages and the deprecations page.
2. **Raw HTML** for the pricing page and the three model pages (`curl`, 2026-10-25T06:38:13Z–06:38:16Z, HTTP 200 each), searched with a scratch script. On the pricing page the price rows are embedded as data: the **first** row per model is the Standard tier, and the later rows are the half-price Batch/Flex tiers.

| Model / default snapshot | Catalogue (input / cached / output, USD per 1M) | Pricing page raw (first row) | Model page raw | Summarizer |
|---|---|---|---|---|
| `gpt-5.4-mini` / `gpt-5.4-mini-2026-03-17` | 0.75 / 0.075 / 4.50 | 0.75 / 0.075 / 4.5 | 0.75 / 0.075 / 4.50; only snapshot listed; not deprecated | match |
| `gpt-5-mini` / `gpt-5-mini-2025-08-07` | 0.25 / 0.025 / 2.00 | 0.25 / 0.025 / 2 | 0.25 / 0.025 / 2.00; only snapshot listed; deprecated | match |
| `gpt-4.1-mini` / `gpt-4.1-mini-2025-04-14` | 0.40 / 0.10 / 1.60 | 0.4 / 0.1 / 1.6 | 0.40 / 0.10 / 1.60; only snapshot listed; deprecated | match |

No long-context row and no cache-write price exist for these models (summarizer), so the catalogue's flat-price assumption still holds. Lifecycle is unchanged: `gpt-5-mini` shuts down 2026-12-11 (replacement `gpt-5.6-terra`), `gpt-4.1-mini` 2027-01-14 (replacement `gpt-6-luna`), and `gpt-5.4-mini` is not on the deprecations page. **Outcome:** all six entries verified; the catalogue was not changed.

## 3. Task 2b — seeding the local development database

**Target identity, confirmed before any write** (scratch script; host, port, database and user only, never the password):

| Property | Value |
|---|---|
| Client URL | `postgresql+psycopg`, host `127.0.0.1`, port `55432` (the compose port), database `llminsight`, user `llminsight` |
| Server | `current_database() = llminsight`, container address `172.19.0.2:5432`, `PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2)`, the compose container, not the Windows service on 5432 |
| Schema | `alembic_version = 0002` |
| Before | 0 rows in `projects`, `project_api_keys`, `traces`, `spans`, `usage_records`, `model_prices` |
| Usage ledger fingerprint | md5 over (id, cost, status, reason, price version, price FK) of all usage rows = `d41d8cd98f00b204e9800998ecf8427e` (empty ledger) |
| Server time zone | `Etc/UTC` |

| Step | Command (backend) | Output | Exit |
|---|---|---|---|
| Dry run | `python -m app.cli seed-prices --dry-run` | 6 × `would insert …`; `would insert 6, unchanged 0 (catalogue openai-2026-10-24)` | 0 |
| Seed | `python -m app.cli seed-prices` | 6 × `inserted …`; `inserted 6, unchanged 0 (catalogue openai-2026-10-24)` | 0 |
| Second run | `python -m app.cli seed-prices` | `inserted 0, unchanged 6 (catalogue openai-2026-10-24)` | 0 |

**Verification after both runs** (scratch script comparing every stored row with `pricing_catalog.ENTRIES`):

- 6 rows; 0 duplicate `(provider, model)`; models exactly `gpt-5.4-mini`, `gpt-5.4-mini-2026-03-17`, `gpt-5-mini`, `gpt-5-mini-2025-08-07`, `gpt-4.1-mini`, `gpt-4.1-mini-2025-04-14`, provider `openai`, version `openai-2026-10-24`, currency `USD`.
- Prices are `numeric(20,10)` and equal the catalogue `Decimal`s exactly (e.g. `0.0750000000`, `4.5000000000`); `cache_write_input_per_million` is `NULL` on every row.
- `effective_from` and `verified_at` are `timestamp with time zone`, both `2026-10-24T21:40:00Z` in UTC; `source_url` is the pricing page for aliases and the model page for snapshots.
- Result: `ALL MATCH: True`, after the first run and again after the second run.
- **Usage ledger:** still 0 rows and the same fingerprint, so no historical usage cost was modified. The seed command never touches `usage_records` (`PRICING.md` §6). No backfill exists or was run.
- After the full test runs (§4) the dev database still holds exactly these 6 rows and an empty ledger. Tests use disposable `*_test` databases, and none is left behind (`pg_database`: `llminsight`, `postgres`).

## 4. Task 3 — final regression

Windows 11, Python 3.12.6, PostgreSQL 17.11 container. `LLMI_TEST_REQUIRE_DB=1` makes an unreachable database a failure, not a skip. `OPENAI_API_KEY` is not set in the environment and has no value in `.env`. No code changed in this task, so these runs test exactly commit `7b3fa28`.

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1082 passed, 0 failed, 0 skipped**, 1 warning (R20), 170 s | 0 |
| 2 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk, unit + live backend) | **343 passed, 0 failed, 0 skipped**, 76 s | 0 |
| 3 | `ruff check .` / `ruff format --check .` (backend) | `All checks passed!` / `86 files already formatted` | 0 / 0 |
| 4 | `ruff check .` / `ruff format --check .` (sdk) | `All checks passed!` / `28 files already formatted` | 0 / 0 |
| 5 | `ruff check --config pyproject.toml ../demo_app` / `ruff format --check …` (sdk) | `All checks passed!` / `3 files already formatted` | 0 / 0 |
| 6 | `python -m mypy src` (strict) / `python -m mypy --config-file pyproject.toml ../demo_app` (sdk) | `Success: no issues found in 8 source files` / `… in 3 source files` | 0 / 0 |
| 7 | `python -m pip check` (backend venv / sdk venv) | `No broken requirements found.` (both) | 0 / 0 |
| 8 | `alembic current` / `alembic check` (backend, dev DB) | `0002 (head)` / `No new upgrade operations detected.` | 0 / 0 |
| 9 | Migration cycle, upgrade with data, refused downgrade (disposable DBs) | pass (inside #1, `test_migrations.py`) | — |
| 10 | `docker compose config --quiet`; `docker compose ps` | exit 0; `Up (healthy)` | 0 |
| 11 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 721 PASS, 0 FAIL` before the documentation changes; the run on the final docs is in §9 | 0 |
| 12 | `git diff --check` (root) | no output | 0 |
| 13 | Secret scan (scratch script; names and counts only) | 0 occurrences of `POSTGRES_PASSWORD`, `LLMI_DATABASE_URL` password, `LLMI_KEY_PEPPER`, `LLMI_ADMIN_TOKEN`, `LLMI_API_TOKEN` in tracked files, the full Git history (`log --all --reflog -p`), the index, the diff, and all 10 captured test-output files of this task | 0 |

**Targeted confirmation runs** (subsets of #1 and #2, re-run separately so each requirement has its own evidence):

| Requirement | Tests (directory) | Result | Exit |
|---|---|---|---|
| No credential leakage in test output | `tests/unit/test_output_redaction.py` (backend): 7 leak paths, control run | 10 passed | 0 |
| | `tests/unit/test_redaction.py` (sdk): module copy identical, `LiveBackend` repr | 3 passed | 0 |
| Windows event-loop regression (R32) | `tests/unit/test_eventloop.py`, `tests/integration/test_server_resilience.py` (backend; real `python -m app`, 300 client resets per pattern) | 7 passed | 0 |
| Decimal cost behaviour | `tests/unit/test_pricing.py`, `test_pricing_catalog.py`, `tests/integration/test_ingest_cost.py`, `test_model_prices.py` (backend) | 144 passed | 0 |
| Metadata privacy (R08) | `tests/unit/test_metadata_privacy.py`, `tests/integration/test_ingest_metadata_privacy.py` (backend) | 250 passed | 0 |
| Metrics endpoints | `tests/integration/test_metrics_api.py`, `test_metrics_plan.py` (backend) | 41 passed | 0 |
| FastAPI demo root spans | `tests/unit/test_demo_web.py`, `tests/integration/test_demo_web_live.py` (sdk) | 21 passed | 0 |
| OpenAI adapter offline, synthetic provenance, no real request | `tests/unit/test_openai_adapter.py`, `tests/integration/test_openai_live.py` (sdk) | 69 passed | 0 |

**How "no real OpenAI request" is established:** every OpenAI test uses an in-process `httpx2.MockTransport`, and `tests/openai_fixtures.py::block_network` replaces `socket.getaddrinfo`, `socket.connect` and `connect_ex` for the test so any non-loopback name or connection fails the test. A dedicated test shows the guard stops the official client (`test_openai_adapter.py`). There is no OpenAI key on the machine for this project. Synthetic provenance: the adapter refuses `provenance="real"` on a mock transport, and the demo and adapter tests assert `llminsight.provenance = synthetic` on every span.

## 5. Task 4 — remaining limitations (recorded, not resolved)

| Item | Status |
|---|---|
| Real OpenAI smoke test | **NOT RUN.** No paid API usage was authorized, and there is no `OPENAI_API_KEY` or budget. |
| OpenAI fake transport | The offline tests run the real `openai` 3.28.0 client through the adapter against SYNTHETIC responses. They prove our span mapping, usage extraction, cost and error handling for responses **shaped like** the documented API. They are **not** proof of live provider integration: real response fields, `service_tier` reporting, error bodies, rate-limit headers and latency are unverified against the live service. |
| R32, original traceback attribution | The mechanism was reproduced and fixed (selector loop, regression tests pass). The attribution of the **original** Phase 2B traceback to this mechanism rests on a fragment, because the full text was lost. Residual uncertainty, recorded in `reports/phase-2c.md` §2.3. |
| R34, Windows selector loop | `select()` handles at most 512 sockets on Windows. Accepted for the loopback, single-user MVP only; any deployment needs another server setup and a new review. |
| Pricing (R28, R29, R16) | The catalogue is correct as of 2026-10-25T06:38Z only. Only 3 models and their default snapshots, Standard tier, global endpoint; every other model, tier, regional/FedRAMP endpoint, long-context or cache-write case is `price_missing`. Events before 2026-10-24T21:40Z are `price_missing`. Tier, endpoint, model and usage are client-asserted. Costs are estimates, not invoices; no discounts, credits or taxes. Backfill/repricing is designed, not implemented (`PRICING.md` §6). |
| Privacy (R08, R16, R21–R24) | R08 value checks are heuristics: an unprefixed random token in a model-shaped value, or names and free-form personal data in labels, can pass. Provenance is client-asserted (`provenance_verified: false`). Test-output redaction does not cover `pytest -s` or live logging. One local Claude Code transcript outside the repository holds the **dead** pre-rotation password (R33). R23, R24 open, accepted for local use. Public deployment is not authorized. |
| T48 | Stays **PARTIAL (phase-2c)**: only the aggregate-API half of T48-S1 is verified. The dashboard badges (SYNTHETIC/MIXED/UNKNOWN) and the E2E check belong to Phase 3. |
| Other known limits | A recovered retry marks its trace `error` (R27); R20 Starlette `httpx` deprecation warning; single-process rate limiter (CR-08); `models/compare` is a bounded top-N; no benchmark yet (Phase 5); SDK delivery is best effort (R26). |

**Test matrix, unchanged by this task (no status promoted):** 48 tests; 30 of the 42 critical tests have a PASS status. Critical tests not yet PASS, with their owner phases:

- **Phase 3 (dashboard):** T41 XSS escape on labels, T42 server-only admin token (blocker), T45 trace-list screenshot, T46 drill-down tree E2E (blocker) are NOT RUN. T43 rate limit is PARTIAL (phase-1c), owned Phase 3→5. T48 provenance badges is PARTIAL (phase-2c).
- **Phase 4:** T33, T34 (blocker), T35 (blocker), T36, T37, T39 are NOT RUN.
- **Non-critical, NOT RUN:** T38, T40 (Phase 4), T47 (Phase 5).

## 6. Verified offline capabilities

Verified by the runs in §4 on commit `7b3fa28`, using SYNTHETIC telemetry, the real backend and real PostgreSQL:

- OTLP/HTTP binary-protobuf ingest `POST /v1/traces` with auth, limits, validation, atomic batches, idempotent spans and usage ledger, privacy allowlist and R08 value checks (Phase 1, re-run).
- Python SDK: private TracerProvider, bounded batch export, GenAI CLIENT inference spans, one span per physical attempt, fail-open, deterministic mock provider, sync and async (T13, T14).
- OpenAI Chat Completions adapter, offline: attribute mapping, provider-reported usage, error propagation, network blocked (T09, T11, T12, T15, T16).
- Decimal cost engine: provider-reported usage only, cached and reasoning subsets priced once, exact-model and event-time selection, standard-tier/global-endpoint rule, single half-even rounding, priced once at insert, unknown stays `NULL` (T17–T23).
- Append-only `model_prices` (migration `0002`), the verified catalogue, and the idempotent seed, now applied to the dev database (§3).
- Aggregate metrics M01–M15, `GET /api/v1/overview` and `GET /api/v1/models/compare`: event-time windows, project isolation, provenance buckets, percentiles with `n` (T24, T29, T32, T48-S1 API half).
- FastAPI demo: SERVER request span as the root of CLIENT inference spans, `traceparent` continuation, live round trip (T10).
- Security gate: test-output redaction, rotated database credential, selector event loop (R32 fix), no secret in the repository, history or test output.

## 7. Unverified real-provider behaviour

None of the following has been observed against the live OpenAI API. Each needs an approved, budgeted smoke run before it can be claimed:

- A real Chat Completions response through the adapter, including real `usage`, `id`, `model`, and whether `service_tier` is reported, and therefore whether a real call is priced (`computed`) or `price_missing`.
- Real error responses (401, 429 with headers, 5xx, timeouts) and the SDK's span mapping for them.
- Provenance `real` end to end (a real response ID that keeps a `real` claim from being downgraded).
- That the stored prices equal what OpenAI actually bills for such a call. Costs remain estimates.
- Out of scope rather than unverified: streaming, which the adapter rejects (`stream=True` raises `UnsupportedRequestError`), and the Responses API.

## 8. Phase 3 prerequisites

1. Explicit user instruction to start Phase 3. This closure does not auto-advance.
2. Decide the TypeScript version (R07) and update Node 22 to the latest 22.x patch (R12).
3. Frontend port: `0.0.0.0:3000` is taken by an unrelated container (R19), so choose a free loopback port.
4. Keep the admin token server-only (T42) and the dashboard local-only (ADR-08).
5. Re-verify the price catalogue before any cost-relevant demo (R28).
6. Optional and separate: a real OpenAI smoke run only with an explicit approval, a key and a budget.

## 9. Task 5 — closure

**Documentation changed:** `docs/reports/phase-2-final.md` (new), `docs/PHASE_STATUS.md` (Phase 2 accepted and closed, dev DB seeded, smoke NOT RUN), `docs/PRICING.md` (2026-10-25 re-verification, seeding record), `docs/SOURCES_AND_RISKS.md` (Phase 2 closure sources; R04 mitigated; R28 last-verified date). No code, test, schema, lock file or `.env` change. `docs/TEST_MATRIX.md` is unchanged, because no status changed.

**Gates on the final documentation:** `python -I scripts/verify_phase0.py --quiet` → `SUMMARY: 721 PASS, 0 FAIL` (exit 0); `git diff --check` clean (exit 0); secret scan of the staged diff: 0 occurrences.

**Commit and tag:** one documentation-only commit on `main`, after `git rev-parse --show-toplevel` confirmed the project root. Then an **annotated** tag `phase-2` on that commit, created only because every required check above passed. Not pushed (no remote; no authorization).

## 10. Verdict

**Phase 2 is accepted and closed for the OFFLINE MVP scope.** The baseline was confirmed. The six official prices were re-verified and seeded idempotently into the local development database only, with the ledger untouched. The final regression passed: backend 1082/1082 and SDK 343/343 with real PostgreSQL, 0 failed and 0 skipped, plus lint, types, dependencies, migrations, compose, the doc check and the secret scan, all exit 0. Live OpenAI behaviour is **unverified** (smoke NOT RUN, no paid usage). T48 and the Phase 3/4 critical tests stay open as listed in §5. Phase 3 has not been started.
