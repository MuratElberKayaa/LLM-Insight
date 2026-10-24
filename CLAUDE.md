# LLM Insight — Claude Code Project Rules

## Mission
Build a verifiable AI observability MVP: Observe → Diagnose → Evaluate → Improve.
Read `docs/MVP_MASTER_PLAN.md` for locked architecture, contracts and phase gates; read only the current phase instructions in `docs/CLAUDE_PHASE_PROMPTS.md`.

## Locked architecture
- Python/FastAPI modular monolith, independent Python OpenTelemetry SDK, PostgreSQL/SQLAlchemy/Alembic, Next.js/TypeScript/Tailwind/Recharts.
- Real OTLP/HTTP protobuf ingest `POST /v1/traces` (not proprietary JSON pretending to be OTLP). Use `opentelemetry-proto` types and proper protobuf response.
- One provider adapter: OpenAI. Support synthetic/mock fixtures explicitly labeled. No ClickHouse, Redis, S3, Kafka, Kubernetes or LLM-as-judge in MVP.
- OTel client inference semantics, span CLIENT kind, trace/span IDs, context propagation, provider-report usage first.
- Metadata-only default. No prompt/response/PII capture; allowlist, redaction, hashed project key.
- Idempotent spans and usage ledger, `unknown` cost must NOT become zero, pricing versioned with Decimal; no parent double count.
- Quality = deterministic golden-set checks + human-approved trace-to-testset; failure cause is a hypothesis, not a fact.
- Admin/dashboard is local-only unless strong authentication is explicitly designed and approved.

## Workflow
- Inspect real files and git status before declaring existing functionality.
- Do ONLY the current phase; stop at its acceptance gate. Do not auto-advance.
- Do not silently alter locked ADRs, overwrite user work, expose keys, install unverified MCP plugins, or start paid API runs.
- Tests first or alongside code; no fabricated outputs or PASS claims. Report exact commands and pass/fail/skip.
- Maintain `docs/PHASE_STATUS.md` and `docs/reports/phase-N.md` with changes, tests, risks, blocked items.
- Fix root causes, not failing assertions. Keep dependencies pinned and scope narrow.
- Read large reference docs selectively; do not paste whole sources into every prompt. Open official docs for uncertain external APIs.

## Security / cost
- `.env` and secrets never committed, `NEXT_PUBLIC_*` never used for backend secrets.
- Production/public deployment requires further auth and threat review.
- Mock mode by default in automated tests; explicit approval for real LLM spend and destructive commands.
- Handle telemetry backend outages gracefully (best effort), do not claim billing-grade reliability.

## Git safety
- This folder is its own repo. The home directory `C:/Users/PC` is a separate, unrelated repo (DailyDietAPP remote): never stage, commit, repair or modify it.
- Before any `git add`/`commit`, verify `git rev-parse --show-toplevel` is this project folder. Never change global git config.

## Contracts (read the one you need, not all)
- `docs/ADR.md`: locked ADR-01…12, accepted CR-01…09, proposed PADR-01…09 (not binding until approved).
- `docs/TELEMETRY_CONTRACT.md`: `/v1/traces` wire contract, attribute allowlist, usage/cost mapping.
- `docs/ARCHITECTURE.md`: DB tables/enums, `/api/v1` contract, version pins, layout per phase.
- `docs/SECURITY.md` · `docs/METRICS_CATALOG.md` · `docs/TEST_MATRIX.md` (T01–T48; 39 critical, 10 blockers).
- `docs/PRD.md` · `docs/DEMO_SCRIPT.md` · `docs/SOURCES_AND_RISKS.md` · `docs/fixtures/`.
