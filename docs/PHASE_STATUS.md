# LLM Insight — Phase Status

Last updated: 2026-10-24 UTC (Phase 2B delivered: OpenAI adapter, verified pricing catalogue and Decimal cost engine; awaiting review. Phase 2 is not complete; no `phase-2` tag). Update this file at the end of every phase. A box is checked only with evidence: a command output, a file, or user approval.

| Phase | Window (target) | State |
|---|---|---|
| 0 Definition | 24 Oct | **Closed.** ADR-13…21 accepted; closure commit `0929a14` approved by the user |
| 1A Backend foundation | 24 Oct | Delivered (`reports/phase-1a.md`); reviewed by the user (Phase 1B instruction) |
| 1B OTLP ingest | 25–27 Oct | Delivered (`reports/phase-1b.md`); accepted conditionally by the user (Phase 1C instruction), the conditions being the 1C tasks |
| 1C Phase 1 integration and acceptance | 25–27 Oct | Delivered (`reports/phase-1c.md`); accepted by the user subject to closing R08 |
| 1 Final security hardening and closure | 24 Oct | **Closed** (`reports/phase-1-final.md`): R08 mitigated, protocol decisions recorded, all Phase 1 gates pass, `phase-1` tag on the closure commit. Awaiting architectural review before Phase 2 |
| 2A SDK + mock integration | 24 Oct | Delivered (`reports/phase-2a.md`); reviewed by the user (Phase 2B instruction) |
| 2B OpenAI adapter, pricing & cost engine | 24 Oct | Delivered (`reports/phase-2b.md`); awaiting user review |
| 2 (rest) aggregates, FastAPI demo root, real smoke | 28–30 Oct | Not started (Phase 2C) |
| 3 Dashboard | 31 Oct–2 Nov | Not started |
| 4 Review / dataset / experiments | 3–4 Nov | Not started |
| 5 QA & delivery | 5–7 Nov | Not started |

**Actual code state:** `backend/` holds a FastAPI app with typed config, `/health/live` and `/health/ready`, the SQLAlchemy models and Alembic migration `0001` for `ARCHITECTURE.md` §4.2–4.6, the OTLP/HTTP binary-protobuf receiver `POST /v1/traces` (auth, limits, validation, `llminsight.attrmap.v1`, idempotent trace/span/usage writes), the key CLI `python -m app.cli`, the local admin read API `GET /api/v1/traces` and `GET /api/v1/traces/{trace_id}` (Phase 1C), and unit plus PostgreSQL integration tests. `docker-compose.yml` runs PostgreSQL 17. Phase 2A added `sdk/` (the installable `llm-insight-sdk` 0.1.0: config contract, OTLP/HTTP protobuf export through a bounded BatchSpanProcessor, GenAI CLIENT inference spans, deterministic mock provider, sync and async) and `demo_app/` (CLI demo, scenarios A–E). Phase 2B added migration `0002` (`model_prices`, append-only, plus the `usage_records.model_price_id` FK), the verified OpenAI catalogue `openai-2026-10-24` with `python -m app.cli seed-prices`, the Decimal cost engine applied once at ingest, and the offline-tested OpenAI Chat Completions adapter (`llm-insight-sdk` 0.2.0, extra `openai`). The dev database is at `0002 (head)` with **no** price rows seeded. There is **no** aggregate service, no other `/api/v1` endpoint, no FastAPI demo root span, no real OpenAI call and no frontend yet.

