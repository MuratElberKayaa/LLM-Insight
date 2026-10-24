# LLM Insight — Phase Status

Last updated: 2026-10-24 (Phase 1B delivered, awaiting review). Update this file at the end of every phase. A box is checked only with evidence: a command output, a file, or user approval.

| Phase | Window (target) | State |
|---|---|---|
| 0 Definition | 24 Oct | **Closed.** ADR-13…21 accepted; closure commit `0929a14` approved by the user |
| 1A Backend foundation | 24 Oct | Delivered (`reports/phase-1a.md`); reviewed by the user (Phase 1B instruction) |
| 1B OTLP ingest | 25–27 Oct | **Delivered; awaiting user review** (`reports/phase-1b.md`) |
| 1C Phase 1 integration and acceptance | 25–27 Oct | Not started. Waits for the Phase 1B review |
| 2 SDK / usage & cost | 28–30 Oct | Not started |
| 3 Dashboard | 31 Oct–2 Nov | Not started |
| 4 Review / dataset / experiments | 3–4 Nov | Not started |
| 5 QA & delivery | 5–7 Nov | Not started |

**Actual code state:** `backend/` holds a FastAPI app with typed config, `/health/live` and `/health/ready`, the SQLAlchemy models and Alembic migration `0001` for `ARCHITECTURE.md` §4.2–4.6, the OTLP/HTTP binary-protobuf receiver `POST /v1/traces` (auth, limits, validation, `llminsight.attrmap.v1`, idempotent trace/span/usage writes), the key CLI `python -m app.cli`, and unit plus PostgreSQL integration tests. `docker-compose.yml` runs PostgreSQL 17. There is **no** `/api/v1`, no SDK, no pricing and no frontend yet.

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
- [ ] **User review of Phase 1B**, including the contract clarifications listed in `reports/phase-1b.md` §7

## Phase 1C — Phase 1 integration and acceptance (not started)
- [ ] `/api/v1/traces` and `/{trace_id}` stubs with the admin bearer token, project scope and cursor pagination (T25, and the API halves of T26, T27, T28)
- [ ] Decisions on the Phase 1B contract clarifications applied (contract text or code)
- [ ] Hardening candidates from the Phase 1B report (e.g. body-read timeout, R22)
- [ ] `reports/phase-1.md` (Phase 1 gate: T01–T08, T25–T28, T30, T31, T44 and their sub-cases), this file updated, commit + `phase-1` tag

## Phase 2 — SDK, usage and cost
- [ ] Semconv pin re-checked (ADR-15)
- [ ] `sdk/` installable; Resource, BatchSpanProcessor, OTLP/HTTP protobuf exporter, bounded queue, short timeout, flush/shutdown
- [ ] Demo FastAPI root span → CLIENT inference span; OpenAI wrapper and deterministic mock adapter
- [ ] `usage_records` and `model_prices` per `ARCHITECTURE.md` §4.6–4.7; response-ID-first keys; verified prices only
- [ ] Decimal cost, cached/reasoning subsets, unknown = NULL + reason
- [ ] Aggregate services M01–M15 with provenance aggregation rules
- [ ] Tests: T09–T24, T29, T32, T23-S1/S2; telemetry-down fail-open demonstrated
- [ ] Real smoke run only with approval, otherwise marked NOT RUN
- [ ] Report, status, commit + tag

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
| `python -m app` (backend) | yes | Dev server on `127.0.0.1:8000`, single worker; serves `/health/*` and `POST /v1/traces` |
| `python -m app.cli create-key --project <name>` / `list-keys` / `revoke-key --prefix <p>` (backend) | yes | Create a project and ingest key (the key is printed once; only its HMAC is stored), list prefixes, revoke |
| `npm run lint/typecheck/build`, `npx playwright test` | no | Planned (Phase 3) |
