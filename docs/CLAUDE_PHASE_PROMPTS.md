# LLM Insight — Claude Code'a Gönderilecek Faz Talimatları

**MVP hedefi:** 24 Ekim–7 Kasım 2026, fakat **takvime değil kabul kriterlerine göre ilerle**. Bu belgeyi VS Code çalışma klasörüne `docs/CLAUDE_PHASE_PROMPTS.md` olarak kopyalayabilirsin. Ana kaynak dosyası: `LLM_Insight_MVP_Nihai_Mimari_ve_2_Haftalik_Plan.md` (repo içinde önerilen: `docs/MVP_MASTER_PLAN.md`).

## 0) Bootstrap: bir defa uygula

```text
ROLE: Senior Python/AI Infrastructure Engineer + Test Engineer.

Project: LLM Insight — Observability & Evaluation Platform.
The project has a LOCKED MVP architecture. Read docs/MVP_MASTER_PLAN.md (focus ADR, data flow, phase exit criteria), CLAUDE.md and docs/PHASE_STATUS.md if existing. Do NOT read the entire Perplexity report or transcript into every turn. Use source material only if a specific technical decision requires verification.

IMPORTANT STARTUP BEHAVIOR:
1. Inspect the actual repository tree, git status, .env.example and package manifests first; distinguish existing working code from planned code. If directory is empty, say so.
2. Produce a short baseline inventory: EXISTS / MISSING / BROKEN / UNKNOWN.
3. Adopt architecture: modular FastAPI monolith, separate Python OTel SDK, PostgreSQL/SQLAlchemy/Alembic, real OTLP/HTTP protobuf /v1/traces, Next.js UI, OpenAI demo, deterministic evaluation. No ClickHouse, Redis, S3, Kafka, mandatory Collector, second LLM provider, LLM-as-a-judge or complex auth in MVP.
4. Be strict about security: no prompt/response capture by default, allowlisted attributes, hashed project API keys, no secret leakage; local-only deployment until proper auth exists.
5. For each phase do ONLY its stated work, run actual tests, write docs/reports/phase-N.md, update docs/PHASE_STATUS.md, stop. Never claim PASS if not run.
6. Never delete/overwrite user work or modify ADR/contract silently. Request approval for paid API runs, external cloud deployment, insecure permission bypass or destructive operations.
7. Present a concise plan BEFORE implementation. Do not automatically proceed past the current phase.

FIRST TASK NOW: Phase 0 ONLY. Do NOT implement Phase 1 yet.
```

## 1) Phase 0 — Scope, repo audit, ADR & contracts

```text
Execute PHASE 0 ONLY for LLM Insight. Read CLAUDE.md + docs/MVP_MASTER_PLAN.md; inspect actual repository state, then deliver a reproducible architecture definition package.

CREATE/UPDATE:
- docs/PRD.md: problem, target users, demo narrative Observe→Diagnose→Evaluate→Improve, explicit P0/P1/P2 scope, non-goals.
- docs/ADR.md: confirm ADR-01 to ADR-12; if source conflict, write a proposed ADR, do not silently change an accepted decision.
- docs/ARCHITECTURE.md: diagram and sequence from demo app to SDK→OTLP protobuf→FastAPI→Postgres→dashboard→evaluation.
- docs/TELEMETRY_CONTRACT.md: OTLP protobuf /v1/traces, ExportTraceServiceRequest/Response, Content-Type, gzip, status errors, trace/span IDs, gen_ai mapping/version, allowlisted attributes, project auth and schema contract.
- docs/METRICS_CATALOG.md: definitions, source, calculation, null/provenance rules, priority P0/P1/P2 (not all 64 must be implemented).
- docs/SECURITY.md: metadata-only capture, redaction, hashed key, body limits, local demo posture, threat notes.
- docs/TEST_MATRIX.md: T01–T48 from master plan, indicate blocking cases.
- docs/PHASE_STATUS.md: actual state, unchecked acceptance gates, test commands.
- CLAUDE.md: keep concise (~60 lines maximum), reference docs rather than pasting them.
- .env.example (no real keys).
- Minimum pyproject/package constraints if needed ONLY to pin implementation direction; no backend feature construction yet.

CONTRACT DECISIONS:
- Direct native OTLP/HTTP protobuf reception, not custom JSON format or fake OTLP.
- Postgres only, no unnecessary infrastructure.
- One provider OpenAI, provider-agnostic schema.
- Real vs synthetic provenance not conflated.
- Quality evaluation deterministic/golden, human-approved testcases.

QUALITY CHECK:
- Cross-check official OTel OTLP and GenAI client inference documentation (exact version and date) for all field names; note GenAI status and maintain adapter.
- Highlight unknowns, no invented working code or claimed tests.

STOP CONDITION:
Docs and locked contracts exist and are internally consistent. Present file list, ADR inconsistencies, Phase 1 prerequisites, and real command outputs only. STOP; wait for Phase 1 instruction.
```

