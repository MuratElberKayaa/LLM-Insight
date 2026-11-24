# LLM Insight — Phase Status

**Current state (2026-11-19 UTC).** The local-only MVP of Phases 0–6 is released. Phases 7–8 added multi-provider live comparison, and Phase 9 runs the short-term roadmap. Phases 9A–9F are on `main` (`ad1ec8e`, PR #17; CI 7/7 green). The medium-term batch, Phases 10A–10F (agent/tool/MCP steps, streaming TTFT, pseudonymous sessions, error patterns, a CI eval gate, alert rules; ADR-54…59), is committed on `feature/phase-10-medium-term` from `ad1ec8e`; the owner pushes.

**Provider execution switches (SDK, as on this branch):** Groq `groq_adapter.REAL_EXECUTION_ENABLED = True` (Phase 8G-B release branch); Gemini `gemini_live.REAL_EXECUTION_ENABLED = False` (re-locked 2026-11-17); OpenRouter `False`. OpenAI live execution is refused by its 14-day price-evidence rule (R83; `reports/phase-9c.md`). Each real request still needs its own written authorization.

**Open items with dates**

| Date (UTC) | Item | Where |
|---|---|---|
| 2026-11-26 06:00 | Groq Free Plan evidence window ends; a new account check is needed before any further Groq request | Phase 8G-A, `reports/phase-8g.md` |
| from 2026-11-30 17:07 | Upgrade `typescript-eslint` / `@typescript-eslint/*` 8.71.1 → 8.73.x (two-week rule met), then delete R102 | Phase 9E, `reports/phase-9e.md` |
| 2026-12-07 | R102 review deadline; the audit gate fails from 2026-12-08T00:00Z | `frontend/security/npm-audit-policy.json` |
| 2026-12-11 | `gpt-5-mini` shutdown; its catalogue rows stay valid for historical events | `PRICING.md` §2 |
| 2026-12-19 12:51 | The list-price reference catalogue becomes `stale` (still computed, flagged) | `PRICING.md` §9 |

**Latest verified results (2026-11-19, Phase 9; CI confirmed 2026-11-20):** backend 2008 passed (PostgreSQL required); SDK 2724 passed; frontend unit 430 passed; audit-policy tests 116 passed; typecheck, lint, build (Node 22.23.4), bundle scan with 0 findings, Ruff; production npm audit 0; full npm audit CONDITIONAL PASS (R102 only). These are local runs, not CI. **Phase 10 batch (2026-11-23, local):** backend 2111 passed; frontend unit 503 and audit-policy 116 passed; build and bundle scan clean; SDK in the batch commit message. CI after the owner's push.

**How this file is organised.** One section per phase, in order, each with its checklist and evidence report. The free-form running log that used to stand here (Phase 8 live validation, UI-1…UI-5, 8H-CI and the code-state summary, 2026-10 to 2026-11-18) was moved verbatim to `reports/phase-status-log-2026-11.md`. New status goes into the phase's own section and this header, not into a new log paragraph.

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
- [x] Architectural review of Phase 5A: covered by the Phase 5 final instruction, which accepts the Phase 5B state (built on 5A) for the local, offline-only MVP scope (2026-10-27)

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
- [x] Architectural review of Phase 5B: accepted for the LOCAL, OFFLINE-ONLY MVP scope; `certifi` 2026.10.19 security exception approved; remaining Low risks accepted for local use only (Phase 5 final instruction, 2026-10-27)

## Phase 5 — QA and delivery
- [x] Clean clone → `docker compose up --build` → init → demo (T47 PASS (phase-5a), re-run PASS (phase-5b): `reports/phase-5b.md` §7)
- [x] All 48 reported PASS, FAIL or SKIP with reason; **0 FAIL on the 42 critical tests** (`reports/phase-5b.md` §3: 48 PASS)
- [x] Request rate limit verified (T43 PASS (phase-5a), re-run PASS (phase-5b))
- [x] Secret scan, `pip-audit`, `npm audit`, `bandit` for the final delivery (`reports/phase-5b.md` §4)
- [x] Benchmark with N, hardware and method; no invented targets (`reports/phase-5b.md` §6)
- [x] README, `DEMO_SCRIPT.md` final; `FINAL_DELIVERY_REPORT.md` and `PROJECT_PITCH.md` (Phase 5 final)
- [x] Report, status, commit (Phase 5B, local)
- [x] Architectural review (Phase 5 final instruction, 2026-10-27)
- [x] Push, Linux CI on the closure commit, annotated `phase-5` tag: done after this commit (Phase 5 final section below); the verified run is recorded in the tag annotation (reconciled in R104-0: see the Phase 5 final section)

## Phase 5 — final MVP acceptance, delivery documentation and release (evidence: `FINAL_DELIVERY_REPORT.md`)
- [x] Baseline: own Git root, `main` clean at `e7f8dcf`; tags `phase-0`…`phase-4` identical locally and on `origin` (`phase-4`: `4d24a91` → `394920c`), no `phase-5`; `origin` = `MuratElberKayaa/LLM-Insight` (public), `origin/main` = `394920c`, local 2 ahead / 0 behind; PostgreSQL healthy, `0005 (head)`, `alembic check` clean; real LLM disabled
- [x] Acceptance evidence re-checked, not re-invented: 48 PASS, 42 critical PASS (no critical failure or skip), 10 blockers PASS; all 78 tests named in the Phase 5B matrix passed in the final JUnit results; approval boundary, trace-to-testset lineage, synthetic A/B results, privacy and isolation, route-security, clean-clone and benchmark evidence confirmed (`FINAL_DELIVERY_REPORT.md` §10, §12, §14)
- [x] Security sign-off: `certifi` 2026.10.19 is the official, non-yanked PyPI release (hash unchanged; OSV fixed version), exact in both locks, installed in both venvs and in an image built from `git archive` of the baseline (identical CA bundle); no older `certifi` in the dependency graph; pip's vendored copy and the OS trust store recorded under R75; R75–R77 accepted for local use only; `pip-audit` locks clean, `npm audit` 0, `bandit` classified, route probe 1339 requests 0 violations, history secret scan 0 secret-value hits (control detected), server logs clean
- [x] Final local regression: backend **1726**, SDK **350**, frontend unit **311**, E2E **55** passed, 0 failed, 0 skipped; typecheck, lint, build, bundle scan 0, Ruff, strict SDK mypy, `pip check`, `alembic check`, both compose configs, image build (`FINAL_DELIVERY_REPORT.md` §20)
- [x] `FINAL_DELIVERY_REPORT.md`, `PROJECT_PITCH.md`; README (delivery links, PostgreSQL requirement, `--build` note, real screenshots, CI scope) and `DEMO_SCRIPT.md` polished; `SOURCES_AND_RISKS.md` (R74–R77 notes, sources); no test definition changed, no product code changed
- [x] Final checks after the documentation: `verify_phase0.py` 780 PASS, 0 FAIL; `git diff --check` clean (line-ending notices only, `core.autocrlf=true`); no format/bidi character in any changed document; secret scan of all reachable blobs, messages and the files to commit: 0 secret-value hits
- [x] Fast-forward push of `main`; the closure commit's own GitHub Actions run passes; annotated `phase-5` tag on that commit recording the run, pushed alone (verified after this commit; never recorded here before it exists). Reconciled in R104-0 (2026-11-03) from the tag annotation: closure commit `1a57d65` (historical SHA), `push` run 40403041389 success (docs, python, frontend); after the history rewrite `phase-5` (tag object `0d6f349`) → `26f733a`, identical tree `aa0248f`

## Phase 6C-C — Live observability dashboard (evidence: `reports/phase-6c-c.md`)
- [x] Baseline: own Git root, `feature/phase-6c-gemini-free` at `580d457`; only the user's `.env.example` modified (kept, not committed); PostgreSQL started on its volume; unrelated `:8000` container and the user's `:8010` backend untouched
- [x] Gap matrix before coding (report §2); first real trace read back through the API and the dashboard, GET only, nothing modified (`reports/phase-6c-b.md`)
- [x] Controlled refresh (ADR-38): off by default, ≥ 15 s, one in flight, hidden-tab pause, unmount cleanup, Strict-Mode safe, 429 cooldown (seconds / HTTP-date) for timer and button, backoff, stop on fatal failures, stale data of the same view only, last successful load shown
- [x] Real (asserted) / Synthetic / Mixed / Unknown from stored values only; exact provider/model filters applied by the backend (ADR-37), echo-checked; project switch resets them
- [x] Latest stored trace (event time vs read time); unknown cost never 0; mixed-provenance notices
- [x] Tests: backend 1765, SDK 817, frontend unit 349, E2E 62 passed, 0 failed, 0 skipped; mutation checks 16/16; typecheck, lint, build, bundle scan 0, Ruff, strict mypy, pip check, `alembic check`, compose, `git diff --check`
- [x] R101 `pyasn1` resolved (0.6.5, approved security exception; `reports/phase-6c-c-security-closure.md` §2)
- [x] R102 `minimatch` reviewed: one advisory, dev-only, no compatible fix; accepted conditionally for local-only development (§3)
- [x] Architectural review: ADR-33…38 accepted at the design level (2026-11-01)
- [x] Architectural review of the security closure and Phase 6C-C closure (Phase 6 release decision, 2026-11-01)

## Phase 6 — Final QA, security review and CI release readiness (evidence: `reports/phase-6-final-qa.md`)
- [x] Baseline re-inspected: `feature/phase-6c-gemini-free` at `5f77c94`, 6 commits ahead of `main` (`1a57d65`), not pushed, no `phase-6` tag; only the user's `.env.example` modified (kept, not staged)
- [x] ADR-28…38 checked against code and tests; Phase 5 behaviour preserved (no migration, ingest or model change since `phase-5`; no test deleted; changed assertions follow ADR-37/38)
- [x] Live Gemini trace verified read-only through the current code (`:8030`, GET only) and the dashboard; the user's `:8010` backend found running pre-6C-C code (ignores `provider`), left untouched
- [x] Full regression, working tree and clean clone: backend 1765, SDK 817, frontend unit 349, E2E 62 (disposable DB), all passed with 0 failed and 0 skipped; typecheck, lint, build, bundle scan 0, Ruff, strict mypy, `pip check`, `alembic check`, compose, `git diff --check`; doc verifier passes on the committed tree
- [x] Python lockfile audits clean (PyPI, OSV); installed `pip` 24.2 only (R75, 12 advisories, register corrected)
- [x] R102 re-verified: dev-only, no compatible fix (ESLint 10 also affected); CI consequence documented; CI policy proposal written, **not applied**
- [x] Bandit reconciled: 0/0 was a changed-files scope, 1 High / 5 Medium is the full tree; fingerprints match `phase-5` (5) and Phase 6A (1); no exploitable finding
- [x] Secret scan: 0 secret-value hits; route probe: 0 violations; dev database untouched (read-only checks only)
- [x] CI `frontend` job green: approved R102 policy applied; run 41350036376 on `1930f8d`, frontend success with R102 as CONDITIONAL PASS (`reports/phase-6-final.md` §5)
- [x] Remote Linux CI run on the release commit (first run on `1930f8d` passed; the closure commit's runs are tracked in the Phase 6 final section). Reconciled in R104-0: runs 41352727469 and 41353876030 on the historical release commit `7e40604`, and 41663913648 on `237e4e1` (same tree), all success
- [x] Architectural review of the Phase 6 final QA and release decision (Phase 6 accepted for a local-only release, 2026-11-01)

## Phase 6 — CI security policy, R102 exception (evidence: `reports/phase-6-ci-security-policy.md`)
- [x] Architectural decision applied: Phase 6 final QA accepted for local scope; R102 approved as a temporary dev-only exception for GHSA-g3hv-75wg-m249 in the verified ESLint chain, review deadline 2026-11-30 UTC
- [x] Baseline re-verified on the current tree: `npm audit` 16 moderate = one advisory, `--omit=dev` 0, `npm ls minimatch` 3.1.5 and 10.2.6, all 18 vulnerable nodes `dev: true`; still no compatible upstream fix (`eslint` 10.14.0 and every plugin on `minimatch` 3.x/10.x)
- [x] Exception metadata recorded (`frontend/security/npm-audit-policy.json`); evaluator `frontend/scripts/audit-policy.mjs` (stdlib, fail-closed: other advisories, unresolvable findings, non-dev nodes, changed severity/range/CWE/source/title/copies/versions/chain, expired or invalid policy, malformed or incomplete report, npm/registry errors)
- [x] CI `frontend` job: strict production audit (`--audit-level=info`; npm's default `low` would let `info` through), policy self-tests, full audit printed and evaluated; typecheck, lint, tests, build and bundle scan unchanged after them; other jobs unchanged
- [x] Policy tests 59 passed, 0 failed, 0 skipped (16 required cases, input mutation sweep); code mutation check 25/25 caught; current real report: CONDITIONAL PASS (not a clean audit)
- [x] Full regression (synthetic data, disposable databases): backend 1765, SDK 817, frontend unit 349, E2E 62, all passed with 0 failed and 0 skipped; typecheck, lint, build, bundle scan 0, Ruff, strict mypy, `pip check`, `alembic check`, compose, lockfile `pip-audit` clean, Bandit unchanged (1 High / 5 Medium classified), secret scan 0 value hits, `git diff --check`; local CI-equivalent frontend job and doc verifier pass on an export of the committed tree
- [x] `.env.example` untouched (blob `22d7ec4e5aaf` before and after), not staged; proposed restore recorded separately
- [x] Remote Linux CI run of all jobs on the release commit (first run on `1930f8d` passed; the closure commit's runs are tracked in the Phase 6 final section). Reconciled in R104-0: runs 41352727469 and 41353876030 on the historical release commit `7e40604`, and 41663913648 on `237e4e1` (same tree), all jobs success
- [x] Architectural review of the CI security policy (accepted with the Phase 6 release decision, 2026-11-01)
- [ ] R102 exception re-reviewed or removed by 2026-11-30 UTC

## Phase 6 — final release acceptance, Linux CI validation and closure (evidence: `reports/phase-6-final.md`)
- [x] Architectural decision: Phase 6 accepted for a local-only release (final release review, 2026-11-01)
- [x] Baseline: own Git root, `feature/phase-6c-gemini-free` at `1930f8d`, 8 commits ahead of `main` (`1a57d65` = `phase-5`), 0 behind; only the user's `.env.example` modified (kept, never staged); no `phase-6` tag
- [x] Pre-push review: only Phase 6 work (112 files, no migration, no backend dependency change, no local artifacts); screenshots inspected (metadata only); history secret scan 0 secret-value hits, with the 40 accepted `url_userinfo` fixture hits
- [x] Workflow eligibility checked without changing it: `pull_request` unfiltered, no draft/path/`if:` skips, no skip directive, no repository secrets, `contents: read`, no deploy or merge step
- [x] Branch pushed normally (no force, no tags); draft PR #1 into `main` created with `draft: true` (Git Credential Manager token used once with the user's approval, never printed or stored); not merged, not ready for review, no auto-merge
- [x] Linux CI run 41350036376 (`pull_request`, head `1930f8d`, merge commit `f520383`, identical tree): docs 124978031708, python 124978031071, frontend 124978030907 all success, every configured step executed, none skipped; R102 annotation CONDITIONAL PASS (not a clean audit, only GHSA-g3hv-75wg-m249 accepted). Linux test counts not verified (job logs need admin rights)
- [x] Release review: Phase 5 contracts preserved (additive `provider` parameter only), ADR-28…38 consistent with the code, project isolation of the new filter, Gemini cost never priced, metadata-only capture; real Gemini trace re-checked by read-only SQL (2 spans, `gcp.gemini` / `gemini-3.1-flash-lite`, 18 / 1 tokens, cost unknown, provenance asserted); R101 `pyasn1` 0.6.5 in lock and venv
- [x] Operational limitations recorded, not resolved: local-only scope without login; attested Free Tier; asserted provenance; the `:8010` backend runs pre-6C-C code until restarted; the OpenAI price evidence expires 2026-11-07T21:40Z; `.env.example` restore is the operator's decision (`reports/phase-6-final.md` §8)
- [x] Closure commit: documentation only (this file and the report); doc verifier on an export of the committed tree; `git diff --check`
- [x] The closure commit's own `pull_request` CI run passes (recorded after the push, never before it exists): run 41352727469, head `7e40604` (historical SHA), docs, python, frontend success (recorded in the `phase-6` tag annotation; reconciled in R104-0)
- [x] Fast-forward of `main` to the closure commit and its `push` CI run passes (separate authorization): PR #1 merged by fast-forward 2026-11-01T21:48:18Z (merge commit `7e40604`, no merge commit); `push` run 41353876030 success (reconciled in R104-0)
- [x] Annotated `phase-6` tag on that commit recording both runs, pushed alone (separate authorization); after the history rewrite: tag object `35cd43c` → `237e4e1`, annotation unchanged and still naming `7e40604` (reconciled in R104-0)
- [ ] R102 remediated or re-reviewed by 2026-11-30 UTC; R104 Python audits, Bandit and secret scan in CI (separate architectural decision)

## Post-release reconciliation — history rewrite and R104-0 (2026-11-03; evidence: `reports/phase-6-final.md` §12)
- [x] Baseline: own Git root `C:/Users/PC/LLM-Insight-Development`, `main` = `origin/main` = `237e4e169aaa22a0106b6f7ffaf073bdf4294ca9`, tree `826f7db0f6e763e9080f5fe2f68177f176dd78e9`; tags `phase-0` … `phase-6` identical locally and on `origin`; only the user's untracked handoff note in the working tree (not staged)
- [x] History rewrite recorded, not repeated: the GitHub history was rewritten after the release (all SHAs new, only the `Co-Authored-By: Claude …` trailers removed); six release-relevant commits checked old → new with identical trees: `7e40604` → `237e4e1`, `1930f8d` → `f3e0fc7`, `3584085` → `1ef3a64`, `5f77c94` → `ff9a7e9`, `e109846` → `065c664`, `1a57d65` → `26f733a` (`reports/phase-6-final.md` §12); older SHAs in this file and in the reports are historical identifiers
- [x] Current `push` CI on `237e4e1`: run 41663913648, docs, python, frontend success
- [x] Tags not changed: the `phase-5` and `phase-6` annotations keep their historical SHAs and run IDs by decision; no GitHub Release exists
- [x] Disclosure: `refs/pull/1/head` (GitHub-managed) still points to `7e40604`, so the original commits and their trailers stay public; recorded as R105, **open, awaiting risk acceptance**
- [x] README status line corrected (one operator-run Gemini Free Tier call; OpenAI offline only; Gemini cost unknown; local only)
- [x] Documentation only (`PHASE_STATUS.md`, `reports/phase-6-final.md`, `SOURCES_AND_RISKS.md`, `README.md`); `verify_phase0.py` and `git diff --check` before the commit; commit with the user's configured Git identity, no `Co-Authored-By` trailer
- [ ] R105 risk-acceptance decision (architectural review)
- [x] R104-1 … R104-6 CI coverage, each step separately approved (closure below; R104 resolves on merge)

## R104-1 — Python dependency audit CI gate (2026-11-04; evidence: `reports/r104.md`)
- [x] Baseline: `feature/r104-ci-coverage` at `cd1dd81` (R104-0) on `main` `237e4e1`; only the user's untracked handoff note in the working tree
- [x] Tool lock `security/tools/requirements-audit.lock`: `pip-audit` 2.10.1, `bandit` 1.9.4 (locked for R104-2 only) and their transitive closure for CPython 3.12.6 on Linux, 32 packages, 457 PyPI SHA-256 hashes; every version first uploaded before 2026-10-21T00:00Z (five transitives held back); generated in a throwaway `python:3.12.6-slim-bookworm` container, not the project's compose stack (`ARCHITECTURE.md` §6)
- [x] Toolchain verified locally on Linux: hash-checked install, `pip check`, versions, imports, installed set = lock, tamper test refused
- [x] CI job `security-python` (independent, `contents: read`, 10 min, no `continue-on-error`): four `pip-audit` runs (2 lockfiles × PyPI/OSV), fail-closed gate, reports uploaded with `actions/upload-artifact` v7.0.2 pinned to its commit SHA, 14 days; existing jobs and the R102 npm policy unchanged
- [x] Local Linux execution of the job's own steps: four audits PASS (51 / 60 packages, 0 vulnerabilities); negative cases (known vulnerable `requests` 2.19.1, service outage, invalid lockfile, missing report, timeout) each fail the job with the right class; gate self-tests 18 passed; Ruff 0.17.0 clean
- [x] GitHub Linux CI run of `security-python`: run 41726371246 (`pull_request`, draft PR #2, head `5e773c3`) success, all four jobs (verified read-only in R104-2, `reports/r104.md` R104-2 §1)
- [x] R104-2 … R104-6, each step separately approved (sections below)

## R104-2 — Fail-closed Bandit CI gate (2026-11-04; evidence: `reports/r104.md`)
- [x] Baseline: `feature/r104-ci-coverage` at `5e773c3` (R104-1) on `main` `237e4e1`; only the user's untracked handoff note in the working tree
- [x] Bandit 1.9.4 semantics read from the installed source and confirmed by experiment: `-b` matches by file/test/text only and hides a second finding of a baselined kind (exit 0), a malformed baseline is only a warning, an unparsable file does not change the exit code; `-b` is not used
- [x] Scope `backend/app sdk/src demo_app scripts` (historical, includes `scripts/qa/tests`); full scan 45 findings, High 1 / Medium 5 / Low 39, no errors, no `nosec`; six historical High/Medium fingerprints reproduced; 42 findings equal the `phase-5` tag; one new Low (B106, R104-1 test fixture) classified R104-A; `backend/alembic` 0 findings (not gated)
- [x] `security/bandit-baseline.json` (strict schema, 45 per-finding entries, no source text) and its row-for-row register in `SECURITY.md` §8; closed classes F5–F8, R104-A in the checker
- [x] `scripts/qa/bandit_baseline_check.py` (stdlib, no subprocess): fails on new, changed or missing findings at any severity, duplicates, unknown classes, register mismatch, schema errors, missing/malformed baseline or report, tool failure (exit, stderr, inconsistency), skipped or unscanned files, scope changes, `nosec` and `.bandit` files; 31 offline tests (19 report-level, 12 with real Bandit in-process), cases A–I all fail the gate
- [x] CI: five steps appended to `security-python` (self-tests with Bandit required, full scan with recorded exit status, baseline validation, gate, sanitized summary upload); run whenever the tool venv installed; no `--exit-zero`, `continue-on-error`, skips or severity filters; R104-1 steps, other jobs and the R102 policy unchanged
- [x] Local Linux execution of the job's steps: positive run passes (gate PASS, pip-audit 4 × PASS); new Medium, new High, same-count swap, unparsable file, `nosec` and scanner failure each fail the job; the uploaded summary contains no issue text or source line; Ruff 0.17.0 clean
- [x] R104-A decided in R104-2A: not accepted; B106 removed at source by renaming the fixture keyword, BL-38 and the class removed
- [x] GitHub Linux CI run of the extended `security-python` job: run 42489979355 (`pull_request`, head `d726e54`), all four jobs success

## R104-2A — Bandit finding reconciliation (2026-11-04; evidence: `reports/r104.md` R104-2A)
- [x] Baseline: `feature/r104-ci-coverage` at `f5fd2b4` (R104-2), remote feature head `5e773c3`, `main` `237e4e1`; only the user's untracked handoff note in the working tree
- [x] B106 removed at source: `report(unexpected_field="x")`; the malformed-report test passes and fails when the gate's top-level key allowlist is removed; no `nosec`, no exclusion, gate code unchanged
- [x] Full scan 44 findings (High 1 / Medium 5 / Low 38), no errors, no `nosec`, identical (fingerprint, occurrence) set to the `phase-6` tree; baseline 44 entries, ids stable, register in `SECURITY.md` §8 updated; class R104-A removed from the checker
- [x] Per-finding origins from scans of every release tag (earliest tag containing the identical finding, absent before, present after); none unverified; classifications unchanged
- [x] 52 self-tests OK, 0 skipped with Bandit (new: B106, stale entry, altered finding); local Linux job: positive PASS, new B106 / new High / altered / stale / same-count swap each fail; Ruff 0.17.0 clean
- [x] GitHub Linux CI fully green: run 42484820349 on `ac5c868` executed (`docs`, `python`, `security-python`, Bandit gate success; `frontend` failed at the R102 policy step); run 42489979355 on `d726e54`: all four jobs success

## R102 — strict advisory range reconciliation (2026-11-08; evidence: `reports/r104.md`, R102 reconciliation)
- [x] Cause recorded: GitHub run 42484820349 failed overall because the old R102 policy no longer matched the advisory revised on 2026-11-04 (metadata drift, no fix of the vulnerability)
- [x] Verified with the Advisory API and real `npm audit --json` on Node 22.23.4: npm source 1244623, advisory range `>=7.0.0 <11.0.1`, npm entry range `7.0.0 - 11.0.0`, moderate, CWE-405; one copy `minimatch` 10.2.6; 8 dev-only findings; production audit 0
- [x] Evaluator and policy schema version 2: required `npmReportedRange`, two independent exact checks, wildcards refused; `approvedOn` and `reviewDeadline` unchanged; no dependency change
- [x] Local Linux: 88 policy tests and 349 frontend unit tests passed, 0 failed, 0 skipped; typecheck, lint, build, bundle scan pass; 50/50 evaluator and 12/12 policy mutants caught; result CONDITIONAL PASS, not a clean audit
- [x] External reviewer evidence recorded: `bandit-summary` artifact 13619410166 inspected by the reviewer (bandit 1.9.4, 104 files, 44 baseline entries, 1 / 5 / 38, PASS, 0 problems); not downloaded by the implementing agent
- [x] GitHub Linux CI run of this change: run 42489979355 (`pull_request`, head `d726e54`), all four jobs success
- [ ] R102 remediated or re-reviewed by 2026-11-30 UTC

## R104-3 — Fail-closed Git history secret-scan CI gate (2026-11-08; evidence: `reports/r104.md` R104-3)
- [x] Gate `scripts/qa/secret_history_gate.py` and job `security-history`: independently verified ref coverage, raw detectors without `ALLOW`, exact comparison with `security/secret-history-baseline.json` (38 occurrences) and the register in `SECURITY.md` §9; commit `24dea61`
- [x] Historical 40 reconciled per occurrence (26 blob + 14 working-file at `phase-6`); 38 raw matches classified individually; 0 secret-value hits locally
- [x] Local Linux execution of the job's steps: positive run passes, 11 negative runs fail; 59 gate tests, 27/27 mutants
- [x] GitHub Linux CI: run 42497852950 (`pull_request`, head `24dea61`), all five jobs success; `security-history` PASS, 38 findings = 38 accepted entries, 0 problems, 39 commits / 7 tags / 506 trees / 1,016 blobs, refs pull 1 / remote 3 / tag 7
- [x] Artifacts `secret-history-summary` 13624098061, `bandit-summary` 13624197820 and `pip-audit-reports` 13623779335 inspected by the external reviewer using the GitHub connection; not downloaded by the implementing Claude session, which confirmed run, job and step conclusions and artifact metadata through the public API
- [x] R104-4 started on separate approval (below)

## R104-4 — Isolated Compose image build gate (2026-11-08; evidence: `reports/r104.md` R104-4)
- [x] Job `image-build`: run-specific Compose project, throwaway interpolation env, both images required, no container, no registry; existing jobs byte-identical
- [x] Build-context gap corrected in both `.dockerignore` files (nested `.env`, `.env.*`)
- [x] Offline negative tests A–I and mutation check; Bandit baseline unchanged (44 entries); history secret gate 38 = 38 on a clean clone
- [x] Real image builds on the GitHub runner: run 42508163312 on `087b829`, build and verification steps success; the earlier run 42505551056 built both images and failed at verification. NOT RUN locally (no isolated Docker daemon)
- [x] GitHub Linux CI green for `image-build`: run 42508163312 (`pull_request`, attempt 1, head `087b829`), all six jobs success; run 42505551056 on `4db0086` failed at verification (an empty `.ci-canary` directory classified as an excluded file leak, corrected in `087b829`)
- [x] R104-5 started on separate approval (below)

## R104-5 — Isolated Playwright E2E gate (2026-11-08; evidence: `reports/r104.md` R104-5)
- [x] Job `e2e`: run-specific Compose project (PostgreSQL only), throwaway env, real backend and Next.js, Playwright Chromium, every discovered test must run and pass; the six existing jobs unchanged
- [x] Offline negative tests A–N and mutation check; Bandit baseline unchanged (44 entries); history secret gate 38 = 38 on a clean clone
- [x] Real browser, Next.js, backend and database run on GitHub: run 42517706300 on `689cf5f`, 60 of 62 tests passed; NOT RUN locally
- [x] GitHub Linux CI green for `e2e`: run 42520879317 (`pull_request`, attempt 1, head `7da7959`), all seven jobs success; run 42515801096 on `fef56cf` failed in the self-test step (racy test, corrected) and run 42517706300 on `689cf5f` failed 2 of 62 tests on a 4 px mobile overflow (corrected in `7da7959`)
- [x] R104-6 started on separate approval (below)

## R104-6 — Closure (2026-11-09; evidence: `reports/r104.md` R104-6)
- [x] Every formerly local gate of R104 runs in CI: `pip-audit`, Bandit, history secret scan, compose image build, Playwright E2E; npm part resolved earlier
- [x] All seven jobs success on `c2eef4e` (run 42522499520, `pull_request`, attempt 1); the real failures found on the way are recorded with their fixes
- [ ] PR #2 merged and `main` CI green (the user merges; R104 resolves then)

## R133 — Main-backend rate-limit exhaustion in the CI E2E suite (2026-11-11; evidence: `reports/r133.md`)
- [x] Observed: `main` run 43013325462 on `ae65321` (the PR #9 merge) failed in `e2e`, 70 of 72; the other six jobs succeeded. PR run 43004380869 on the identical tree passed 72 of 72. The two failures and the 429 timestamps were verified by the reviewer from the job log and the `e2e-summary` artifact
- [x] Cause: the main E2E backend's default `/api/v1` bucket (10/s, burst 20, keyed by client address) is shared by the dashboard's server-side reads and the tests' direct reads from `127.0.0.1`. A 429 renders the designed error state instead of the awaited element. The backend's own reason code was not observable, and runner speed was not measured
- [x] CI-only correction authorized 2026-11-11 (conditional GO): `scripts/qa/e2e_gate.sh` sizes the main backend process's bucket at 1000/1000; the report check reads that backend's log fail-closed and fails on any rate-limit rejection, recording `backend_rate_limited`. No production, SDK, migration, dependency, workflow, environment-file or test-assertion change; the `:8001`/`:8002` 429 backends unchanged
- [x] Local verification: see `reports/r133.md` §6. Committed as `11486ee` on `fix/r133-e2e-rate-limit`; pushed by the owner as PR #10
- [x] PR #10 CI run 43044114789 (attempt 1 and the failed-jobs re-run): `e2e` failed at step 10, before any test ran. `npx playwright install --with-deps chromium` could not resolve the Ubuntu mirror, and the step hit its `timeout 600` (exit 124). The other six jobs succeeded. The log was read by the reviewer; run, job, step and annotation data from the public API (`reports/r133.md` §9)
- [x] Installation correction authorized 2026-11-11: the gate installs the pinned browser only (`npx playwright install chromium`), then:
  - checks with `dpkg-query` that the 33 packages Playwright 1.64.0's `--with-deps` would install are present: libraries required, fonts and `xvfb` warned by name;
  - launches the exact CI Chromium on a local page, fail-closed.

  No workflow, timeout, test or production change. A second local commit, not pushed
- [x] GitHub CI, read from the public API on 2026-11-11: PR #10 run 43075270239 (`74e1df1`) and post-merge `main` run 43077503488 (`3e4d622`), all seven jobs successful; in `e2e`, steps 10 (browser) and 11 (Playwright) succeeded. The gate fails closed on fewer than 72 passing tests, a project count other than five, a rate-limit rejection, a missing library package or a failed launch check, so those criteria held; the summary artifact itself was not downloaded (it needs authentication). Both runs carry one warning: `BROWSER_RENDERING_PACKAGES_MISSING`, 7 font packages (`fonts-unifont`, `xfonts-cyrillic`, `xfonts-scalable`, `fonts-ipafont-gothic`, `fonts-wqy-zenhei`, `fonts-tlwg-loma-otf`, `fonts-freefont-ttf`); all 72 tests passed with them absent. **R133 closed**

## Phase 7A — Gemini content demo: architecture and source audit (2026-11-09; evidence: `reports/phase-7a.md`)
- [x] Source and Git baseline verified: `feature/phase-7-gemini-demo` at `4350306`, equal to GitHub `main`; three user-owned untracked files preserved
- [x] Inventory recorded with source facts, historical claims, unverified runtime facts and provider facts separated
- [x] Design written: three versioned synthetic scenarios, CLI contract, trace contract, security boundaries, 7B file map, 7C evidence matrix, 7D operator gate
- [x] Decision extending ADR-36 drafted in the report, status PROPOSED; `ADR.md` unchanged
- [x] Design reviewed and §11.2 decisions taken (Phase 7B instruction, 2026-11-09; ADR-39 accepted for implementation)
- [x] Phase 7B offline implementation (`reports/phase-7b.md`; 0 provider requests; no real call made through the new runner)
- [ ] Phase 7C offline integration evidence (local implementation complete, `reports/phase-7c.md`; 25 database-backed tests await the first GitHub run)
- [ ] Phase 7D offline package; a real Gemini request needs new, explicit authorization for one request

## Phase 8A-0 — Multi-provider free API integration: architecture and readiness audit (2026-11-09; evidence: `reports/phase-8a-0.md`)
- [x] Audit and design written; 0 provider API requests; no adapter, guard, ADR, migration or stored data changed
- [x] Observed on GitHub: PR #3 merged, `main` at `91c4d7d`, all seven CI jobs successful on `e581dc4` and on `91c4d7d`; the 7C item above is left for the reviewer to close
- [x] Reviewed 2026-11-09: conditional GO for offline development; decisions and renumbering recorded in §16 of the report
- [x] Checkpoint 8A-1 offline: fail-closed Groq and OpenRouter cost (ADR-40), Groq key detection in the SDK, backend and scanners (ADR-41). Evidence: `reports/phase-8a-1.md`
- [x] Review closure of ADR-41: accepted in principle, six conditions verified, explicit and narrow amendment of ADR-15 applied
- [x] 8A-1 merged by PR #4 (`d10e16b`). Observed on GitHub: run 42612935755 on `d10e16b`, all seven CI jobs successful, including `python` with `LLMI_TEST_REQUIRE_DB=1`. Only job conclusions were read
- [x] Checkpoint 8A-2 offline implementation: `GroqTextAdapter` (ADR-42, ADR-43). Committed locally on `feature/phase-8a-groq-adapter`, not pushed; 0 provider requests; evidence: `reports/phase-8a-2.md`
- [x] 8A-2 offline verification, on the final code including the strict Content-Type validation: mutations 50 of 50 caught across three runs (0 survived, 0 invalid, 0 unfinished), 17 of 17 supplementary caught; SDK clean-install regression 1589 tests, 1508 passed, 0 failed, 81 skipped (database-backed); backend unit 1202 passed
- [x] Architectural review of 8A-2, 2026-11-09: GO for commit and CI submission preparation; the Content-Type correction and offline verification accepted
- [x] 8A-2 merged by PR #5 (`eece127`). Observed on GitHub: run 42675227030 on `eece127`, `main`, all seven CI jobs successful, including `python`. Only job conclusions were read, so the 6 PostgreSQL tests of `test_live_groq.py` are known to have passed only through that job's result
- [x] Checkpoint 8A-3 offline implementation: Groq Free Plan guard and endpoint boundary (ADR-44). **Execution stays disabled** (`REAL_EXECUTION_ENABLED` false, no evidence, no allowlisted model). Committed as `ec2bb61` on `feature/phase-8a3-free-plan-guard`, pushed by the owner (PR #6); 0 provider requests; evidence: `reports/phase-8a-3.md`
- [x] 8A-3 offline verification: SDK clean-install regression 1894 tests, 1813 passed, 0 failed, 81 skipped (database-backed); backend unit 1202 passed; mutations 112 of 112 caught across two runs (0 survived, 0 invalid, 0 unfinished)
- [x] Architectural review of 8A-3: CONDITIONAL GO for one local commit received 2026-11-10, subject to a final architecture verification. That verification found a fail-open in the endpoint boundary (F1, `reports/phase-8a-3.md` §12). **Commit held**
- [x] F1/F2 correction, authorized 2026-11-10 without commit authorization: the transport and pool are compared by identity and audited shape, at client validation and again before the send, with no check skipped on a wrong type. The four unset pool attributes must be present and `None`. Corrected code: SDK clean-install regression 2056 tests, 1975 passed, 0 failed, 81 skipped (database-backed); backend unit 1202 passed; mutations 142 of 142 caught across two runs (the 112 earlier ones rerun, 30 new; 0 survived); evidence: `reports/phase-8a-3.md` §13
- [x] Architectural review of the F1/F2 correction, 2026-11-10: GO for one local commit only. Committed as `ec2bb61` (`reports/phase-8a-3.md` §14)
- [x] R123: security fix for the two Gemini findings implemented in Phase 8C (ADR-49, `reports/phase-8c.md`); **architectural review still required** before any additional real Gemini call
- [x] First CI run of 8A-3 (PR #6, run 42759696574 on `ec2bb61`, reported by the reviewer): `security-history` failed with one new finding, the reviewed `CHANGE_ME` placeholder in the new `.env.example` blob. Baseline entry SH-39 committed as `d72379a` (`reports/phase-8a-3.md` §15); PR #6 merged into `main` at `d32f3df`. The reviewer reports that the PR run and the post-merge `main` run passed all seven jobs; those runs were not read by the implementing session
- [x] Checkpoint 8A-4 offline implementation, authorized 2026-11-10: comparison scenarios and runner `demo_app.live_compare` (ADR-45). Groq only; `gemini` (R123) and `openrouter` (no adapter) refused; the send gate now refuses a request whose credential is not the validated `GROQ_API_KEY`. **Execution stays disabled** (lock false, no evidence, empty allowlist). Committed locally on `feature/phase-8a4-comparison-runner`, not pushed; 0 provider requests; evidence: `reports/phase-8a-4.md`
- [x] 8A-4 offline verification: SDK clean-install regression 2200 tests, 2119 passed, 0 failed, 81 skipped (database-backed); backend unit 1208 passed; mutations 48 of 48 new and 142 of 142 earlier caught on the final code; history gate 39 = 39
- [x] Architectural review of 8A-4, 2026-11-10: conditional GO for one local commit only; the credential-binding check and the R127 deferral approved (`reports/phase-8a-4.md` §12)
- [x] `.env.example` Groq comment update with its reviewed history-baseline entry (R127): done in Phase 8B, entry SH-40 (`reports/phase-8b.md`)
- [x] First CI run of 8A-4: PR #7 (`e2a275a`) merged into `main` at `b34a018`. The reviewer reports that the PR run and the post-merge `main` run passed all seven jobs, with 1896 backend and 2200 SDK tests passing and the database required; those runs were not read by the implementing session
- [x] Checkpoint 8A-5 offline integration implementation, authorized 2026-11-11: `sdk/tests/integration/test_live_compare.py` (20 tests) and `backend/tests/integration/test_multi_provider_read.py` (8 tests); test code only, no production change. Committed locally on `feature/phase-8a5-comparison-integration`, not pushed; 0 provider requests; evidence: `reports/phase-8a-5.md`
- [x] 8A-5 local verification: the 3 database-free integration tests passed; the 25 database tests skip with a reason in normal mode and fail in required mode, shown without a database; see the report for the regressions
- [ ] **25 database-backed 8A-5 tests: collected, NOT RUN LOCALLY**; they require the GitHub `python` job with `LLMI_TEST_REQUIRE_DB=1`
- [x] Architectural review of 8A-5, 2026-11-11: conditional GO for one local commit and CI submission (`reports/phase-8a-5.md` §9); the checkpoint closes only when CI passes
- [x] First CI run of 8A-5 (PR #8, `2f3c698`, run `42944649002`, as reported by the reviewer): six jobs passed; `python` failed with 4 backend setup errors, from a random-date price fixture against the append-only `model_prices` trigger. A test-fixture defect; no production change
- [x] Fixture correction authorized 2026-11-11: one session-scoped insert at a fixed date, with the precondition asserted; database-free checks pass. Second local commit, not pushed (`reports/phase-8a-5.md` §10)
- [x] Second CI run of 8A-5 (PR #8, `9f2a147`, run `42963391108`, as reported by the reviewer): backend 1904 passed; SDK 2213 passed, 1 failed, 6 errors; the other six jobs passed. F1: a second groq price, dated earlier, for the same model in the shared SDK session database. F2: three runner executions inside one test against the one-request-per-process slot. Both are test-design defects; no production change
- [x] F1 and F2 corrections authorized 2026-11-11: one session-scoped canonical groq price (`sdk/tests/integration/conftest.py`) for both modules; one runner execution per test case, a SYNTHETIC aggregate without the runner, and negative tests for a second execution. Database-free checks pass. Approved 2026-11-11 (conditional GO); a third local commit, not pushed (`reports/phase-8a-5.md` §11)
- [ ] CI retry of PR #8: all seven jobs; with `LLMI_TEST_REQUIRE_DB=1`, all backend integration tests and all SDK integration tests pass with no skip, including the 8 backend and 25 SDK comparison tests and the 6 `test_live_groq.py` tests; **NOT RUN**
- [x] 8A-5 closed: PR #8 merged into `main` at `3424644`. The reviewer reports that the PR run and the post-merge `main` run passed all seven jobs, with 1904 backend and 2225 SDK tests passing and the database required; those runs were not read by the implementing session
- [x] Checkpoint 8A-6 offline dashboard E2E implementation, authorized 2026-11-11: run-owned comparison project (`--set phase8a6`), `frontend/tests/e2e/phase8a6.spec.ts` (10 browser tests) and `backend/tests/unit/test_e2e_comparison_fixture.py` (8 offline tests). Test and fixture code only; no production, dependency, migration or CI change. Committed locally on `feature/phase-8a6-comparison-dashboard`, not pushed; 0 provider requests; evidence: `reports/phase-8a-6.md`
- [x] 8A-6 local verification: frontend typecheck, lint, 353 unit tests, build, bundle scan and the CI-style listing (72 tests) pass; backend unit 1216 passed; fixture mutants 8 of 8 caught
- [ ] **10 browser E2E tests of 8A-6: listed, NOT RUN LOCALLY** (they need the running backend, PostgreSQL and `frontend/.env.local`); they require the GitHub `e2e` job
- [x] Architectural review of 8A-6, 2026-11-11: conditional GO for one local commit (`reports/phase-8a-6.md` §11); the checkpoint closes only when the CI `e2e` job passes
- [x] First CI run of 8A-6 (PR #9, `7f393fc`, run `42985427705`, as reported by the reviewer): all 72 browser tests passed and the teardown deleted five projects, but the `e2e` job failed in `scripts/qa/e2e_check.py`, which still expected four run-owned projects; the other six jobs passed
- [x] E2E lifecycle gate correction authorized 2026-11-11: five projects, at least 72 tests, distinct positive ids required; self-tests extended (65 and 102 passed); gate mutants 11 of 11 caught. Second local commit, not pushed (`reports/phase-8a-6.md` §12)
- [ ] CI retry of PR #9: all seven jobs green, the `e2e` job with 72 tests and five projects deleted; **NOT RUN**
- [ ] Offline checkpoint 8A-7
- [ ] Real Groq or OpenRouter request: NO-GO until blockers B1 to B7 clear and each request is separately approved

## Phase 8B — Multi-provider live comparison MVP: offline implementation (2026-11-11; evidence: `reports/phase-8b.md`)
- [x] Scope confirmed by the owner: Groq and OpenRouter real inference and model comparison are MVP features; one integrated milestone; mutation campaigns not run by default
- [x] Baseline verified on GitHub: PR #10 merged, `main` at `3e4d622`, post-merge run green; worktree `feature/live-comparison-groq-openrouter` from `origin/main`, upstream tracking removed; the owner's working tree and files untouched
- [x] OpenRouter adapter (ADR-46), free-model guard with three locks and the shared process slot (ADR-47), two-provider runner and the Compare real-data note (ADR-48); OpenRouter docs re-read 2026-11-11
- [x] R127 corrected: `.env.example` Groq comment, OpenRouter block, history-baseline entry SH-40, `REVIEWED_BASELINE_SHA256`; risks R134–R139 added, R128 extended
- [x] Offline verification: see `reports/phase-8b.md` §6 for the exact commands and pass/fail/skip counts
- [ ] **Database-backed tests (SDK integration, including the new OpenRouter module): collected, NOT RUN LOCALLY**; they need the GitHub `python` job with `LLMI_TEST_REQUIRE_DB=1`
- [ ] GitHub CI of this branch: **NOT RUN** (not pushed)
- [ ] Pre-send blockers of `reports/phase-8b.md` §8 cleared and each real request separately authorized: **NO-GO**

## Phase 8C — Three-provider real comparison: readiness and preflight (2026-11-11; evidence: `reports/phase-8c.md`)
- [x] Reconciled: PR #11 merged (`e665877`); its CI runs queued when read (**not verified**); shipped locks confirmed in code
- [x] R123 fixed (strict attestation date; TLS key logging refused by the policy, the runner's own contexts and the client check in three places); ADR-49; R140, R141 added
- [x] Gemini added to `demo_app.live_compare` through the shared `live_gemini.run_single_request`; three providers verified offline on the same pinned scenario
- [x] Official model pages re-read; no account evidence exists or was inferred; preflight A–F written
- [ ] **Database-backed three-provider test: NOT RUN LOCALLY** (CI `python` job)
- [ ] GitHub CI of this branch: **NOT RUN** (not pushed); PR #11 CI: queued when read
- [ ] Real requests: **NO-GO for Groq, OpenRouter and Gemini** (`reports/phase-8c.md` §F); one request per provider only after its evidence, a reviewed release and a separate written authorization

## Phase 8D — Real provider readiness (2026-11-12; evidence: `reports/phase-8d.md`)
- [x] PR #12 merged (`0ab5e71`); its PR run and post-merge `main` run successful (public API)
- [x] Official docs re-read for the three models; operator attestations recorded as attestations, not billing evidence
- [x] Targeted offline tests 1414 passed; dry-run readiness probe: only the code locks remain (Groq 3, Gemini 1, OpenRouter 4)
- [x] Project 1 accepted for the local test; trace mixing documented (filter by `prompt_version`)
- [ ] Groq: account Limits evidence and a reviewed release; **NO-GO**
- [ ] Gemini: AI Studio check and a reviewed evidence re-release (which alone unlocks Gemini, R140); R123 review confirmation; **NO-GO**
- [ ] OpenRouter: R135 and free-endpoint routability under `data_collection: deny` unresolved; **NO-GO**

## Phase 8E — Independent Gemini execution switch (2026-11-12; evidence: `reports/phase-8e.md`)
- [x] `gemini_live.REAL_EXECUTION_ENABLED = False` (ADR-50): refused in the policy and the session reservation, no environment override; R140 resolved, for review
- [x] Both Gemini runners, the content runner and `live_compare --provider gemini` blocked; 30 as-shipped gate tests; Gemini dry-run blockers now `real_execution_disabled` and `free_tier_evidence_stale`
- [ ] Gemini real request: **NO-GO** until the evidence re-release and the switch release, two separate reviewed changes, and the authorization
- [ ] GitHub CI of this commit: **NOT RUN** (not pushed)

## Phase 8G-A — Groq Free Plan evidence (2026-11-12; evidence: `reports/phase-8g.md`)
- [x] Operator account Limits evidence recorded for `openai/gpt-oss-20b` (Free, operator attestation; 30 RPM, 1000 RPD, 8000 TPM, 200000 TPD)
- [x] Evidence-only change: `FREE_PLAN_MODELS` = `openai/gpt-oss-20b`, `FREE_PLAN_EVIDENCE_VERIFIED_AT` 2026-11-12 06:00 UTC (window ends 2026-11-26 06:00 UTC); `groq_adapter.REAL_EXECUTION_ENABLED` stays false
- [x] Offline: 1108 unit tests passed; Groq dry run refused only by `real_execution_disabled`
- [ ] Groq real request: **NO-GO** until CI on this commit, the written authorization and the reviewed release (patch B)
- [x] Phase 8G-B execution release prepared on `release/groq-first-observed-request` (`groq_adapter.REAL_EXECUTION_ENABLED = True`; Gemini and OpenRouter locked); unit 2461 passed, export integration 41 passed / 112 skipped; **CI NOT RUN until pushed**

## Phase 9A — Reference list price (F3): design and evidence (2026-11-19; evidence: `reports/phase-9a.md`)
- [x] Owner decision: F3 proceeds as a read-time list-price reference, outside the cost ledger (ledger pricing of paid tiers and an OpenAI-only refresh not chosen)
- [x] Official pages read 2026-11-19: Groq `openai/gpt-oss-20b` 0.075 / 0.037 cached / 0.30 output; Gemini `gemini-3.1-flash-lite` paid Standard 0.25 / 0.025 cached / 1.50 output incl. thinking (per 1M tokens)
- [x] Design written: invariants I1–I8, code-only catalogue, M15-only API block, Compare column; a new ADR drafted in the report (§6), status PROPOSED; `ADR.md` unchanged; 0 code, 0 migration, 0 provider requests
- [x] Owner review: the design and D1–D6 accepted as recommended (2026-11-19); the decision recorded as ADR-52 in `ADR.md`
- [x] Phase 9B implementation (tests first), below

## Phase 9B — Reference list price (F3): implementation (2026-11-19; evidence: `reports/phase-9b.md`)
- [x] Branch `feature/phase-9b-reference-price` from `9120e7f`; 0 provider requests, 0 migrations; nothing committed or pushed
- [x] Evidence re-read as raw HTML (2026-11-19T12:51:42Z): Groq and Gemini prices unchanged from 9A
- [x] Catalogue `reference-2026-11-19`, read-time `reference_list_price` per `models/compare` group, a Compare column with "Not billed" and a notice; ledger, `cost` blocks and the overview unchanged (ADR-52)
- [x] Tests: backend full 1986 passed (DB required), 32 new; frontend unit 412 passed, 19 new; typecheck, lint, build (Node 22.23.4), bundle scan 0; Ruff; doc verifier; mutants 7/7 caught
- [x] Real local backend, read-only: the same-window compare response equals the Phase 3C recording outside the new block; `models-compare.live.json` re-recorded
- [x] SDK full suite (DB required): 2724 passed, 0 failed
- [x] Playwright E2E and GitHub CI: PR run 44544264449 on `aca65f2`, 7/7 jobs success including `e2e`; post-merge `main` run 44725779130 on `ad1ec8e`, 7/7 success (job conclusions read from the public API, 2026-11-20)
- [x] Owner review: committed as `aca65f2` in the 9A–9F batch, pushed by the owner, merged by PR #17

## Phase 9C — OpenAI price catalogue re-verification (2026-11-19; evidence: `reports/phase-9c.md`)
- [x] Five official pages read raw (2026-11-19T13:17:43Z): prices, snapshots, tiers, regional uplift and shutdown dates unchanged from `openai-2026-10-24`
- [x] Decision (recommended option, batch authorization): recorded in `PRICING.md` §2, no new version released; the SDK live guard stays closed by its 14-day rule until an approved smoke test releases a version
- [x] Documentation only; doc verifier 865 PASS

## Phase 9D — Confidence intervals and the exact paired test (F5) (2026-11-19; evidence: `reports/phase-9d.md`)
- [x] ADR-53 (batch authorization, recommended options): 95 % Wilson intervals per pass rate; exact two-sided McNemar on paired cases; additive to the mock report, derived at read for live imports (snapshot unchanged, no winner)
- [x] Stored live A/B (Groq, Gemini): 3/6 vs 5/6, 2 discordant pairs both v2, p = 0.5, not significant
- [x] Tests: backend full 2008 passed (DB required); frontend unit 430 passed; typecheck, lint, Ruff; mutants 5 caught, 2 equivalent (reasoned)
- [x] Playwright E2E: the `e2e` job passed in runs 44544264449 and 44725779130, including the updated `phase4d.spec.ts` expectation

## Phase 9E — npm audit exceptions R102 / R144 re-review (2026-11-19; evidence: `reports/phase-9e.md`)
- [x] R144 removed: GitHub withdrew GHSA-fc5q-39xc-vjpf (2026-11-18T15:38:56Z); empty-cache audits no longer report `picomatch`
- [x] R102 re-approved once, deadline 2026-12-07: the fix (`typescript-eslint` 8.73.0) meets the two-week rule only on 2026-11-30T17:07:26Z; upgrade planned then
- [x] Gate: CONDITIONAL PASS, 7 findings, R102 only; production audit 0; policy tests 116 passed; no dependency changed
- [ ] On or after 2026-11-30T17:07:26Z: upgrade `typescript-eslint` / `@typescript-eslint/*` to 8.73.x and delete R102

## Phase 9F — PHASE_STATUS cleanup (2026-11-19; evidence: `reports/phase-9f.md`)
- [x] Running log (57 KB above Phase 0) moved verbatim to `reports/phase-status-log-2026-11.md`; short dated header written; phase sections byte-identical
- [x] Doc verifier 865 PASS, 0 FAIL
- [x] Batch commit of Phases 9A–9F: `aca65f2` (2026-11-19), pushed by the owner, PR #17 merged into `main` as `ad1ec8e`

## Phase 10A — Agent, tool, retrieval and MCP steps (F1) (2026-11-20; evidence: `reports/phase-10a.md`)
- [x] ADR-54 (batch authorization): `llminsight.attrmap.v2` allowlists agent/tool/data-source/MCP identifiers and denies their content keys; read-time `genai_step` and `agent_summary`; diagnostics `rules.v2` with `repeated_tool_calls`; SDK `agent_span` / `tool_span`; trace-detail "Agent steps"
- [x] Tests: backend unit 19 + integration 3 new; SDK 9 new; frontend 16 new; 10 earlier version pins updated with notes; mutants 5/5 caught after two added tests
- [x] Trace-detail contract fixtures re-recorded read-only, identical apart from the new fields

## Phase 10B — Streaming time to first chunk (F7, M20) (2026-11-20; evidence: `reports/phase-10b.md`)
- [x] ADR-55: `gen_ai.response.time_to_first_chunk` (seconds, [0, 3600]) in attrmap v2; M20 per provenance bucket and per Compare group, never estimated; SDK `RequestParams.stream` and `record_first_chunk()`
- [x] Tests: backend full 2047 passed (DB required, 10A + 10B); SDK 35 targeted; frontend unit 459 passed; typecheck, lint, Ruff, mypy; doc verifier 865 PASS
- [x] Overview and Compare contract fixtures re-recorded read-only; differences limited to the new M20 shape (and the 6C-C `filters.provider` echo on the Phase 3A overview)

## Phase 10C — Pseudonymous sessions (F2) (2026-11-20; evidence: `reports/phase-10c.md`)
- [x] ADR-56 (amends ADR-19 for `gen_ai.conversation.id` only): server-side keyed, project-scoped HMAC `llminsight.session.hash`; raw ID never stored, logged or returned; `GET /api/v1/sessions`, `GET /api/v1/traces?session=`, Sessions page; user identifiers stay denied
- [x] Tests: backend unit 13 + integration 4 (sentinel raw IDs absent everywhere; a real status-case defect found and fixed); frontend 16; route, reader, navigation and query pins updated with notes

## Phase 10D — Deterministic error patterns (F8) (2026-11-20; evidence: `reports/phase-10d.md`)
- [x] ADR-57: `GET /api/v1/error-patterns` groups ERROR spans by exact fingerprint (source, provider, model or tool, error.type) with the diagnostics `error_class` label; overview section; no clustering, no root-cause claim
- [x] Tests: backend integration 3 (an `array_agg` SQL defect found and fixed); frontend 15

## Phase 10E — CI regression gate on the golden set (F4) (2026-11-20; evidence: `reports/phase-10e.md`)
- [x] ADR-58: `python -m app.eval_gate --config evals/gate.json`, offline (mock, SYNTHETIC, no DB or secret); fails on lost passes or a pass-rate floor; CI `python` job step added; v1 → v2 fails on G04, G07–G10 as documented
- [x] Tests: 20 unit (a stdout-binding defect found and fixed); CI run pending the owner's push

## Phase 10F — Threshold alert rules (F6, M21) (2026-11-20; evidence: `reports/phase-10f.md`)
- [x] ADR-59: `backend/alerts/rules.json` evaluated at read time on M04, M06, M10, M20; `GET /api/v1/alerts`; overview strip; `insufficient_data` below `min_n`; cost lower bound; **no outbound delivery**
- [x] Tests: backend unit 21 + integration 3; frontend 14

## Batch 10A–10F — final verification (2026-11-23)
- [x] Backend full: 2111 passed, 0 failed (PostgreSQL required)
- [x] Frontend: typecheck, lint, unit 503 passed, audit-policy 116 passed, production audit 0, build (Node 22.23.4), bundle scan 0
- [x] Mutation checks: 10A 5/5 (after two added tests), 10B–10F 9/9
- [x] SDK: six live-backend tests pinned `attrmap.v1` or the old M20 shape and were updated with notes (they first failed because the PostgreSQL container had stopped; reproduced after a restart); SDK full suite: see the commit message
- [ ] GitHub CI (including the new eval-gate step and the Playwright E2E gate): after the owner's push

## Phase UI-R1 — Dark-first shell and overview hierarchy (2026-11-23; evidence: `reports/phase-ui-r1.md`)
- [x] Frontend only: navy/anthracite tokens with three accent tokens; sidebar groups Observe / Analyze / Evaluate; one scope panel; KPI sections (traffic and reliability, latency with its own M20 card, tokens and cost with a completeness bar); recent traces (limit 5); Alerts card
- [x] No backend, API, schema or URL contract change; no new dependency; every existing test id kept
- [x] typecheck, lint, unit 513 passed, build, bundle scan 0; mutants 5/5; Playwright E2E 72 passed against a disposable `_test` database (a pre-existing fixture flake, a run id with 11 digits read as a phone number, failed 5 tests on an earlier run; reported, not fixed here)
- [ ] Owner review; uncommitted

## Phase UI-R2 — Trace and agent explorer (2026-11-23; evidence: `reports/phase-ui-r2.md`)
- [x] E2E run-id flake fixed in `scripts/dev/e2e_fixtures.py` (no 10+ digit runs by construction; backend label check unchanged); 8 backend tests
- [x] Trace page as a three-panel explorer (trace rail with the `list` listing, waterfall with step families and longest-span marks, inspector with TTFT, diagnostics and reviews of the span); narrow screens: rail disclosure and Spans / Selected span switch
- [x] Agent analysis: step-family counts, tool repeats, possible loop at the backend threshold (5) shown as a hypothesis; no tool content
- [x] Sticky context bar (Overview, Traces, trace page); Overview → Traces carries project, window, provenance, provider, model
- [x] Frontend only (plus the dev fixture scripts); no backend, API, schema or URL contract change; no new dependency
- [x] typecheck, lint, unit 530 passed, build, bundle scan 0; mutants 10/10; backend unit 1391 passed; scripts/qa 256 OK; Playwright E2E 78 passed on a disposable `_test` database; screenshots in `reports/assets/ui-r2/` (secret-checked)
- [ ] Owner review; uncommitted

## Phase UI-R3 — Model analytics, session explorer, collapsible sidebar (2026-11-23; evidence: `reports/phase-ui-r3.md`)
- [x] Models: one scope panel, side-by-side pair (`?a=`/`?b=` group keys, dashboard only) with p50/p95/p99, tokens with subsets not added, cost completeness, provenance, stated differences and no ranking or significance claim; p50/p95/p99 chart; group → trace list (provider, model, window only)
- [x] Sessions: inspected session (`?session=`) with the backend's aggregates and its traces in time order from `GET /api/v1/traces?session=`; cards on phones; no raw id, no invented session metric
- [x] Collapsible sidebar (state kept across client navigation, nothing stored); explorer three panels by container query: at 1280 px with the sidebar collapsed (rail 240, spans 504, inspector 336 px); list panel can be hidden
- [x] Shared chart tooltip; auto-refresh select disabled until hydrated (root cause of an intermittent E2E failure)
- [x] typecheck, lint, unit 552 passed, build, bundle scan 0; mutants 10/10; backend unit 1394 passed; scripts/qa 256 OK; Playwright 91 passed (incl. 6 local visual-regression tests, 12 baselines; not discovered in CI); screenshots in `reports/assets/ui-r3/` (secret-checked)
- [ ] Owner review; uncommitted

## Phase UI-R4 — Evaluation workspace and frontend finalization (2026-11-24; evidence: `reports/phase-ui-r4.md`)
- [x] Datasets: facts, workflow strip, breadcrumbs, page-scoped case filters (`?rule=`/`?origin=`/`?expected=`, dashboard only), case index, trace → review → case lineage, previous/next, human-entered input never shown as a captured conversation; no action the API lacks
- [x] Experiments: mock runs labelled "Synthetic / Offline Mock", imported live reports labelled read-only and kept separate, run → its dataset, the existing report kept; no runner, no real-model path, no winner
- [x] Whole-frontend pass at 1280 (sidebar open and collapsed) and 390, dark and light: one h1, no horizontal scroll, skip link first, no secret; experiment table tightened
- [x] Linux visual regression: per-spec selection in CI with an explicit NOT RUN log line and no baseline writing in CI; **no Linux baselines produced** (WSL unreachable, no CI-equivalent image); Windows baselines kept (12 + 9 new)
- [x] typecheck, lint, unit 565 passed, build, bundle scan 0, audit-policy 116; mutants 10/10; scripts/qa OK; Playwright 104 passed (full suite after the last fixes); screenshots in `reports/assets/ui-r4/` (secret-checked)
- [ ] Owner review; Linux baselines on an ubuntu-24.04 runner; uncommitted

## Frontend final stabilization (2026-11-24; evidence: `reports/frontend-final-stabilization.md`)
- [x] Experiments `Started` fits at 1280 px (E2E-measured); focus moves to the chosen test case (index, previous/next, deep link; E2E and unit)
- [x] Linux visual regression: acceptance manifest + policy (pending → NOT RUN in CI with reason; accepted → must run; missing image → run stops; CI never writes baselines); manual capture workflow `visual-baselines.yml`; `ci.yml` unchanged. **Linux visual tests: NOT RUN** (no CI-equivalent environment locally: WSL is Ubuntu 22.04, no matching image)
- [x] Root cause of an intermittent E2E failure: clicks before hydration are held by React; the test waits for hydration (`waitForHydration`)
- [x] typecheck, lint, unit 575, audit-policy 116, build, bundle scan 0, backend unit 1394, ruff (CI commands), scripts/qa OK, docs 865; change-set credential scan 0; Playwright local 104/104 (incl. Windows visual)
- [ ] Owner: commit (GO), push and PR (conditional GO: state Linux visual NOT RUN), then the Linux baseline capture and acceptance

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
| `npm audit --omit=dev --audit-level=info` (frontend) | yes | Production dependency audit: any advisory of any severity fails; no exceptions (Phase 6, CI step) |
| `npm run test:audit-policy` (frontend) | yes | Offline fail-closed tests of the npm audit exception policy (Phase 6) |
| `npm audit --json > F; npm run audit:policy -- --report F --npm-exit-code $?` (frontend) | yes | Full audit evaluated against `security/npm-audit-policy.json`: PASS, CONDITIONAL PASS (only approved exceptions; not a clean audit) or FAIL (Phase 6, R102) |
| `npm run test:e2e` (frontend) | yes | Playwright (installed Edge) against the real backend on :8000; starts dashboards on :3001, an outage instance on :3002 and a wrong-token instance on :3003, plus (Phase 5A, T43) a second backend with a tiny `/api/v1` bucket on :8001 and a dashboard on :3004 reading through it (ports must be free); `LLMI_SCREENSHOT_DIR=<dir>` saves screenshots. Phase 3C: the global setup creates and seeds this run's own projects and the teardown deletes them (`LLMI_E2E_KEEP=1` keeps them); no other data is needed |
| `LLMI_SEED_PROJECT_KEY=<key> sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py [--set edge\|isolation] [--seed S]` (root) | yes | Phase 3B: SYNTHETIC edge-case traces (nesting, equal starts, orphan, cycle, mixed/unknown provenance, application error, hostile labels, 771-span trace) through the real `POST /v1/traces`; key from the environment only, loopback endpoint only; prints trace ids |
| `backend/.venv/Scripts/python scripts/dev/e2e_fixtures.py setup\|teardown\|experiment-counts\|add-trace --state <file> [--experiment <id>]` (root) | yes | Phase 8A-6: `setup` also creates the run's comparison project (`--set phase8a6`). Phase 6C-C: `setup` also creates the run's providers project (`--set phase6c`), `add-trace` sends one more SYNTHETIC trace to it (key minted and revoked per call). Phase 4D: `experiment-counts` reads one run-owned experiment's stored results from PostgreSQL (read-only; counts, case ids, provenance). Phase 3C (R41): create run-owned SYNTHETIC E2E projects (`e2e-<run>-main/-foreign/-xss …`), seed them through `/v1/traces`, revoke the keys / delete exactly those projects. Called by the Playwright global setup and teardown |
| `backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project <id> --approved-by <name> [--run-experiment --model <m>]` (root) | yes | Phase 4C: load the SYNTHETIC golden dataset (12 cases) through the real API and optionally run one mock A/B experiment; loopback only, token from `backend/.env`, never printed; prints ids and counts |
| `sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py --set phase3c` (root) | yes | Phase 3C: a retried call and a multi-provider/multi-model trace (SYNTHETIC) |
| `sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py --set phase6c\|phase6c-new` (root) | yes | Phase 6C-C: provider/provenance cases (openai, gcp.gemini, unknown provider; one asserted-real SYNTHETIC fixture span; a downgraded claim) / one more trace during a test |
| `sdk/.venv/Scripts/python scripts/dev/seed_trace_fixtures.py --set phase8a6` (root) | yes | Phase 8A-6: SYNTHETIC multi-provider comparison set (OpenAI priced, Gemini, three Groq scenarios under `demo.live-compare`, a SYNTHETIC OpenRouter row, a root-only trace, one adversarial trace); checked offline by `backend/tests/unit/test_e2e_comparison_fixture.py` |
| `docker compose --profile app up -d --build` / `docker compose --profile app config --quiet` (root) | yes | Phase 3C opt-in profile: backend + dashboard containers (one network namespace), dashboard on `127.0.0.1:3001`; the default `docker compose up` still starts PostgreSQL only. Stop with `docker compose --profile app stop backend frontend` (never `down` on the dev stack unless intended) |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` (root) | yes | Phase 5B: credential scan of every reachable blob, every commit/tag message and the working files (exact local secret values, never printed, plus patterns); exit 1 on any hit |
| `python -I scripts/qa/secret_history_gate.py {coverage,scan,validate-baseline,evaluate}` (root; on a clone or in CI, not on the development repository, whose local `refs/codex/` ref is rejected) | yes | R104-3 CI gate: full-history coverage verification, raw detectors without `ALLOW`, exact comparison with `security/secret-history-baseline.json` and `SECURITY.md` §9; sanitized summary; any problem exits 1 |
| `bash scripts/qa/image_build_gate.sh {preflight,build,verify}` (GitHub Actions only; refuses elsewhere) | yes | R104-4 CI gate: builds the Compose `app` images under a run-specific project name and verifies them without starting a container |
| `python -I -m unittest discover -s scripts/qa/tests -p test_image_build_check.py -v` and `bash scripts/qa/tests/test_image_build_gate.sh` (root; set `LLMI_PYTHON=python` on Windows) | yes | R104-4 offline tests; the shell suite uses a fake `docker` and contacts no daemon |
| `bash scripts/qa/e2e_gate.sh {prepare,install,database,backend,frontend,test,cleanup}` (GitHub Actions only; refuses elsewhere) | yes | R104-5 CI gate: the real Playwright suite on the ephemeral runner with an isolated PostgreSQL |
| `python -I -m unittest discover -s scripts/qa/tests -p test_e2e_check.py -v` and `bash scripts/qa/tests/test_e2e_gate.sh` (root; `LLMI_REAL_PYTHON` selects the interpreter) | yes | R104-5 offline tests; the shell suite uses fake processes and contacts no daemon, database or browser |
| `python -m pip install --require-hashes --no-deps --only-binary :all: -r security/tools/requirements-audit.lock` (root, in a separate throwaway venv only) | yes | R104-1: hash-checked `pip-audit` 2.10.1 / `bandit` 1.9.4 tool environment; never into `backend/.venv` or `sdk/.venv` |
| `python -I scripts/qa/pip_audit_gate.py evaluate --dir <raw> --upload-dir <out> --target backend=backend/requirements.lock --target sdk=sdk/requirements-dev.lock --service pypi --service osv` / `<tool-venv>/bin/python -I scripts/qa/pip_audit_gate.py check-env --lock security/tools/requirements-audit.lock` (root) | yes | R104-1: fail-closed evaluation of the four `pip-audit` runs recorded by the CI job (`<id>.json`, `.stderr.txt`, `.exit`) / installed tool set equals the lock. Self-tests: `python -I -m unittest discover -s scripts/qa/tests -v` |
| `python -I scripts/qa/bandit_baseline_check.py validate-baseline --baseline security/bandit-baseline.json --security-doc docs/SECURITY.md` / `… evaluate --report R --exit-file E --stderr-file S --baseline … --security-doc … --root . --target backend/app --target sdk/src --target demo_app --target scripts --out-dir O` (root) | yes | R104-2: baseline and register check / fail-closed evaluation of a recorded `bandit -r … -f json -o R -q` run; `candidates` and `register` are local review aids, never CI. Self-tests with real Bandit: `LLMI_TEST_REQUIRE_BANDIT=1 <tool-venv>/bin/python -I -m unittest discover -s scripts/qa/tests -p test_bandit_baseline_check.py -v` |
| `python -I scripts/qa/route_probe.py <port> <action-id> <out.json>` (root, against `next start`) | yes | Phase 5B: raw-socket Server Action dispatch probe (78 targets × 17 Host/Origin variants); exit 1 on a dispatch outside the write pages' same-origin checks |
| `python -I scripts/qa/backend_probe.py <port> <.env> <out.json>` (root) | yes | Phase 5B: backend alternate-path probe (every route, many spellings, with and without the token); run it against a throwaway backend, because unauthenticated requests drain the auth-failure bucket |
| `backend/.venv/Scripts/python -I scripts/qa/benchmark.py --repo . --db <name>_test --out <dir>` (root) | yes | Phase 5B: local benchmark in a test-owned database (dropped and recreated), with its own backend on :8012 and dashboard on :3006 |
| `.github/workflows/ci.yml` | yes | Phase 3C CI: docs check; backend/SDK lint, types, migrations, tests (real PostgreSQL via compose); frontend audit, typecheck, lint, unit tests, build, bundle scan. Runs on GitHub on every push to `main` and on pull requests, not on a feature-branch push alone (first runs: `reports/phase-3-final.md` §6; first Phase 6 run, PR #1: `reports/phase-6-final.md` §5); `pip-audit` and Bandit run in job `security-python`, the history secret scan in `security-history`, Playwright E2E in `e2e` and the compose image build in `image-build`, on the R104 branch and draft PR #2 until it is merged (`reports/r104.md`) |