## Phase 0 — Definition
- [x] Independent repo initialized; `git rev-parse --show-toplevel` = project folder; no remotes
- [x] PRD with P0/P1/P2 and non-goals (`PRD.md`)
- [x] ADR-01…12 confirmed; CR-01…09 accepted; ADR-13…21 accepted by the user on 2026-10-24, with no open proposals (`ADR.md`)
- [x] Architecture diagrams, column-level DB contract, API contract, version pins (`ARCHITECTURE.md`)
- [x] Telemetry contract verified against OTLP 1.11.1 and GenAI semconv, including the support statement, response encoding, `BadRequest` details, usage identity and provenance (`TELEMETRY_CONTRACT.md`)
- [x] Representative 3-span OTLP fixture with expected results (`fixtures/`)
- [x] Metrics catalog with provenance aggregation (`METRICS_CATALOG.md`), security (`SECURITY.md`), test matrix with 48 tests / 42 critical / 10 blockers plus sub-cases (`TEST_MATRIX.md`)
- [x] Demo script draft (`DEMO_SCRIPT.md`), sources and risks (`SOURCES_AND_RISKS.md`)
- [x] `.env.example` with placeholders only; `CLAUDE.md` concise
- [x] Reproducible documentation check committed and passing (`scripts/verify_phase0.py`; results in `reports/phase-0.md` §11)
- [x] User decision on the Phase 0 proposals: ADR-13…21, formerly PADR-01…09 (final ADR approval, 2026-10-24)
- [x] User review of the Phase 0 closure commit (approved; Phase 1A instruction, 2026-10-24)

## Phase 1A — Backend foundation (evidence: `reports/phase-1a.md`)
- [x] Prerequisites: Phase 0 closure approved; Docker Engine 28.5.1 running (R18 resolved); Python 3.12.6 venv `backend/.venv`; user instruction to install the pinned backend deps
- [x] Pinned runtime/dev deps re-checked on PyPI (all first uploaded ≤ 2026-10-10), `backend/requirements.lock`
- [x] docker-compose: PostgreSQL 17 on `127.0.0.1:55432` only, `pg_isready` health check (`healthy`)
- [x] FastAPI app factory, typed config (pydantic-settings; placeholder/short secrets and non-loopback bind rejected; secrets hidden from errors)
- [x] Alembic migration `0001` matching `ARCHITECTURE.md` §4.2–4.6 column for column; `model_price_id` FK deferred to the Phase 2 migration (`ARCHITECTURE.md` §4.13 note)
- [x] Migration upgrade → downgrade → upgrade on disposable `*_test` databases (CLI and pytest); `alembic check` shows no drift
- [x] `/health/live` (no DB) and `/health/ready` (`SELECT 1` within 1 s, else `503`), verified with the DB up, stopped and restarted. Neither is traced (no tracing exists yet)
- [x] Unit + real-PostgreSQL integration tests and Ruff pass (counts in the report)
- [x] Local `.env` created by `scripts/dev/init_env.py` (never overwrites, never prints values; git-ignored, untracked)
- [x] User review of Phase 1A (Phase 1B instruction, 2026-10-24)

## Phase 1B — OTLP ingest (evidence: `reports/phase-1b.md`)
- [x] Prerequisites: Phase 1A reviewed; user instruction to install the pinned OTel/protobuf dependencies; PostgreSQL healthy; baseline 118/118
- [x] Pinned OTel/protobuf runtime deps and the dev-only SDK/exporter installed and locked (all ≤ 2026-10-10; no existing pin changed; `pip check` clean)
- [x] Project + key CLI (`python -m app.cli create-key / list-keys / revoke-key`); HMAC-SHA256 key hash with the pepper; constant-time verify; revocation
- [x] `/v1/traces`: §1.1 processing order, §1.2 response encoding, `BadRequest` (≤ 20, no values), limits (4 / 16 MiB, 2048 spans, `413`), gzip with a bounded inflater, atomic batch, `200` only after COMMIT, allowlist and always-deny list, attrmap v1 aliases
- [x] Auth-failure and per-key token buckets (`429` + `Retry-After`), single process only (CR-08)
- [x] Idempotent span insert, derived trace recompute, orphan support, usage rows for inference spans only with response-ID-first call keys, collision counter, provenance downgrade; no cost computed (every row `unknown` with a reason)
- [x] Tests pass with real Postgres: T01–T08, T30, T31, T44 plus sub-cases T01-S1…S3, T03-S1/S2, T06-S1…S6, T08-S1…S3, T30-S1 (counts in the report). Partial evidence only: T14, T16, T18, T23 (S1/S2), T26, T27, T28, T43, T48 (S1/S2)
- [x] The standard OTel exporter (1.45.1) reaches the DB; the same spans exported twice leave counts unchanged; the real `python -m app` process smoke matches the contract
- [x] User review of Phase 1B: accepted conditionally (Phase 1C instruction, 2026-10-24). The §7 clarifications were decided in that instruction and applied in 1C