## 2) Phase 1 — FastAPI + Postgres + gerçek OTLP ingestion

```text
Execute PHASE 1 ONLY. Review docs/PRD.md, ADR.md, TELEMETRY_CONTRACT.md, TEST_MATRIX.md, PHASE_STATUS.md. Preserve prior accepted contracts and existing user code.

IMPLEMENT:
1. Backend FastAPI, typed Pydantic config, SQLAlchemy/Alembic, PostgreSQL Docker Compose, health/live/ready, .env.example.
2. Project identity and project API key creation/init, salted strong hash or secure hash, constant-time verification and revocation; X-Project-Key read from header; no secrets in logs.
3. Standard OTLP/HTTP protobuf /v1/traces: parse opentelemetry-proto ExportTraceServiceRequest (ResourceSpans → ScopeSpans → Spans), preserve binary IDs as canonical hex, timestamps ns, parent-child/status/kind/resources, allowlisted gen_ai attrs. Support protobuf success response and Content-Type. Bound request size, span count and duration. If adding gzip support, bound decompressed size.
4. idempotent project-scoped trace/span UPSERT; rollback on invalid batch, no duplicate IDs. Migration, indexes and repository tests. Never store raw prompt, completion, auth headers or PII attributes.
5. API query stub GET /api/v1/traces /api/v1/traces/{trace_id} with pagination and proper project isolation. Keep initial query minimal for next phase.
6. Clear telemetry ingestion limitations / error-status semantics; /health excluded from tracing to avoid recursion.

TEST:
- T01–T08, T25/T27/T28/T30/T31, multiple resourceSpans, duplicate retry, invalid auth, corrupted protobuf, bad IDs and request upper limits.
- Docker health, migration up/down on disposable database, pytest integration with real Postgres. Build a standard OTel exporter smoke fixture if possible.

Do not build dashboard, real LLM SDK, ClickHouse, queues, hidden sample data. If external technical documentation differs from planned field names, record compatibility adapter notes and propose ADR rather than silent change.

STOP: actual POST from protobuf client successfully persists parent/child spans in Postgres, duplicate unchanged. Report commands, proof (test results, SQL counts), changed files, failures. Update phase status; STOP.
```

## 3) Phase 2 — Independent SDK, OpenAI demonstration, reliable cost

