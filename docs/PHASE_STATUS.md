# LLM Insight — Phase Status

Last updated: 2026-10-24. Update this file at the end of every phase. A box is checked only when there is evidence (a command output, a file, or user approval).

| Phase | Window (target) | State |
|---|---|---|
| 0 Definition | 24 Oct | **Delivered, awaiting user review** (see `reports/phase-0.md`) |
| 1 Backend / ingest | 25–27 Oct | Not started (blocked on Phase 0 approval) |
| 2 SDK / usage & cost | 28–30 Oct | Not started |
| 3 Dashboard | 31 Oct–2 Nov | Not started |
| 4 Review / dataset / experiments | 3–4 Nov | Not started |
| 5 QA & delivery | 5–7 Nov | Not started |

**Actual code state:** no implementation code exists. The repository contains documentation, `.gitignore` and `.env.example` only.

## Phase 0 — Definition
- [x] Independent repo initialized; `git rev-parse --show-toplevel` = project folder; no remotes
- [x] PRD with P0/P1/P2 and non-goals (`PRD.md`)
- [x] ADR-01…12 confirmed; contract resolutions CR-01…09; proposals PADR-01…09 (`ADR.md`)
- [x] Architecture diagrams, DB and API contracts, version pins (`ARCHITECTURE.md`)
- [x] Telemetry contract verified against official OTLP and GenAI docs (`TELEMETRY_CONTRACT.md`)
- [x] Representative 3-span OTLP fixture with expected results (`fixtures/`)
- [x] Metrics catalog (`METRICS_CATALOG.md`), security (`SECURITY.md`), test matrix with T01–T48 (`TEST_MATRIX.md`)
- [x] Demo script draft (`DEMO_SCRIPT.md`), sources and risks (`SOURCES_AND_RISKS.md`)
- [x] `.env.example` with placeholders only; `CLAUDE.md` concise
- [x] Verification commands run (results in `reports/phase-0.md`)
- [ ] **User review of Phase 0 and decision on PADR-01…09**

## Phase 1 — Backend / data foundation
- [ ] Prerequisites: Phase 0 approved; PADRs decided; `docker info` OK; Python 3.12 venv
- [ ] docker-compose (PostgreSQL 17, bound to 127.0.0.1), FastAPI app, typed config, Alembic baseline
- [ ] `/health/live`, `/health/ready` (not traced)
- [ ] Project + key init command; HMAC key hash; constant-time verify; revocation
- [ ] `/v1/traces`: protobuf decode, gzip, 413/415/400/401/503 semantics, atomic batch, allowlist and attrmap v1
- [ ] Idempotent span/trace upsert; orphan support; indexes
- [ ] `/api/v1/traces` and `/{trace_id}` stubs with project scope and pagination
- [ ] Tests pass with real Postgres: T01–T08, T25–T28, T30, T31, T44 plus the extra sub-cases
- [ ] Standard OTel exporter smoke reaches the DB; same payload twice leaves counts unchanged
- [ ] `reports/phase-1.md`, this file updated, commit + tag

## Phase 2 — SDK, usage and cost
- [ ] `sdk/` installable; Resource, BatchSpanProcessor, OTLP/HTTP protobuf exporter, bounded queue, short timeout, flush/shutdown
- [ ] Demo FastAPI root span → CLIENT inference span; OpenAI wrapper and deterministic mock adapter
- [ ] `usage_records` ledger, canonical key, attempts; `model_prices` with verified sources only
- [ ] Decimal cost, cached/reasoning subsets, unknown = NULL + status
- [ ] Aggregate services (M01–M15)
- [ ] Tests: T09–T24, T29, T32; telemetry-down fail-open demonstrated
- [ ] Real smoke run only with approval, otherwise marked NOT RUN
- [ ] Report, status, commit + tag

## Phase 3 — Dashboard
- [ ] Prerequisites: TypeScript version decided; Node 22 patch updated
- [ ] Overview, models/compare, full traces API with filters, null handling and `n`
- [ ] Next.js server-side fetching only; KPIs, trends, model compare, trace list, span tree, provenance badges
- [ ] Empty, loading and error states; no constant mock KPIs
- [ ] Tests: T24–T32 re-verified, T41–T43, T45, T46, T48; lint, typecheck, build, Playwright; screenshots
- [ ] Report, status, commit + tag

## Phase 4 — Failure Review → dataset → experiments
- [ ] Failure Review with CR-03 enums; trace and span link
- [ ] Human-approved test case with redaction; no approval means no row
- [ ] Deterministic rule engine (JSON Schema, field, exact, regex)
- [ ] 10+ synthetic cases; prompt v1 vs v2 experiment (mock; real only with approval and budget)
- [ ] Tests: T33–T40, T48
- [ ] End-to-end loop reproducible; **feature freeze**
- [ ] Report, status, commit + tag

## Phase 5 — QA and delivery
- [ ] Clean clone → `docker compose up --build` → init → demo (T47)
- [ ] All 48 reported PASS, FAIL or SKIP with reason; 0 FAIL on the 39 critical tests
- [ ] Secret scan, `pip-audit`, `npm audit`, `bandit`; limits and rate limit verified
- [ ] Benchmark with N, hardware and method; no invented targets
- [ ] README, `DEMO_SCRIPT.md` final, `FINAL_DELIVERY_REPORT.md`
- [ ] Report, status, commit + tag

## Test commands

None exist yet. The planned commands are listed in `TEST_MATRIX.md`.
