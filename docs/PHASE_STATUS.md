# LLM Insight — Phase Status

Last updated: 2026-10-26 UTC (Phase 5B **delivered**: final QA, security audit, benchmark and delivery readiness; the complete T01–T48 evidence report: all 48 PASS, **0 FAIL and 0 SKIP on the 42 critical tests**, 17 rows re-statused PASS (phase-5b); security audit with `pip-audit` 2.10.1, `bandit` 1.9.4, `npm audit`, bundle and full-history secret scans: R74 `certifi` fixed (security pin exception), R78 bidi characters in tests fixed, R75–R77 accepted (Low); route-safety re-audit of R68/R69/R71 on the production build (1339 dashboard and 1389 backend probe requests, 0 violations); reproducible local benchmark (ingest ≈ 1.1 k spans/s; API trace open 18–184 ms for 10–2000 spans on a 10k-span dataset; dashboard trace page bottleneck R76); root `README.md`, final `DEMO_SCRIPT.md`; clean-clone acceptance with the full demo and PostgreSQL traceability; backend 1726 / SDK 350 / frontend unit 311 / E2E 55 passed, 0 failed, 0 skipped; evidence `reports/phase-5b.md`; no tag, no push; awaiting architectural review. Phase 5A **delivered**: MVP contract gap closure: T43 request rate limit PASS (phase-5a) on every `/api/v1` route and the live dashboard path; T47 clean-clone compose demo PASS (phase-5a); R69 resolved by ADR-27 (write pages are UI entry points, not an authorization boundary); R71 security correction (the dashboard guard's static-asset exclusions are whole path segments; a same-origin action POST to `/_next/static` had stored an experiment); R72 two timing-dependent tests fixed at the root cause; no Optimization Lab feature added (no locked contract describes one; Phase 4 freeze); R66/R67 retained; backend 1726 / SDK 350 / frontend unit 311 / E2E 55 passed, 0 failed, 0 skipped; evidence `reports/phase-5a.md`; no tag, no push; awaiting architectural review. Phase 4 **final closure**: feature freeze; ADR-22…26 audited against the code (consistent; R68 security correction: the dashboard request guard now also covers `/favicon.ico`, where a Server Action had run unguarded; R69 action ids not page-bound, open for the architectural review); the human-approved case's own id followed into both variants' stored results (new backend integration test, stronger browser E2E against PostgreSQL); R66/R67 documented with their real-mode and distributed-deployment prerequisites; a CRLF-sensitive static test fixed (R70); backend 1720 / SDK 350 / frontend unit 311 / E2E 53 passed, 0 failed, 0 skipped; T33–T40, T48 statuses unchanged (PASS); evidence `reports/phase-4-final.md`; the annotated `phase-4` tag goes on the closure commit only after its own GitHub Actions run passes, and its annotation records that run; Phase 5 not started. Phase 4D **delivered**: experiment pages (list, mock-only setup, results, per-case detail) with ADR-26 (the fifth dashboard write, mode constant `mock`), additive read-only `GET /api/v1/experiments` and `GET /api/v1/experiment-options`, bounded runs (4 concurrent, 15 s, controlled `500`), report money as plain decimal strings (Phase 4C fix), the full live loop trace → review → approved case → golden dataset → mock A/B → results checked against PostgreSQL, the Phase 4C mutation follow-up (trigger → controlled `409`), T48 PASS (phase-4d); backend 1719 / SDK 350 / frontend unit 274→310 / E2E 52 passed, 0 failed, 0 skipped; mutation checks 16/16; evidence `reports/phase-4d.md`; Linux CI not run; awaiting architectural review, Phase 4 not closed. Phase 4C **delivered**: Gate A — versioned rule contract `llminsight.rule.v1`, RE2-only regexes, a bounded no-network JSON Schema 2020-12 subset, exact numbers, review decision freeze and conversion eligibility, partial unique index (migration `0004`), ADR-22 for the dashboard writes; Gate B — migration `0005` (§4.11–4.12), deterministic engine, 12-case SYNTHETIC golden dataset, mock-only A/B experiments `POST`/`GET /api/v1/experiments` with a report that names no better version on incomplete evidence; T36–T40 PASS (phase-4c), T33–T35 re-verified; backend 1705 / SDK 350 / frontend unit 274 / E2E 48 passed, 0 failed, 0 skipped; mutation checks 15/16 (the survivor is caught by the database trigger); evidence `reports/phase-4c.md`; awaiting architectural review; no experiment page yet (Phase 4 closure prerequisite). Phase 4B **delivered**: human Failure Review (CR-03) and human-approved Trace-to-Testset, migration `0003` (§4.8–4.10), review/dataset/test-case API, Trace Detail review section beside a separate unverified diagnostics panel, Datasets pages, narrow Server Actions with CSRF checks; T33, T34, T35 PASS (phase-4b), T36–T40 NOT RUN (Phase 4C); backend 1425 / SDK 350 / frontend unit 268 / E2E 48 passed, 0 failed, 0 skipped; evidence `reports/phase-4b.md`; awaiting architectural review, Phase 4C not started. Phase 4A **delivered**: deterministic, metadata-only trace diagnostics `GET /api/v1/traces/{trace_id}/diagnostics` (`llminsight.diagnostics.v1`), derived on request, no schema change, no UI; T33–T40 unaffected and still NOT RUN; evidence `reports/phase-4a.md`; awaiting architectural review, Phase 4B not started. Phase 3 **final closure**: full local regression passed, truncated project listing stated explicitly, history-wide secret scan 0; first GitHub Actions run on `db7f0bc` failed one SDK test on Linux (a test race, R48), which is fixed and re-verified on Linux and Windows; evidence `reports/phase-3-final.md`. The annotated `phase-3` tag goes on this closure commit only if its own CI run passes, and its annotation records that run. Phase 4 not started. Phase 3C **delivered** and architecturally accepted: model comparison, project selector, R40 and R41 closed, filters/auto-refresh, compose `app` profile and CI workflow; evidence `reports/phase-3c.md`. Phase 3B **delivered**: Trace Explorer, Trace Detail, span tree and waterfall on the real trace API; evidence `reports/phase-3b.md`. Phase 3A delivered, `reports/phase-3a.md`. Phase 2 closed for the offline MVP scope, `reports/phase-2-final.md`). Update this file at the end of every phase. A box is checked only with evidence: a command output, a file, or user approval.

| Phase | Window (target) | State |
|---|---|---|
| 0 Definition | 24 Oct | **Closed.** ADR-13…21 accepted; closure commit `0929a14` approved by the user |
| 1A Backend foundation | 24 Oct | Delivered (`reports/phase-1a.md`); reviewed by the user (Phase 1B instruction) |
| 1B OTLP ingest | 25–27 Oct | Delivered (`reports/phase-1b.md`); accepted conditionally by the user (Phase 1C instruction), the conditions being the 1C tasks |
| 1C Phase 1 integration and acceptance | 25–27 Oct | Delivered (`reports/phase-1c.md`); accepted by the user subject to closing R08 |
| 1 Final security hardening and closure | 24 Oct | **Closed** (`reports/phase-1-final.md`): R08 mitigated, protocol decisions recorded, all Phase 1 gates pass, `phase-1` tag on the closure commit. Awaiting architectural review before Phase 2 |
| 2A SDK + mock integration | 24 Oct | Delivered (`reports/phase-2a.md`); reviewed by the user (Phase 2B instruction) |
| 2B OpenAI adapter, pricing & cost engine | 24 Oct | Delivered (`reports/phase-2b.md`); reviewed by the user (Phase 2C instruction) |
| 2C Metrics, demo integration & security gate | 24–25 Oct | Delivered (`reports/phase-2c.md`); technically accepted by the user (Phase 2 final instruction) |
| 2 Final acceptance and closure | 25 Oct | **Closed** (`reports/phase-2-final.md`) for the offline MVP scope: baseline verified, prices re-verified and seeded (dev DB only), backend 1082 / SDK 343 passed, 0 failed, 0 skipped; real OpenAI smoke NOT RUN; `phase-2` tag on the closure commit |
| 3A Frontend foundation & Overview | 25 Oct | Delivered (`reports/phase-3a.md`); reviewed by the user (Phase 3B instruction) |
| 3B Trace Explorer & span waterfall | 25 Oct | Delivered (`reports/phase-3b.md`); reviewed by the user (Phase 3C instruction) |
| 3C Model comparison, project selector, release readiness | 25 Oct | Delivered (`reports/phase-3c.md`); architecturally accepted by the user (Phase 3 final instruction) |
| 3 Final acceptance, CI and release | 25 Oct | **Closure** (`reports/phase-3-final.md`): backend 1104 / SDK 343 / frontend unit 237 / E2E 44 passed, 0 failed, 0 skipped. CI run 1 (`db7f0bc`) failed one SDK test (Linux-only test race, fixed); annotated `phase-3` tag only on a closure commit whose own CI run passed. Awaiting architectural review before Phase 4 |
| 4A Evidence-based failure diagnostics | 25 Oct | Delivered (`reports/phase-4a.md`): additive read-only diagnostics endpoint and rule engine; backend 1247 / SDK 350 / frontend unit 237 passed, 0 failed, 0 skipped. Awaiting architectural review before Phase 4B |
| 4B Human Failure Review & Trace-to-Testset | 25–26 Oct | Delivered (`reports/phase-4b.md`): migration `0003`, CR-03 reviews, approved test cases with server redaction, dashboard workflow; backend 1425 / SDK 350 / frontend unit 268 / E2E 48 passed, 0 failed, 0 skipped. Reviewed by the architect (Phase 4C instruction): D2, D7, D9 accepted, D6 recorded as ADR-22, D1/D3/D4/D5 revised (ADR-23, ADR-24), D8 deferred |
| 4C Safe deterministic evaluation & synthetic A/B | 26 Oct | Delivered (`reports/phase-4c.md`): Gate A (rule contract v1, RE2, schema safety, review integrity, migration `0004`) passed before Gate B (migration `0005`, engine, golden dataset, mock experiments, report); T36–T40 PASS (phase-4c); backend 1705 / SDK 350 / frontend unit 274 / E2E 48 passed, 0 failed, 0 skipped. Awaiting architectural review |
| 4D Experiment dashboard & integration acceptance | 26 Oct | Delivered (`reports/phase-4d.md`): experiment pages, ADR-26, two additive read endpoints, bounded execution, live end-to-end loop verified against PostgreSQL, T48 PASS (phase-4d); backend 1719 / SDK 350 / frontend unit 310 / E2E 52 passed, 0 failed, 0 skipped. Technically accepted for the offline-only MVP scope (Phase 4 final instruction) |
| 4 Final acceptance, security verification and release | 26 Oct | **Closure** (`reports/phase-4-final.md`): feature freeze, ADR-22…26 audit, R68 fixed, identity evidence, backend 1720 / SDK 350 / frontend unit 311 / E2E 53 passed, 0 failed, 0 skipped; annotated `phase-4` tag only on a closure commit whose own CI run passed. Awaiting architectural review before Phase 5 |
| 5A MVP contract gap closure | 26 Oct | Delivered (`reports/phase-5a.md`): T43 and T47 PASS (phase-5a), ADR-27 (R69), R71 fixed, R72 test races fixed; backend 1726 / SDK 350 / frontend unit 311 / E2E 55 passed, 0 failed, 0 skipped. Awaiting architectural review |
| 5B Final QA, security audit, benchmark, delivery readiness | 26 Oct | Delivered (`reports/phase-5b.md`): 48/48 PASS, 0 FAIL / 0 SKIP on the 42 critical tests; R74 and R78 fixed, R75–R77 accepted; route probes 0 violations; benchmark; README; clean clone; backend 1726 / SDK 350 / frontend unit 311 / E2E 55 passed, 0 failed, 0 skipped. Awaiting architectural review |
| 5 QA & delivery | 5–7 Nov | 5A and 5B delivered; the Phase 5 gate is met locally (§ Phase 5); release steps (review, push, CI, `phase-5` tag) remain |

**Actual code state:** `backend/` holds a FastAPI app with typed config, `/health/live` and `/health/ready`, the SQLAlchemy models and Alembic migration `0001` for `ARCHITECTURE.md` §4.2–4.6, the OTLP/HTTP binary-protobuf receiver `POST /v1/traces` (auth, limits, validation, `llminsight.attrmap.v1`, idempotent trace/span/usage writes), the key CLI `python -m app.cli`, the local admin read API `GET /api/v1/traces` and `GET /api/v1/traces/{trace_id}` (Phase 1C), and unit plus PostgreSQL integration tests. `docker-compose.yml` runs PostgreSQL 17. Phase 2A added `sdk/` (the installable `llm-insight-sdk` 0.1.0: config contract, OTLP/HTTP protobuf export through a bounded BatchSpanProcessor, GenAI CLIENT inference spans, deterministic mock provider, sync and async) and `demo_app/` (CLI demo, scenarios A–E). Phase 2B added migration `0002` (`model_prices`, append-only, plus the `usage_records.model_price_id` FK), the verified OpenAI catalogue `openai-2026-10-24` with `python -m app.cli seed-prices`, the Decimal cost engine applied once at ingest, and the offline-tested OpenAI Chat Completions adapter (`llm-insight-sdk` 0.2.0, extra `openai`). The dev database is at `0002 (head)` with **no** price rows seeded. Phase 2C added the test-output redaction layers in both suites and rotated the local PostgreSQL password (R33), runs the backend on the selector event loop (R32 fix, `app/core/eventloop.py`), re-verified the price catalogue (unchanged), added the aggregate metrics M01–M15 (`app/{repositories,services}/metrics.py`) with `GET /api/v1/overview` and `GET /api/v1/models/compare`, and the FastAPI demo `demo_app/web.py` (SERVER request span → CLIENT inference spans, mock and offline fake OpenAI). At the Phase 2 closure (no code change) the six verified price rows `openai-2026-10-24` were seeded into the dev database, which is at `0002 (head)` with no telemetry. There is no real OpenAI call. Phase 3A added `frontend/` (Next.js 16.4.3 App Router, TypeScript 6.0.3 strict, Tailwind 4, Recharts 3; Node 22.23.4 project-scoped in `.tools/`): app shell and design system, a `server-only` backend access layer (token server-side only, loopback-only, validated parameters, schema-decoded responses, no proxy surface, loopback `Host` guard and nonce CSP in `proxy.ts`) and the Overview page over `GET /api/v1/overview` and `GET /api/v1/models/compare`; plus `scripts/dev/install_node.py` and `scripts/dev/init_frontend_env.py`. No backend change. The dev DB now holds project 1 (`phase3a-demo`) with SYNTHETIC telemetry from the Phase 2 demos. Phase 3B added the Trace Explorer (`/traces`: backend-supported filters only, cursor pagination with a frozen window, URL state) and Trace Detail (`/traces/{trace_id}`: provenance notice, facts, status breakdown, span tree + waterfall with keyboard navigation and bounded rendering, logical calls/retries, attempts, span table) over the existing `GET /api/v1/traces[/{trace_id}]`, lossless JSON and BigInt nanosecond handling, and `scripts/dev/seed_trace_fixtures.py` (SYNTHETIC edge-case traces through the real `/v1/traces`). No backend change. The Phase 3A plaintext ingest key was revoked and its scratch copy deleted; the dev DB now also holds the Phase 3B demo and edge-case traces in project 1 and one trace in project 2 (`phase3b-isolation`); every ingest key is revoked. Phase 3C added (additively, no schema change) `GET /api/v1/projects`, `usage.models` on trace list/detail items (R40) and per-group `tokens`/`usage_source` on `models/compare`; the dashboard gained `/models`, a `?project=` context with a selector on every page, custom UTC ranges and optional bounded auto-refresh; the E2E suite now creates and deletes its own projects (`scripts/dev/e2e_fixtures.py`, R41); `backend/Dockerfile`, `frontend/Dockerfile`, an opt-in compose `app` profile and `.github/workflows/ci.yml` were added. The dev DB content is unchanged by Phase 3C (row counts identical before and after every E2E run). The Phase 3 final closure changed only:
- `frontend/components/project-selector.tsx`, which now states when the project listing is truncated, plus its unit test;
- two SDK unit tests, to remove a Linux-only schedule/flush race (R48).

There is no backend, SDK source, schema, dependency or workflow change, and the dev DB is unchanged.

Phase 4A added `backend/app/services/diagnostics/` (rule catalogue and pure engine) and `backend/app/api/diagnostics.py` (`GET /api/v1/traces/{trace_id}/diagnostics`, registered in `app/main.py`), with unit, PostgreSQL integration and live SDK end-to-end tests. Nothing is persisted: no table, column, index or migration (dev DB still `0002 (head)`, content unchanged). No frontend, SDK source, dependency, lock file or workflow change.

Phase 4B added migration `0003` (`test_datasets`, `failure_reviews`, `test_cases`, column for column with `ARCHITECTURE.md` §4.8–4.10; downgrade refuses while human rows exist), the models, repositories and services for reviews, datasets, test cases, human-text redaction (`services/redaction.py`) and rule validation (`services/case_rules.py`), the endpoints `POST /api/v1/failure-reviews`, `PATCH /api/v1/failure-reviews/{id}`, `GET`/`POST /api/v1/datasets`, `GET`/`POST /api/v1/datasets/{id}/cases` (§5.7), `reviews` on the trace detail and review counts on list items. The dashboard gained the review section and the diagnostics panel on Trace Detail, `/traces/{id}/reviews/{id}/test-case`, `/datasets`, `/datasets/{id}`, and four Server Actions guarded by `proxy.ts` (§5.8). The dev DB was upgraded to `0003 (head)`: every Phase 1–3 table kept identical row counts and content checksums, and the three new tables are empty (E2E runs create and delete their own projects). No dependency, lock file, SDK source or workflow change.

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
- [x] User review of Phase 2B (Phase 2C instruction, 2026-10-24)

## Phase 2C — Metrics, demo integration and security gate (evidence: `reports/phase-2c.md`)
- [x] Prerequisites: clean tree at `720fedc`; PostgreSQL healthy; dev DB `0002 (head)`; backend 1024/1024 and SDK 319/319 before any edit; no ADR conflict (report §1)
- [x] Task A security gate: every leak path identified (pytest funcargs, assertion reprs, `--showlocals`, a dataclass repr, messages, captured output); `RedactedUrl` fixtures and report hooks in both suites; synthetic-credential regression probe (7 paths) with a control run; disk and git-history scan (no copy in the repository)
- [x] Credential rotation: project PostgreSQL role password rotated (SCRAM verifier, `.env` updated atomically, never printed), new login works and the old one is rejected, container recreated on the same volume; Windows PostgreSQL on 5432 untouched; nothing dropped
- [x] R32: reproduced (Proactor loop closes the listening socket after an early client reset), fixed (selector loop), regression tests against the real `python -m app` with mutation check; SDK harness fails on any backend-log traceback
- [x] Task B pricing: six rows re-verified against the official pages (summarizer and raw HTML); unchanged; verification time kept distinct from historical validity; seed dry run; nothing repriced or re-dated
- [x] Task C/D metrics M01–M15 and `GET /api/v1/overview`, `GET /api/v1/models/compare`: event-time windows, project isolation, provenance buckets, Decimal money, unknown as `null`, bounded results, index use checked by EXPLAIN; mutation check 11/11
- [x] Task E FastAPI demo: SERVER root span → CLIENT inference spans (success, multi, failure, recovered retry, offline OpenAI), `traceparent` continuation, synthetic only, no content or keys, fail-open, live round trip to DB, trace API and metrics API
- [x] Task F: backend **1082 passed** (688 unit + 394 integration), SDK **343 passed** (314 unit + 29 live backend), 0 failed, 0 skipped; Ruff; strict mypy; `pip check`; `alembic check` + cycle; compose; doc check; `git diff --check` (report §5)
- [x] Test matrix: T10, T24, T29, T32 `PASS (phase-2c)`; T48 `PARTIAL (phase-2c)` (API half of T48-S1)
- [x] User review of Phase 2C (Phase 2 final instruction, 2026-10-25: technically accepted)

## Phase 2 — final acceptance and closure (evidence: `reports/phase-2-final.md`)
- [x] User acceptance of Phases 2A–2C for the OFFLINE MVP scope (Phase 2 final instruction, 2026-10-25)
- [x] Baseline: project Git root, `main`, clean tree at `7b3fa28`, tags `phase-0`/`phase-1`, no `phase-2`; PostgreSQL healthy; dev DB `0002 (head)`
- [x] Six catalogue prices re-verified against the official pages (summarizer and raw HTML, 2026-10-25T06:38Z): unchanged
- [x] Dev DB seeded with user approval limited to the local development database: target identity confirmed, dry run (would insert 6), seed (inserted 6), second run (inserted 0, unchanged 6); exact Decimal and UTC values, no duplicates, usage ledger untouched, no backfill
- [x] Final regression: backend **1082 passed**, SDK **343 passed**, 0 failed, 0 skipped (real PostgreSQL); Ruff; strict mypy; `pip check`; `alembic check`; compose; doc check; `git diff --check`; secret scan 0
- [ ] Real OpenAI smoke run: **NOT RUN** (no approval, no key, no budget). Phase 2 is accepted on the SYNTHETIC fake-adapter path (master plan §H); live provider behaviour stays unverified (report §7)
- [x] Limitations recorded (report §5): R32 attribution caveat, R34, pricing and privacy limits, T48 dashboard half, open critical tests
- [x] Closure commit and annotated `phase-2` tag (`phase-0`, `phase-1` unchanged; not pushed)

## Phase 3A — Frontend foundation and Overview (evidence: `reports/phase-3a.md`)
- [x] Prerequisites: user instruction (Phase 3A); clean tree at `75ff8c3`, annotated `phase-2`; PostgreSQL healthy; backend ready; no backend contract change needed (report §1)
- [x] Toolchain: Node 22.23.4 project-scoped and checksum-verified (R12; global 22.11.0 untouched); TypeScript 6.0.3 (R07 resolved); Next.js 16.4.3, React 19.3.1, Tailwind 4.3.3, Recharts 3.10.1, ESLint 9.39.5, Playwright 1.64.0 with installed Edge; exact pins, lockfile, `npm audit` 0; two-week-rule exceptions recorded (R37)
- [x] Frontend package, design system (shell, nav, header, metric cards, charts with table views, filters, status, provenance badges, skeleton/empty/error states, accessible tables), palette validated
- [x] Secure integration: server-only token, loopback-only config, fixed paths, validated window/filters, timeout, size cap, no redirects, schema decode, fixed failure kinds; no route handlers or Server Functions; `Host` guard (`421`), GET/HEAD only, nonce CSP; port 3001 (R19)
- [x] Overview M01–M14 + trend, M15 top groups, M11/M13/M14 breakdowns; unknown cost never 0; synthetic labelled; UTC windows aligned to trend buckets; time range and provenance controls re-query the backend
- [x] Live round trip: empty-DB state, synthetic data from `demo_app` and `demo_app.web --drive` (no OpenAI), UI equal to API, outage state, no secret in logs/responses/bundle; screenshots
- [x] Tests: frontend typecheck, lint, 107/107 unit+component, build, bundle scan (0 findings, mutation-checked), 12/12 E2E; backend 1082 passed, SDK 343 passed (no regression)
- [x] Test matrix: T42 `PASS (phase-3a)`; T41, T48 `PARTIAL (phase-3a)`
- [x] User review of Phase 3A (Phase 3B instruction, 2026-10-25)

## Phase 3B — Trace Explorer & span waterfall (evidence: `reports/phase-3b.md`)
- [x] Prerequisites: user instruction (Phase 3B); clean tree at `b79e641`; PostgreSQL healthy; backend ready; build passes before changes; `frontend/AGENTS.md` reviewed (no conflict); trace API schemas inspected; no backend contract change needed (report §1)
- [x] Key hygiene: the Phase 3A scratch-file ingest key (prefix `ZM057mhY`) had exactly one copy and no user; revoked, file deleted, removal verified without printing it. Seeding keys are minted per run, held in memory only, and revoked (report §2)
- [x] Trace list: real API data, ID/service, start (UTC), duration, status, spans, inference calls, tokens, known/unknown cost, prompt version, provenance; backend filters only (window, status, provenance, exact model, page size); cursor pagination with a frozen window; URL state; table on desktop, cards on mobile; empty/loading/error states. No provider/model column: the list API has none (R40)
- [x] Trace detail: provenance notice, facts (ns timestamps, duration, cost, tokens), status breakdown (trace error vs span failures vs recovery, no cause inferred), span tree + waterfall (several roots, orphans, truncated parents, cycles, equal starts, 200-row batches, ARIA tree keyboard navigation, selected-span panel with usage/cost/attributes), logical calls and retries, attempts table, span table alternative; back link restores the list
- [x] Security: path allowlist, validated ids, canonical back links, lossless JSON, escaped rendering; no route handler or Server Function; CSP/Host/method guards unchanged
- [x] Live integration with SYNTHETIC data only (mock demos + edge-case seeder; no OpenAI): UI equal to API; screenshots
- [x] Tests: frontend typecheck, lint, 194/194 unit+component, build, bundle scan, 30/30 E2E (12 Phase 3A + 18 Phase 3B), mutation checks (12 unit + 1 live); backend and SDK regressions (counts in the report)
- [x] Test matrix: T41, T45, T46, T48 `PASS (phase-3b)`
- [x] User review of Phase 3B (Phase 3C instruction, 2026-10-25)

## Phase 3C — Model comparison, project selector and release readiness (evidence: `reports/phase-3c.md`)
- [x] Prerequisites: own Git root, `main` at `1554a93`, clean tree, tags `phase-0`/`phase-1`/`phase-2`; `origin` = `MuratElberKayaa/LLM-Insight` and `origin/main` = local `main`, so the upstream was set (no history change, no push); PostgreSQL healthy, backend ready, DB `0002 (head)`
- [x] Model comparison page `/models` on `GET /api/v1/models/compare` (+ additive per-group tokens): backend groups in backend order, exact Decimal, unknown ≠ $0, incomplete cost, tokens with unknown counts, latency with `n` and low-sample flags, provenance per group, SYNTHETIC banner, "not ranked" notice, two charts with table views, cards on mobile, UTC filters, loading/empty/error states
- [x] `GET /api/v1/projects` (admin auth first, ≤ 200, id/name/created_at only, read-only, no schema change) and the project selector: one explicit project per request, invalid ids refused without fallback, unknown ids → 404 state, echoed-project check, full-page switch without cursor/page/span, navigation keeps the project
- [x] R40 closed: additive `usage.models` (every provider/model combination with call counts, ≤ 10 + `distinct`), index-backed, isolation-tested; list column; no new filter
- [x] Filters: custom UTC ranges (µs-exact 31-day check) on Overview/Models/Traces, shareable canonical URLs, invalid values refused with reasons; optional auto-refresh (off by default, ≥ 30 s, no overlap, hidden-tab pause, 30-min stop, off on cursor pages)
- [x] R41 closed: run-owned SYNTHETIC E2E projects created and deleted by the Playwright global setup/teardown; dev-DB counts identical before/after
- [x] Security regression incl. hostile project names, model/span names and URL parameters; T41, T42, T45, T46, T48 re-verified live
- [x] Compose `app` profile (backend + dashboard sharing one network namespace, dashboard on `127.0.0.1:3001`, PostgreSQL unchanged) built and smoke-tested; CI workflow added (not run on GitHub: not pushed)
- [x] Full regression (counts in the report), mutation checks 16/16 after closing two gaps, credential and bundle scans 0, no OpenAI call
- [x] Architectural review before the Phase 3 final closure: Phase 3C architecturally accepted (Phase 3 final instruction, 2026-10-25)

## Phase 3 — final acceptance, CI verification and release (evidence: `reports/phase-3-final.md`)
- [x] Prerequisites: TypeScript version decided (R07); Node 22 patch updated for the frontend (R12); free loopback frontend port 3001 (R19) — done in Phase 3A
- [x] Repository: own Git root, `main` at `c76943f`, clean; `origin/main` (`1554a93`) an ancestor (fast-forward only); `phase-0`/`phase-1`/`phase-2` identical locally and remotely; no `phase-3`; action pins equal their upstream tags
- [x] Overview, models/compare, traces with backend filters (including provenance), null handling and `n`; server-side fetching only; provenance badges; empty, loading and error states; no constant mock KPIs (3A–3C, re-verified by the final E2E run)
- [x] Project listing beyond 200: the selector now says the list is incomplete (disabled marker option + notice); unit test with mutation check
- [x] Security review: token server-side only, no proxy surface, loopback-only, `Host`/CSP guards, escaped rendering, project isolation, exact Decimal/ns; secret scan over all 508 history blobs, messages and tracked files: 0; no OpenAI call
- [x] Tests: frontend typecheck, lint, **237/237** unit, build, bundle scan 0, **44/44** E2E, `npm audit` 0; backend **1104 passed**, SDK **343 passed**, 0 failed, 0 skipped; Ruff; strict mypy; `pip check`; `alembic check`; compose; doc check; `git diff --check`
- [x] Report and status (closure commit A `db7f0bc`)
- [x] Fast-forward push `1554a93..db7f0bc`; CI run [`40210403217`](https://github.com/MuratElberKayaa/LLM-Insight/actions/runs/40210403217): `docs` and `frontend` passed; `python` passed every backend step and failed one SDK test (R48)
- [x] Root cause reproduced in a disposable Linux container and fixed in the test only (assertions unchanged); SDK 343 passed on Linux and Windows (`reports/phase-3-final.md` §6.1)
- [x] CI run on closure commit B passes every job; then the annotated `phase-3` tag on B, recording that run, pushed alone (`phase-0`…`phase-2` unchanged). Verified at the Phase 4A baseline: run `40211917319` on `e5a631c` `completed`/`success`; remote `phase-3` = tag object `55d2f4e` → `e5a631c` (`reports/phase-4a.md` §1)
- [ ] Real OpenAI smoke run: **NOT RUN** (no approval, no key, no budget); the dashboard is verified on SYNTHETIC data only
- [x] Architectural review of the Phase 3 closure before Phase 4 (the user issued the Phase 4A instruction, 2026-10-25)

## Phase 4A — Evidence-based failure diagnostics (evidence: `reports/phase-4a.md`)
- [x] Baseline: own Git root, `main` = `e5a631c` = `phase-3` (annotated, also remote), clean; origin as expected; latest CI run success; PostgreSQL healthy; backend 1104, SDK 343, frontend unit 237 passed before any change
- [x] Contract review: T33–T40 identified exactly; Phase 4A implements none of them; no conflict with ADR-01…21/CR-01…09 (no schema change, no `failure_reviews` write, no LLM judge, hypotheses unverified); scope note recorded for the review (report §2)
- [x] Contract `llminsight.diagnostics.v1` / ruleset `rules.v1`: facts, evidence, unverified hypothesis, limitations, `data_complete`, investigation steps, `not_assessed`; constant catalogue texts, no scores (`ARCHITECTURE.md` §5.6)
- [x] 18 rules over stored metadata only, each input verified in the schema; exact-match `error.type` interpretation; no latency, semantic, prompt-quality or outage diagnosis
- [x] API: auth before validation, project-scoped, identical `404`, `422` without echo, 4 statements per request (no N+1), bounded output, deterministic order, byte-identical repeated reads, `no-store`
- [x] Tests: unit 108, integration 35, live SDK end to end 7 (every evidence field compared with storage); mutation check 10/10; backend 1247 passed, SDK 350 passed, frontend unit 237 passed, 0 failed, 0 skipped; Ruff; ad-hoc strict mypy on the new modules; `pip check`; `alembic check`; compose; doc check; `git diff --check`; secret scan
- [x] Test matrix unchanged: T33–T40 NOT RUN; no Phase 4B/4C item marked complete
- [ ] Frontend integration of the diagnostics: not in scope; requirements recorded (report §9, R53)
- [ ] Architectural review of Phase 4A before Phase 4B

## Phase 4B — Human Failure Review & Trace-to-Testset (evidence: `reports/phase-4b.md`)
- [x] Baseline: own Git root, `main` = `888b188`, clean; tags unchanged; PostgreSQL healthy, dev DB `0002`; backend 1247, SDK 350, frontend unit 237 passed before any change
- [x] Contract review and acceptance mapping before coding; gaps resolved by the narrowest reading, recorded as D1–D9 (no new column, enum or relationship); stop condition not met (report §2)
- [x] Migration `0003` for §4.8–4.10 only; upgrade with data, refused downgrade with human rows, up/down/up; dev DB upgraded with unchanged Phase 1–3 content
- [x] CR-03 reviews: six `issue_type` values, open/pending by default, PATCH for status/decision with the closed ⇒ decided check, duplicate open review `409`, server redaction of evidence and hypothesis
- [x] Trace-to-Testset: explicit `approved: true`, redacted input, decided review of the same trace and project, one case per (dataset, review), provenance references, nothing copied from telemetry
- [x] Diagnostics shown as a separate, unverified panel; static and integration tests prove no diagnostics-to-review path
- [x] Dashboard: review/decision/dataset/test-case forms via four Server Actions, CSRF checks in `proxy.ts`, text-only rendering, no double submission, keyboard-reachable labelled controls
- [x] Tests: backend 1425, SDK 350, frontend unit 268, E2E 48 passed, 0 failed, 0 skipped; mutation checks 9/9 backend, 4/4 frontend; Ruff, SDK mypy, pip check, `alembic check`, compose, doc check, secret scan, `git diff --check`
- [x] Matrix: T33, T34, T35 PASS (phase-4b); T36–T40 NOT RUN (Phase 4C)
- [ ] Architectural review of Phase 4B (decisions D1–D9) before Phase 4C

## Phase 4C — Safe deterministic evaluation and synthetic A/B experiments (evidence: `reports/phase-4c.md`)
- [x] Baseline: own Git root, `main` = `7fd1528`, clean; dev DB `0003` with 0 test cases, 0 reviews, 0 duplicate pairs
- [x] Gate A: rule contract `llminsight.rule.v1` (ADR-23), RE2 pinned and bounded (measured worst case ~0.5 s), JSON Schema 2020-12 subset with no retrieval and no regex keywords, exact numbers end to end, review decision freeze and eligibility (API + triggers), partial unique index, ADR-22 (D6); migration `0004`; Gate A checks passed before Gate B (report §6)
- [x] Gate B: migration `0005` (§4.11–4.12 + integrity triggers), deterministic engine, 12-case SYNTHETIC golden dataset, mock-only A/B experiments with budget checks, report with honest comparisons (ADR-25)
- [x] Tests: backend 1705, SDK 350, frontend unit 274, E2E 48 passed, 0 failed, 0 skipped; mutation checks 15/16; Ruff, SDK mypy, pip check, `alembic check`, compose, doc check, secret scan, `git diff --check`
- [x] Matrix: T36–T40 PASS (phase-4c); T33–T35 re-verified (PASS phase-4b); T48 Phase 4 re-check open (no experiment page)
- [ ] Architectural review of Phase 4C (ADR-22…25)

## Phase 4D — Experiment dashboard & integration acceptance (evidence: `reports/phase-4d.md`)
- [x] Baseline: own Git root, `main` = `5949ed7`, clean; tags `phase-0`…`phase-3` unchanged; dev DB `0005`; real LLM disabled
- [x] Contract inspection; gaps G1–G7 documented before implementing; additive read-only `GET /api/v1/experiments` and `GET /api/v1/experiment-options`; no migration, no column
- [x] Experiment pages (list, setup, results, case detail), side navigation, dataset link; ADR-26 (one new Server Action, mode constant `mock`, explicit confirmed start, double-submit guard)
- [x] Bounded execution: 4 concurrent runs (`429 experiments_busy`), 15 s deadline (`409 experiment_time_limit`), controlled `500`; nothing stored on any refusal
- [x] Report money as plain decimal strings (Phase 4C `str(Decimal)` notation bug fixed)
- [x] Phase 4C mutation follow-up: bypassed eligibility/freeze checks end in the trigger's controlled `409` (regression tests)
- [x] Live E2E: trace → diagnostics → review → decision → approved case → golden dataset → mock A/B → results → per-case comparison, counts checked against PostgreSQL directly
- [x] Tests: backend 1719, SDK 350, frontend unit 310, E2E 52 passed, 0 failed, 0 skipped; mutation checks 16/16; typecheck, lint, build, bundle scan, npm audit, Ruff, SDK mypy, pip check, `alembic check`, compose, doc check, secret scan, `git diff --check`
- [x] Matrix: T48 PASS (phase-4d); T33–T42 re-verified (statuses unchanged)
- [ ] Linux CI run (needs a push; not done)
- [ ] Architectural review of Phase 4D (ADR-26)

## Phase 4 — Failure Review → dataset → experiments
- [x] Failure Review with CR-03 enums and partial unique index; trace and span link; redaction of evidence and hypothesis (Phase 4B)
- [x] Human-approved test case with redaction and `origin_review_id` lineage; no approval means no row (Phase 4B)
- [x] Deterministic rule engine (JSON Schema, field, exact, regex) (Phase 4C)
- [x] 10+ synthetic cases; prompt v1 vs v2 experiment, mock only; real refused (Phase 4C)
- [x] Tests: T33–T40, T48 (T33–T40 PASS; T48 re-checked for Phase 4, PASS (phase-4d))
- [x] Experiment page in the dashboard (Phase 4D)
- [x] End-to-end loop reproducible in the dashboard (Phase 4D live E2E; the human-approved case's own id verified in PostgreSQL at the Phase 4 final closure)
- [x] **Feature freeze** (Phase 4 final: only the R68 security correction, the R70 test fix, focused regressions and documentation)
- [x] Report, status, commit (`reports/phase-4-final.md`); the tag follows the closure commit's own CI run (Phase 4 final section below)

## Phase 4 — final acceptance, security verification and release (evidence: `reports/phase-4-final.md`)
- [x] Baseline: own Git root, `main` = `62b6b0b`, clean; tags `phase-0`…`phase-3` identical locally and remotely, no `phase-4`; `origin/main` = `e5a631c`, 4 ahead / 0 behind; PostgreSQL healthy, dev DB `0005 (head)`, `alembic check` clean; real LLM disabled, no key
- [x] ADR-22…26 audited (full text) against the code and tests: consistent, no conflict with ADR-01…21, none weakened; R68 found by a live replay and fixed (`frontend/proxy.ts` matcher); R69 recorded for the architectural review (ADR text unchanged)
- [x] Trace-to-testset identity: the exact human-approved case id followed into both variants' stored results (backend integration test with a killed mutation; browser E2E against PostgreSQL); all test data isolated and removed (dev DB checksums identical)
- [x] Execution and integrity audit (mock only, real refused, 4 runs, 15 s, rollback, controlled errors, budget pre-flight, no provider network, exact Decimal, unknown ≠ 0, identical ids, append-only, no winner on incomplete evidence, 4C trigger regressions); real-mode prerequisite noted (pre-call budget guard)
- [x] R66 and R67 documented with their prerequisites; R54, R55, R56, R08 and the local boundary preserved; public deployment prohibited
- [x] Local regression: backend **1720**, SDK **350**, frontend unit **311**, E2E **53** passed, 0 failed, 0 skipped; typecheck, lint, build, bundle scan 0, `npm audit` 0/0, Ruff, strict SDK mypy, `pip check`, `alembic check` + migration cycle, both compose configurations and the `app` image build, credential scan of the tree, the history and the messages 0
- [x] Final checks after the documentation: `verify_phase0.py` 737 PASS, 0 FAIL; `git diff --check` clean (line-ending notices only, `core.autocrlf=true`)
- [x] Fast-forward push of `main` and the GitHub Actions run of the closure commit (recorded in the `phase-4` tag annotation: run 40372061697, success; reconciled in Phase 5A: `origin/main` = `394920c` = local `main`, `git ls-remote`)
- [x] Annotated `phase-4` tag on that commit, pushed alone (Phase 5A baseline: annotated tag object `4d24a91` → `394920c`, identical on the remote)
- [x] Architectural review of the Phase 4 closure before Phase 5 (the user's Phase 5A instruction starts Phase 5 from `phase-4`)

## Phase 5A — MVP contract gap closure (evidence: `reports/phase-5a.md`)
- [x] Baseline: own Git root, `main` clean at `394920c` = `origin/main`; annotated `phase-4` → HEAD; `phase-0`…`phase-3` unchanged locally and remotely; PostgreSQL healthy, `0005 (head)`, `alembic check` clean; dashboard on 127.0.0.1:3001
- [x] Requirements map: Phase 5 is final QA under the Phase 4 freeze; T43 (PARTIAL) and T47 (NOT RUN) were the open locked items; "Optimization Lab" appears in no locked contract, so no new feature was built (decision recorded for the review)
- [x] R69: ADR-27 (supersedes one ADR-26 sentence; ADR-22 preserved; proxy checks unchanged); R68 `/favicon.ico` regression re-run passes
- [x] R71 found by a raw-path probe and fixed (`frontend/proxy.ts` matcher, static test, live replay E2E extended; failed before the fix with one stored experiment)
- [x] T43 PASS (phase-5a): `test_phase5a_rate_limit.py` (6) and `phase5a.spec.ts` (2), mutation-checked
- [x] T47 PASS (phase-5a): clean clone, compose `app` profile, init, demo in the browser; T47 stack removed afterwards (containers, volume, images); dev stack untouched
- [x] Optimization Lab audit: identical case sets, variant identity, rule versions, exact Decimal, unknown ≠ 0, recorded latency only, synthetic provenance, no winner on incomplete evidence: all held by the existing engine and tests; no change
- [x] R66 retained (accepted local limitation), R67 unchanged; real mode disabled; public deployment prohibited
- [x] R72: trace-tree E2E and SDK live-diagnostics test races fixed at the root cause (no product change)
- [x] Regression: backend 1726, SDK 350, frontend unit 311, E2E 55 passed, 0 failed, 0 skipped; typecheck, lint, build, bundle scan 0, `npm audit` 0, Ruff, strict SDK mypy, `pip check`, `alembic check`, both compose configs, credential scan 0 (report §7)
- [x] Final checks after the documentation: `verify_phase0.py` 739 PASS, 0 FAIL; `git diff --check` clean (line-ending notices only, `core.autocrlf=true`); credential scan of all reachable blobs, messages and the files to commit 0 hits (control detected)
- [ ] Architectural review of Phase 5A

## Phase 5B — Final QA, security audit, benchmark and delivery readiness (evidence: `reports/phase-5b.md`)
- [x] Baseline: own Git root, `main` clean at `fe259e7` (Phase 5A, local only); tags `phase-0`…`phase-4` unchanged, no `phase-5`; `origin` correct; PostgreSQL healthy, `0005 (head)`, `alembic check` clean; baseline suites backend 1726 / SDK 350 / frontend unit 311 passed before any edit
- [x] T01–T48 evidence matrix (definition, class, implementation, tests, run, result, limitation): 48 PASS; **42 critical: 0 FAIL, 0 SKIP**; 10 blockers PASS; no definition changed; T33–T40, T43, T47, T48 re-checked (suites, E2E against PostgreSQL, clean clone)
- [x] Security audit: `pip-audit` (both locks × PyPI/OSV; venvs), `bandit` (production code and tests), `npm audit` (full and production), bundle scan, full-history secret scan with a control run; R74 fixed (`certifi` 2026.10.19, security pin exception, hash-verified), R78 fixed (escapes in seven test files), R75 (toolchain `pip` 24.2, interpreter/OS not audited, unhashed locks), R76, R77 accepted; every other finding classified as false positive or Info
- [x] Route safety: R68/R71 look-alikes, `/favicon.ico`, `/_next/static*`, `/_next/image*`, dot segments, encodings, Host/Origin variants on the production build (1339 requests) and backend alternate paths (1389 requests): 0 violations; R69/ADR-27 consistent; nothing stored
- [x] Benchmark (`scripts/qa/benchmark.py`, test-owned DB, 3 runs, warm-up discarded): ingest 100/500/1000 spans fresh and re-ingest (counts unchanged), trace open on exactly 10,000 spans (API and dashboard); hardware, versions, method, p50/p95, query limits, bottlenecks and limitations recorded; no target set
- [x] Root `README.md` (what it does, architecture, requirements, host quick start, compose `app` profile without host ingest, configuration, security boundaries, tests, limitations); final `DEMO_SCRIPT.md`
- [x] Clean clone (from the local repository; Phase 5A is not pushed) + Phase 5B changes: init, compose `--build`, migration, key, prices, host ingest, golden dataset, browser demo (review → refused without approval → approved case → mock A/B → results), host dashboard; the human-approved case traced to its review and trace and evaluated under both variants in PostgreSQL; only test-owned resources removed
- [x] Regression: backend **1726**, SDK **350**, frontend unit **311**, E2E **55** passed, 0 failed, 0 skipped; typecheck, lint, build, bundle scan 0, Ruff, strict SDK mypy, `pip check`, `alembic check`, both compose configs, image build (clean clone), `pip-audit` locks clean, `npm audit` 0
- [x] Final checks after the documentation: secret scan of 769 reachable blobs, 26 commit/tag messages and the 452 files to commit: 0 secret-value hits, 35 pattern hits = the triaged test/doc fixtures (F9), none in a Phase 5B file; no format/bidi character in any text file; `verify_phase0.py` 740 PASS, 0 FAIL; `git diff --check` clean (line-ending notices only, `core.autocrlf=true`)
- [ ] Architectural review of Phase 5B

## Phase 5 — QA and delivery
- [x] Clean clone → `docker compose up --build` → init → demo (T47 PASS (phase-5a), re-run PASS (phase-5b): `reports/phase-5b.md` §7)
- [x] All 48 reported PASS, FAIL or SKIP with reason; **0 FAIL on the 42 critical tests** (`reports/phase-5b.md` §3: 48 PASS)
- [x] Request rate limit verified (T43 PASS (phase-5a), re-run PASS (phase-5b))
- [x] Secret scan, `pip-audit`, `npm audit`, `bandit` for the final delivery (`reports/phase-5b.md` §4)
- [x] Benchmark with N, hardware and method; no invented targets (`reports/phase-5b.md` §6)
- [x] README, `DEMO_SCRIPT.md` final (`FINAL_DELIVERY_REPORT.md` left to the final release step, `reports/phase-5b.md` §11)
- [x] Report, status, commit (Phase 5B, local)
- [ ] Architectural review, push, Linux CI on the Phase 5 commits, annotated `phase-5` tag (not done: no push or tag in Phase 5B)

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
| `python -m app` (backend) | yes | Dev server on `127.0.0.1:8000`, single worker, selector event loop (R32); serves `/health/*`, `POST /v1/traces`, `GET /api/v1/traces[/{trace_id}]`, `GET /api/v1/traces/{trace_id}/diagnostics` (Phase 4A), `POST /api/v1/failure-reviews`, `PATCH /api/v1/failure-reviews/{id}`, `GET`/`POST /api/v1/datasets`, `GET`/`POST /api/v1/datasets/{id}/cases` (Phase 4B), `POST /api/v1/experiments`, `GET /api/v1/experiments/{id}` (Phase 4C, mock only), `GET /api/v1/experiments` and `GET /api/v1/experiment-options` (Phase 4D), `GET /api/v1/overview`, `GET /api/v1/models/compare` and `GET /api/v1/projects` (`?project=<id>`, header `Authorization: Bearer $LLMI_ADMIN_TOKEN`) |
| `python -m app.cli create-key --project <name>` / `list-keys` / `revoke-key --prefix <p>` (backend) | yes | Create a project and ingest key (the key is printed once; only its HMAC is stored), list prefixes, revoke |
| `python -m app.cli seed-prices [--dry-run]` (backend) | yes | Insert the verified price catalogue (`PRICING.md` §2); never updates a stored price, exits 1 on a conflicting row, never reprices usage |
| `python -m pip install -r requirements-dev.lock && python -m pip install --no-deps .` (sdk, in `sdk/.venv`) | yes | Install the locked SDK environment (includes `openai` 3.28.0 for the offline adapter tests) and the SDK (non-editable; reinstall after source changes) |
| `python -m pytest tests/unit` / `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration` (sdk) | yes | SDK unit tests (local OTLP stub) / live-backend tests (starts `backend/.venv` `python -m app` on a disposable `*_test` DB) |
| `ruff check .`, `ruff format --check .`, `python -m mypy src` (sdk) | yes | SDK lint / format / strict types (`--config pyproject.toml ../demo_app` for the demo) |
| `sdk/.venv/Scripts/python -m demo_app` (root) | yes | Phase 2A demo A–E (mock only); needs `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>`, `OTEL_SERVICE_NAME` |
| `sdk/.venv/Scripts/python -m demo_app.web [--drive]` (root) | yes | Phase 2C FastAPI demo on `127.0.0.1:8100` (`LLMI_DEMO_PORT`): request spans as parents of mock and offline fake-OpenAI inference spans; `--drive` sends one request per route and prints trace IDs. Same environment as `python -m demo_app` |
| `python -I scripts/dev/install_node.py [--print-bin]` (root) | yes | Install Node 22.23.4 into `.tools/` (SHA-256 checked) / print its bin directory to prepend to PATH for frontend commands; never touches the global Node |
| `python -I scripts/dev/init_frontend_env.py --project-id <id> [--force]` (root) | yes | Create `frontend/.env.local` (git-ignored) with the backend URL, server-side token (not printed) and project id |
| `npm ci` (frontend) | yes | Install the locked frontend dependencies |
| `npm run dev` / `npm run build` / `npm start` (frontend) | yes | Dashboard on `127.0.0.1:3001` (dev / production); Node 22.23.4 enforced, Next.js telemetry off |
| `npm run typecheck` / `npm run lint` / `npm test` (frontend) | yes | Strict tsc / ESLint (0 warnings) / unit + component tests (tsc → node:test) |
| `npm run check:bundle` (frontend, after build) | yes | T42: token value and secret variable names absent from `.next/` |
| `npm run test:e2e` (frontend) | yes | Playwright (installed Edge) against the real backend on :8000; starts dashboards on :3001, an outage instance on :3002 and a wrong-token instance on :3003, plus (Phase 5A, T43) a second backend with a tiny `/api/v1` bucket on :8001 and a dashboard on :3004 reading through it (ports must be free); `LLMI_SCREENSHOT_DIR=<dir>` saves screenshots. Phase 3C: the global setup creates and seeds this run's own projects and the teardown deletes them (`LLMI_E2E_KEEP=1` keeps them); no other data is needed |
| `LLMI_SEED_PROJECT_KEY=<key> sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py [--set edge\|isolation] [--seed S]` (root) | yes | Phase 3B: SYNTHETIC edge-case traces (nesting, equal starts, orphan, cycle, mixed/unknown provenance, application error, hostile labels, 771-span trace) through the real `POST /v1/traces`; key from the environment only, loopback endpoint only; prints trace ids |
| `backend/.venv/Scripts/python scripts/dev/e2e_fixtures.py setup\|teardown\|experiment-counts --state <file> [--experiment <id>]` (root) | yes | Phase 4D: `experiment-counts` reads one run-owned experiment's stored results from PostgreSQL (read-only; counts, case ids, provenance). Phase 3C (R41): create run-owned SYNTHETIC E2E projects (`e2e-<run>-main/-foreign/-xss …`), seed them through `/v1/traces`, revoke the keys / delete exactly those projects. Called by the Playwright global setup and teardown |
| `backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project <id> --approved-by <name> [--run-experiment --model <m>]` (root) | yes | Phase 4C: load the SYNTHETIC golden dataset (12 cases) through the real API and optionally run one mock A/B experiment; loopback only, token from `backend/.env`, never printed; prints ids and counts |
| `sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py --set phase3c` (root) | yes | Phase 3C: a retried call and a multi-provider/multi-model trace (SYNTHETIC) |
| `docker compose --profile app up -d --build` / `docker compose --profile app config --quiet` (root) | yes | Phase 3C opt-in profile: backend + dashboard containers (one network namespace), dashboard on `127.0.0.1:3001`; the default `docker compose up` still starts PostgreSQL only. Stop with `docker compose --profile app stop backend frontend` (never `down` on the dev stack unless intended) |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` (root) | yes | Phase 5B: credential scan of every reachable blob, every commit/tag message and the working files (exact local secret values, never printed, plus patterns); exit 1 on any hit |
| `python -I scripts/qa/route_probe.py <port> <action-id> <out.json>` (root, against `next start`) | yes | Phase 5B: raw-socket Server Action dispatch probe (78 targets × 17 Host/Origin variants); exit 1 on a dispatch outside the write pages' same-origin checks |
| `python -I scripts/qa/backend_probe.py <port> <.env> <out.json>` (root) | yes | Phase 5B: backend alternate-path probe (every route, many spellings, with and without the token); run it against a throwaway backend, because unauthenticated requests drain the auth-failure bucket |
| `backend/.venv/Scripts/python -I scripts/qa/benchmark.py --repo . --db <name>_test --out <dir>` (root) | yes | Phase 5B: local benchmark in a test-owned database (dropped and recreated), with its own backend on :8012 and dashboard on :3006 |
| `.github/workflows/ci.yml` | yes | Phase 3C CI: docs check; backend/SDK lint, types, migrations, tests (real PostgreSQL via compose); frontend audit, typecheck, lint, unit tests, build, bundle scan. Runs on GitHub on every push to `main` and on pull requests (first runs: `reports/phase-3-final.md` §6); Playwright E2E and the compose image build stay local gates |