## Phase 1C — Phase 1 integration and acceptance (evidence: `reports/phase-1c.md`)
- [x] Prerequisites: clean tree at `9a09e44`; PostgreSQL healthy; dev DB at `0001 (head)`; baseline 374/374; `reports/phase-1b.md` §7 decisions checked against ADR-01…21 (no conflict needing a stop; report §2)
- [x] Task A: normalization hardening. `gen_ai.request.max_tokens` kept; explicit credential classification replaces substring matching; bounded `gen_ai.prompt.version`; identifier-only `error.type`; all-unknown trace → `unknown`; IDs never truncated; saturating drop count; provider namespace escaped in call keys; `real` only ever downgraded
- [x] Task B (R21): `Status.message` no longer stored (schema unchanged, column always `NULL`); adversarial DB/log/API test
- [x] Task C (R22): overall body-read deadline (`LLMI_OTLP_BODY_READ_TIMEOUT_S`, `408` + `Connection: close`), disconnect and cancellation handling, auth still before any body read; raw-socket tests against uvicorn
- [x] Task D: `GET /api/v1/traces` and `/{trace_id}`: admin bearer token, auth before validation, shared auth-failure bucket plus `/api/v1` bucket, project scope with one indistinguishable `404`, keyset pagination, validated filters and 31-day window, ns-exact ISO `Z` timestamps, unknown cost as `null`, `provenance_verified: false`, read-only snapshot transactions, index use verified by `EXPLAIN ANALYZE`
- [x] Task E: idempotent re-exports, separate retry attempts, provider-namespaced keys, differing payloads under one identity counted (first write kept)
- [x] Task F: full suite **626 passed, 0 failed, 0 skipped** with a real database required; official exporter round trip; Ruff; `pip check`; compose; migration cycle + `alembic check`; live `python -m app` smoke; doc check (report §3)
- [x] Test matrix: T01–T08, T25–T28, T30, T31, T44 `PASS (phase-1c)`; T14, T16, T18, T23, T43, T48 still `PARTIAL` (owned by Phases 2–4)
- [x] User review of Phase 1C: accepted subject to closing R08 (Phase 1 final instruction, 2026-10-24)

## Phase 1 — Final security hardening and closure (evidence: `reports/phase-1-final.md`)
- [x] R08: every persisted untrusted string passes `TELEMETRY_CONTRACT.md` §3.1 (per-field format and length, unsafe characters, obvious credentials, personal-data shapes; checked before truncation; dropped or `redacted_span`, counted). Stated as a heuristic, not a guarantee
- [x] Adversarial regression: synthetic secrets in span name, request/response model, `error.type`, resource attributes, scope name/version, `prompt_version` (and 11 more fields), plain and gzip, absent from the DB dump, API responses and DEBUG logs; valid GenAI metadata and token counters preserved; mutation check (detectors disabled → tests fail)
- [x] Protocol decisions recorded: `408` incomplete body (not retried by OTLP exporters, no partial write), `413` size/span limits, `503` temporary unavailability, no `403`, cross-project `404` identical to missing
- [x] Risks: R08 mitigated (residual Low); R23, R24 open and accepted for local use; public deployment not authorized; client provenance is not proof; unknown cost stays `NULL`
- [x] Verification: full suite **876 passed, 0 failed, 0 skipped** (real PostgreSQL); Ruff; `pip check`; `alembic check` + migration cycle; compose; doc check; `git diff --check`
- [x] Closure commit and `phase-1` tag (`phase-0` unchanged)
- [x] Architectural review before Phase 2: the user issued the Phase 2A instruction (2026-10-24); no separate review findings were recorded