```text
Execute PHASE 2 ONLY after Phase 1 has confirmed tests passing.

IMPLEMENT:
1. Installable independent Python package (sdk/) configuring OpenTelemetry SDK Resource, tracer, BatchSpanProcessor, OTLP/HTTP protobuf exporter, exporter endpoint and project key from env. Add clean shutdown/flush; ensure bounded queue and short timeout, no synchronous network request in provider critical path. Fail open on exporter outages.
2. Trace nested root FastAPI request and child CLIENT-kind GenAI inference span. Provide a minimal OpenAI adapter/wrapper with requested/response model, input/output token, status/error, duration, optional provider response ID, prompt_version, application version, one physical attempt identity. SDK captures metadata only.
3. Add deterministic mock provider adapter for tests + clearly optional (manual) real OpenAI smoke. No paid model calls in normal pytest/CI.
4. Add usage_records (idempotent per canonical physical attempt), versioned model_prices with explicit source/checked date. Cost Decimal, cached input subset, reasoning output subset, unknown cost = NULL + status. No parent/inference double counting. Support retry attempt differentiation. Prefer provider-reported usage and state provenance.
5. Implement basic dashboard-agnostic aggregate query services: request count distinct, inference calls distinct, errors, p50/p95/p99, provider/model/price_version total, unknown count.
6. Log/drop behaviour for invalid/secret fields; failure when telemetry server down never crashes LLM workload.

TEST:
T09–T24 and T32, especially provider-reported usage, versioned price, no double count on replay, cached/reasoning math, timeout, telemetry outage, response.model vs request.model, trace context, error status. Validate one real smoke when key/budget available; otherwise mark NOT RUN and retain synthetic data label. Do not invent actual prices: seed only verified known model pricing with effective dates, mark unknown otherwise.

STOP: call → exported child span → Postgres usage → provable Decimal cost, non-blocking failure path. Report source and status of real smoke, actual tests and known limits; STOP.
```

## 4) Phase 3 — Connected Next.js dashboard and Trace Explorer

```text
Execute PHASE 3 ONLY after the ingestion+SDK integration is real and tested.

IMPLEMENT:
- FastAPI /api/v1/overview, /traces, /traces/{trace_id}, /models/compare with date/project/model/status filters, pagination, null handling, cohort sample counts.
- Next.js + TypeScript + Tailwind + Recharts frontend with server-side requests, no API token shipped to browser; mobile-readable but desktop-first observability dashboard.
- KPI: inference calls, computed/known cost with separate unknowns, p95 latency+N, error rate. Time trend chart, model comparison, trace listing, trace drilldown tree/span timeline, status and cost provenance badges/text.
- Empty state, loading, backend error state, no mock KPI constants, no fake/live conflation. Integrate actual POST trace and show it after refresh.
- Ensure inference/trace count and cost aggregator separation. UI should make unknown and synthetic obvious.

TEST:
- API fixtures, SQL aggregation correctness, frontend lint/typecheck/build, Playwright scripted interactions and UI screenshots. Cover T24–T32, T41/T42, T45/T46/T48.
- Prove dashboard data changes when DB changes; test direct trace link.

STOP: verifiable DB-backed overview and nested spans. Record actual E2E outcomes and screenshots paths. No Failure Review implementation yet; STOP.
```

## 5) Phase 4 — Failure Review, approved dataset, offline prompt experiments

```text
Execute PHASE 4 ONLY after Phase 3 is working on real trace data.

IMPLEMENT:
- Failure Review (trace-linked): issue_type from documented categories (retrieval_missing, chunk_boundary, prompt_instruction, generation_mismatch, schema_violation, unknown) + evidence and hypothesis; never automatically claim true root cause.
- Human-approved Trace-to-Testset: no save without approved=True + explicit sanitized input and expected result. If original trace had metadata only, ask developer/user to ENTER safe test content; do not fake recovering a prompt which was never stored.
- Dataset model, cases and deterministic rule engine (JSON Schema, expected field/value match, optionally exact-match/regex). No hidden LLM-judge.
- One small dataset (e.g. 10+ sanitized/synthetic scenarios), two prompt_version configurations. If running real LLM comparisons, require explicit budget, seeded controlled inputs, captured provenance and model. Mock/synthetic mode clearly labeled.
- Experiment outputs: sample count, pass/fail, p50/p95 (where sample permits), input/output cost and latency with missing-field indicators, prompt version tags. Existing evaluation metric values must not be inferred from trace latency.
- Demo path: failed trace → review → safe approval → dataset → rerun A/B → quality-cost comparison.

TEST:
- T33–T40, T48, unauthorized/duplicate review, no-approval guard, sanitized input, regression deterministic results, real/mock provenance.

STOP: one end-to-end reproducible improvement loop works. Record test results, screenshot/video rehearsal, known accuracy limitations. Freeze MVP feature set and STOP.
```