## Phase 2A — Python telemetry SDK and deterministic mock integration (evidence: `reports/phase-2a.md`)
- [x] Prerequisites: clean tree at `cec668b`, `phase-1` tag; PostgreSQL healthy; baseline 876/876; ADR/contract check found no conflict and no migration need (report §2)
- [x] Semconv pin re-checked (ADR-15): `9ffc7e00c639` is still `main` HEAD; `client-inference.md` unchanged since before the pin; no `attrmap.v2`
- [x] `sdk/` installable (src layout, pinned OTel 1.45.1, typed, own venv and lock); clean-room wheel install and import outside the source tree; never imports the backend
- [x] Explicit resource, private TracerProvider (no global override; opt-in `set_global` never replaces), idempotent `init`, BatchSpanProcessor with bounded queue/batch, request size under the 4/16 MiB limits, batch ≤ 2048, explicit exporter timeout in seconds, bounded `force_flush`/`shutdown`, `stats()`, fail-open, best-effort delivery documented
- [x] Deterministic mock provider (success, delayed, 429, 500, timeout, retry), SYNTHETIC only, UUIDv4 `call_id`, one span per physical attempt, sync and async, no content, no exception events, client-side R08 rules
- [x] Demo app A–E against the real `python -m app` and PostgreSQL; verified by SQL and the read API
- [x] Tests: SDK 247 passed (237 unit + 10 live backend), 0 skipped; backend 876 passed; Ruff; strict mypy; `pip check`; `alembic check`; compose; doc check; `git diff --check`; mutation check 9/9
- [x] Test matrix: T13, T14 `PASS (phase-2a)`; T09–T12, T15, T16, T18, T23 `PARTIAL (phase-2a)`
- [x] User review of Phase 2A (Phase 2B instruction, 2026-10-24)

## Phase 2B — OpenAI adapter, model pricing and cost engine (evidence: `reports/phase-2b.md`)
- [x] Prerequisites: clean tree at `169e6d0`; PostgreSQL healthy; backend 876/876 and SDK 247/247 before any edit; no ADR conflict and no contract gap requiring a stop (report §2)
- [x] Migration `0002`: `model_prices` column for column (§4.7), FK `usage_records.model_price_id → model_prices(id) ON DELETE RESTRICT`, non-negative NUMERIC prices, no overlap (`UNIQUE(provider, model, effective_from)`), append-only triggers, guarded downgrade; upgrade with existing data, downgrade, cycle and `alembic check` tested
- [x] Verified catalogue `openai-2026-10-24`: `gpt-5.4-mini`, `gpt-5-mini`, `gpt-4.1-mini` and their default snapshots (6 rows) from the official pricing and model pages; flat-priced models only; synthetic fixtures for math tests only (`PRICING.md`)
- [x] Decimal cost engine: provider-reported usage only, subsets priced once, exact-model and event-time selection, OpenAI standard-tier/global-endpoint rule, single half-even rounding to 10 places, priced once at insert, never repriced; backfill workflow documented, not implemented
- [x] OpenAI Chat Completions adapter (sync + async), offline tests with fake transports and a network block; one span per physical request; fail-open; errors propagate
- [x] Tests: backend 1024 passed, SDK 319 passed (298 unit + 21 live backend), 0 skipped; Ruff; strict mypy; `pip check`; `alembic check`; compose; doc check; `git diff --check`; mutation check 15/15. The first complete SDK run had one intermittent failure: a test-ordering bug (fixed) and an unconfirmed backend-log traceback (R32), report §4.3
- [x] Test matrix: T09, T11, T12, T15–T23, T31 `PASS (phase-2b)`; T10 `PARTIAL (phase-2b)`
- [ ] User review of Phase 2B

## Phase 2 — remaining (Phase 2C, not started)
- [ ] Demo FastAPI root span → CLIENT inference span (completes T10)
- [ ] Aggregate services M01–M15 with provenance aggregation rules
- [ ] Tests: T24, T29, T32
- [ ] Real smoke run only with approval and a budget, otherwise marked NOT RUN
- [ ] Report, status, commit + `phase-2` tag

## Phase 3 — Dashboard
- [ ] Prerequisites: TypeScript version decided; Node 22 patch updated
- [ ] Overview, models/compare, full traces API with filters (including provenance), null handling and `n`
- [ ] Next.js server-side fetching only; KPIs, trends, model compare, trace list, span tree, provenance badges (real / synthetic / mixed / unknown)
- [ ] Empty, loading and error states; no constant mock KPIs
- [ ] Tests: T24–T32 re-verified, T41–T43, T45, T46, T48 (+S1/S2); lint, typecheck, build, Playwright; screenshots
- [ ] Report, status, commit + tag