## 6) Phase 5 — Security, complete test matrix, freeze and delivery

```text
Execute PHASE 5 ONLY. Do not add major features. Treat docs/MVP_MASTER_PLAN.md and the six phase reports as source of truth.

CHECK:
- Clean clone/install/Docker Compose startup and migration; execute all 48 test scenarios (PASS, FAIL or NOT RUN/SKIP with reason); all critical tests PASS. Never turn off failing tests to claim success.
- Check .env and .gitignore, scans for accidental tokens, dependency/security audit, API rate and body limits, prompt/PII default off, project key RBAC boundary, injection/XSS test, dashboard server-only keys, SQL ORM params.
- Test ingestion idempotency, exporter downtime, retry storms within safe local fixture limits, one tracked real API smoke if explicitly authorized. No uncontrolled paid bulk load.
- Benchmark: explicitly report N, hardware, methodology, p50/p95, query limits, and known defects. Do not claim zero packet loss with no persistent queue.
- README quickstart, architecture graph, .env.example, API docs, test results, usage/provenance limitations, screenshots, DEMO_SCRIPT.md for 3–5 min.
- Prepare a short GitHub project pitch suitable for AI Engineering / AI Infrastructure portfolio.

HANDOFF:
- Create docs/FINAL_DELIVERY_REPORT.md with requirements PASS/FAIL/SKIP, commands and outputs, security findings, bugs, open risks, actual infrastructure cost and LLM model usage records.
- If blocking failure remains, prioritize repair and clearly label incomplete. DO NOT fabricate metrics, release claims or completed work.
STOP.
```

## 7) Her faz sonunda kullanılacak denetim promptu

```text
Act as an independent skeptical reviewer. Evaluate the current Phase N deliverables against docs/MVP_MASTER_PLAN.md, docs/TEST_MATRIX.md and docs/PHASE_STATUS.md.

Do not assume Claude's earlier success claims are true. Inspect actual changed files, API contracts, migrations and test output. Run only safe/relevant repeatable verification commands, not paid API calls without permission. Check for contract drift, simulated-vs-real telemetry labels, privacy violations, duplicate cost counting, missing provider usage, undefined error statuses, ineffective idempotency and unprotected admin endpoints.

Return:
1. Verifiable facts with commands, files and outputs
2. Blockers and severity
3. Corrections constrained to current phase
4. Recommendation PASS or BLOCKED, with explicit reasons
5. Whether Phase N+1 may safely begin.

Never change source files unless explicitly asked to fix findings.
```

## 8) Claude Code kullanım kısayolları

- `/model` uygun model seçimi; rutin kod için güncel verimli seçenek, karmaşık mimari denetim için güçlü seçenek. Fiyat/erişim hesabında farklı olabilir.
- `/clear` faz değişiminde, `/compact` büyük ama devam eden tek görevde, `/context` gereksiz sürekli yüklenen bağlamı incelemede.
- `/mcp` sadece gerekli MCP'lerin çalıştığını kontrol eder; ihtiyaç yoksa ek sunucu kurma.
- Playwright: test runner deterministik CI; görsel debug gerekiyorsa Microsoft Playwright MCP.
- Context7: yalnızca API/sürüm belirsizliğinde; çıktı içindeki kaynak belgesini doğrula.
- Özel Claude Code model adını promptlarda sabitleme; `/model` seçeneği ve güncel hesabın gerçeği.

**Çalışma protokolü:** Bir prompt ver, Phase N bitiş raporunu al, gerçek çıktıyı gözden geçir, düzeltme gerekirse aynı fazın denetim promptunu çalıştır, **ondan sonra** bir sonraki faz promptunu gönder.