## Phase 4 — Failure Review → dataset → experiments
- [ ] Failure Review with CR-03 enums and partial unique index; trace and span link; redaction of evidence and hypothesis
- [ ] Human-approved test case with redaction and `origin_review_id` lineage; no approval means no row
- [ ] Deterministic rule engine (JSON Schema, field, exact, regex)
- [ ] 10+ synthetic cases; prompt v1 vs v2 experiment (mock; real only with approval and budget)
- [ ] Tests: T33–T40, T48
- [ ] End-to-end loop reproducible; **feature freeze**
- [ ] Report, status, commit + tag

## Phase 5 — QA and delivery
- [ ] Clean clone → `docker compose up --build` → init → demo (T47)
- [ ] All 48 reported PASS, FAIL or SKIP with reason; **0 FAIL on the 42 critical tests**
- [ ] Secret scan, `pip-audit`, `npm audit`, `bandit`; limits and rate limit verified
- [ ] Benchmark with N, hardware and method; no invented targets
- [ ] README, `DEMO_SCRIPT.md` final, `FINAL_DELIVERY_REPORT.md`
- [ ] Report, status, commit + tag

## Commands

| Command (run from) | Exists | Purpose |
|---|---|---|
| `python -I scripts/verify_phase0.py --quiet` (root) | yes | Documentation consistency (Phase 0 contracts) |
| `python -I scripts/dev/init_env.py` (root) | yes | Create the local `.env` with generated secrets; never overwrites |
| `docker compose up -d --wait postgres` / `docker compose config --quiet` (root) | yes | Local PostgreSQL 17 on `127.0.0.1:55432` / validate compose without printing secrets |
| `python -m pip install -r requirements.lock && python -m pip install --no-deps -e .` (backend, in `.venv`) | yes | Install the locked backend environment |
| `alembic upgrade head` / `alembic downgrade base` / `alembic check` (backend) | yes | Migrations (URL from `.env`) |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest` (backend) | yes | Unit + integration tests; integration uses disposable `*_test` DBs. Without the variable, integration tests skip with a reason if PostgreSQL is unreachable |
| `ruff check .` / `ruff format --check .` (backend) | yes | Lint / format |
| `python -m app` (backend) | yes | Dev server on `127.0.0.1:8000`, single worker; serves `/health/*`, `POST /v1/traces` and `GET /api/v1/traces[/{trace_id}]?project=<id>` (header `Authorization: Bearer $LLMI_ADMIN_TOKEN`) |
| `python -m app.cli create-key --project <name>` / `list-keys` / `revoke-key --prefix <p>` (backend) | yes | Create a project and ingest key (the key is printed once; only its HMAC is stored), list prefixes, revoke |
| `python -m app.cli seed-prices [--dry-run]` (backend) | yes | Insert the verified price catalogue (`PRICING.md` §2); never updates a stored price, exits 1 on a conflicting row, never reprices usage |
| `python -m pip install -r requirements-dev.lock && python -m pip install --no-deps .` (sdk, in `sdk/.venv`) | yes | Install the locked SDK environment (includes `openai` 3.28.0 for the offline adapter tests) and the SDK (non-editable; reinstall after source changes) |
| `python -m pytest tests/unit` / `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration` (sdk) | yes | SDK unit tests (local OTLP stub) / live-backend tests (starts `backend/.venv` `python -m app` on a disposable `*_test` DB) |
| `ruff check .`, `ruff format --check .`, `python -m mypy src` (sdk) | yes | SDK lint / format / strict types (`--config pyproject.toml ../demo_app` for the demo) |
| `sdk/.venv/Scripts/python -m demo_app` (root) | yes | Phase 2A demo A–E (mock only); needs `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>`, `OTEL_SERVICE_NAME` |
| `npm run lint/typecheck/build`, `npx playwright test` | no | Planned (Phase 3) |
