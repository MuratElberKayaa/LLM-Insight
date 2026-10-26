# LLM Insight

A local, verifiable observability MVP for LLM applications: **Observe → Diagnose → Evaluate → Improve**.

An instrumented application sends **metadata-only** OpenTelemetry GenAI spans over real **OTLP/HTTP binary protobuf** to a FastAPI backend. The backend stores them idempotently in PostgreSQL, prices provider-reported usage with a versioned `Decimal` catalogue (unknown cost stays unknown, never `$0`), and a Next.js dashboard lets a person explore traces, read automatic diagnostics, record a human failure review, turn a reviewed trace into an approved regression test case, and compare two prompt versions in a **mock-only, SYNTHETIC** A/B experiment on a golden dataset.

> **Status: MVP, authorized for local use only.** Real-provider mode is disabled and has never been validated against a real provider. Every experiment result and all demo telemetry are SYNTHETIC. Estimated costs are not provider invoices. Public deployment is not authorized (`docs/SECURITY.md` §7). Evidence for every claim below is in `docs/reports/` (latest: `docs/reports/phase-5b.md`).

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quick start (host stack)](#quick-start-host-stack)
- [Alternative: compose `app` profile](#alternative-compose-app-profile)
- [Demo walkthrough](#demo-walkthrough)
- [Configuration](#configuration)
- [Security boundaries](#security-boundaries)
- [Running the tests](#running-the-tests)
- [Known limitations](#known-limitations)
- [Documentation map](#documentation-map)

## What it does

| Step | Capability | Where |
|---|---|---|
| **Observe** | `POST /v1/traces` OTLP/HTTP protobuf receiver (real `opentelemetry-proto` types and protobuf responses; gzip; size, span-count and rate limits); idempotent span and usage ledger; independent Python SDK (`llm-insight-sdk`) with a deterministic mock provider and an offline-tested OpenAI adapter | `backend/app/api/otel.py`, `sdk/` |
| | Overview KPIs (with `n`, p50/p95, error rate, known cost next to an unknown-cost count), Trace Explorer, span tree and waterfall, model comparison, project selector | dashboard `/`, `/traces`, `/models` |
| **Diagnose** | Evidence-based, read-only machine observations per trace, labelled "Automatic · Unverified · Not a human review" | `GET /api/v1/traces/{id}/diagnostics`, trace page |
| **Evaluate** | Human failure review (issue type, evidence, hypothesis, decision); a failure cause is recorded as a **hypothesis**, never as a fact | trace page |
| | Trace-to-testset: a reviewed trace becomes a test case only with explicit human approval; the input is typed by the person (telemetry never stored the prompt) and server-side redaction runs before insert | `/traces/{id}/reviews/{review_id}/test-case`, `/datasets` |
| | Deterministic rule engine (restricted JSON Schema subset, field rules, RE2-only regex), golden dataset of 12 SYNTHETIC cases | `backend/app/services/evaluation/` |
| **Improve** | Mock-only A/B experiment of two prompt versions on identical case ids; pass rate with its denominator, synthetic latency, estimated cost or "unknown"; a better version is named only on complete paired evidence | `/experiments`, `/experiments/new`, `/experiments/{id}` |

There is no LLM-as-judge, no semantic score and no real-provider experiment.

## Architecture

```text
 instrumented app (demo_app, mock / offline fake-OpenAI)
   └─ llm-insight-sdk  ── OTLP/HTTP protobuf, X-Project-Key ──┐
                                                               ▼
                         ┌────────────── FastAPI backend (127.0.0.1:8000, 1 worker) ───────────────┐
                         │ POST /v1/traces   auth → limits → decode → allowlist/redact → idempotent │
                         │                   trace/span/usage upsert → Decimal pricing → COMMIT     │
                         │ /api/v1/*         admin Bearer token; traces, metrics, diagnostics,      │
                         │                   reviews, datasets, test cases, mock experiments        │
                         └───────────────┬──────────────────────────────────────────────────────────┘
                                         │ SQLAlchemy + Alembic (migrations 0001–0005)
                                         ▼
                                PostgreSQL 17 (docker compose, 127.0.0.1:55432)
                                         ▲
   browser ── 127.0.0.1:3001 ── Next.js dashboard (server components + 5 Server Actions;
                                 the admin token stays on the server; Host/Origin/CSRF guard)
```

- **Backend** (`backend/`): Python 3.12, FastAPI modular monolith; one process, one worker (the rate limiter and run bounds are in-process).
- **SDK** (`sdk/`): independent package (never imports the backend); exports CLIENT inference spans with `gen_ai.*` attributes and provenance; fail-open when the backend is down.
- **Dashboard** (`frontend/`): Next.js 16 / React 19 / TypeScript / Tailwind / Recharts; every backend call is made server-side; no route handlers.
- **Contracts:** wire contract `docs/TELEMETRY_CONTRACT.md`, column-level DB and `/api/v1` contract `docs/ARCHITECTURE.md`, decisions `docs/ADR.md`.

## Requirements

Verified on Windows 11 with Git Bash (the commands below use Git Bash syntax; on Linux/macOS replace `.venv/Scripts/` with `.venv/bin/`). The CI runs the backend, SDK and frontend checks on Ubuntu 24.04.

| Tool | Version | Note |
|---|---|---|
| Python | 3.12 (3.12.6 used) | separate venvs for `backend/` and `sdk/`, from the lock files |
| Docker Desktop / Engine with Compose v2 | Engine 28.5.1 used | PostgreSQL 17; optional `app` profile |
| Node.js | 22.23.4 | installed project-locally by `scripts/dev/install_node.py` (SHA-256 checked); the global Node is not changed |
| Microsoft Edge | installed | only for the Playwright browser tests (no browser download) |
| Git | any recent | |

Free local ports: `55432` (PostgreSQL), `8000` (backend), `3001` (dashboard), `8100` (demo app). On Windows, keep the clone in a short directory path: the venvs exceed the 260-character limit under long paths (R73).

## Quick start (host stack)

This is the path used by the test suites and the demo. Everything stays on `127.0.0.1`, uses mock providers only and spends nothing.

**1. Secrets and PostgreSQL** (repository root):

```bash
python -I scripts/dev/init_env.py           # creates the git-ignored .env with generated secrets (never printed; never overwrites)
docker compose up -d --wait postgres        # PostgreSQL 17 on 127.0.0.1:55432
```

**2. Backend** (venv, migrations, project and ingest key, verified prices, server):

```bash
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.lock
backend/.venv/Scripts/python -m pip install --no-deps -e backend
cd backend
.venv/Scripts/alembic upgrade head                      # migrations to 0005 (head)
.venv/Scripts/python -m app.cli create-key --project demo   # prints project_id and the ingest key ONCE
.venv/Scripts/python -m app.cli seed-prices             # verified price catalogue (docs/PRICING.md)
.venv/Scripts/python -m app                             # http://127.0.0.1:8000 (keep it running)
cd ..
```

Put the printed key into `.env` as `OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>`. Only its HMAC is stored; it cannot be shown again (create a new one with `create-key`, revoke with `revoke-key --prefix <p>`). The first project has id `1`, the dashboard's default.

**3. SDK and synthetic demo traffic** (second terminal, repository root):

```bash
python -m venv sdk/.venv
sdk/.venv/Scripts/python -m pip install -r sdk/requirements-dev.lock
sdk/.venv/Scripts/python -m pip install --no-deps ./sdk
set -a; . ./.env; set +a                    # export the OTEL_* settings, including the ingest key
sdk/.venv/Scripts/python -m demo_app.web --drive   # 5 SYNTHETIC traces (success, multi-call, failure, retry, fake OpenAI); prints trace ids
```

**4. Dashboard** (repository root):

```bash
export PATH="$(python -I scripts/dev/install_node.py --print-bin):$PATH"   # installs Node 22.23.4 into .tools/ on first use
python -I scripts/dev/init_frontend_env.py --project-id 1                  # frontend/.env.local with the server-side token (not printed)
cd frontend
npm ci
npm run build && npm start                  # http://127.0.0.1:3001
```

**5. Golden dataset** (repository root, backend running):

```bash
backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project 1 --approved-by <your-name>
```

This loads the 12 SYNTHETIC golden cases (`golden-synthetic-v1`) through the real API. Add `--api http://127.0.0.1:<port>` if the backend is not on 8000.

**Stopping and resetting:** stop the backend and dashboard with Ctrl+C; `docker compose stop` keeps the data; `docker compose down -v` deletes the local database volume (then repeat from step 1's `docker compose up` and step 2's migration and key).

## Alternative: compose `app` profile

`docker compose --profile app up -d --build --wait` additionally builds and starts the backend and the dashboard as containers (dashboard on `http://127.0.0.1:3001`; the backend migrates to head on start). Run the init inside the container:

```bash
docker compose --profile app exec backend python -m app.cli create-key --project demo
docker compose --profile app exec backend python -m app.cli seed-prices
```

**`docker compose --profile app` does not by itself expose host OTLP ingestion.** The backend container shares one network namespace with the dashboard and binds `127.0.0.1` inside it; only the dashboard port is published (R43). To send telemetry from a host SDK or `demo_app`, run a host backend on the same database as in quick-start step 2 (`backend/.venv/Scripts/python -m app`, which reaches PostgreSQL on `127.0.0.1:55432`) and keep `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://127.0.0.1:8000/v1/traces`. The seed script also needs the host backend venv. The default `docker compose up` starts PostgreSQL only. Step-by-step clean-clone instructions, including relocated ports when the defaults are taken, are in `docs/DEMO_SCRIPT.md`.

## Demo walkthrough

A 3–5 minute script is in `docs/DEMO_SCRIPT.md`. In short, on `http://127.0.0.1:3001`:

1. **Overview**: traces, inference calls, error rate, p50/p95 with `n`, known cost plus the count of calls whose cost is unknown; the SYNTHETIC badge.
2. **Traces** → the failing trace (`POST /v1/summarize/failure`): root → child span tree, waterfall, usage, provenance, price version, and the machine observations panel (automatic, unverified).
3. **Failure review** on that trace: issue type, evidence, hypothesis, your (self-declared) name; then a decision (for example "hypothesis supported").
4. **Convert to a regression test case**: choose the `golden-synthetic-v1` dataset, type a synthetic input (to make the scripted mock answer it, use a golden input such as the G02 input from `backend/app/services/experiments/golden.py`), choose the rule, optionally an expected output, tick the approval box and save. Without the tick nothing is stored; e-mail, phone, long numbers and key-like values are masked.
5. **Experiments → New mock experiment**: dataset `golden-synthetic-v1`, `golden-prompt-v1` vs `golden-prompt-v2`, a priced model, tick the mock-only confirmation, run.
6. **Results**: SYNTHETIC banner, passed / failed / not evaluated per version with the pass-rate denominator, synthetic latency, estimated cost or "Total unknown", and every case (including your approved case, linked to its review and trace) under both versions. On the golden set no better version is named, because one case timed out under v2 (incomplete evidence).

## Configuration

All settings come from `.env` (template: `.env.example`, placeholders only; `init_env.py` fills the secrets). The dashboard reads `frontend/.env.local` (written by `init_frontend_env.py`). Neither file is committed.

| Setting | Default | Meaning |
|---|---|---|
| `LLMI_DATABASE_URL` | `postgresql+psycopg://llminsight:…@127.0.0.1:55432/llminsight` | backend database |
| `LLMI_BIND_HOST` / `LLMI_PORT` | `127.0.0.1` / `8000` | backend bind (loopback only) |
| `LLMI_KEY_PEPPER` | generated | HMAC pepper for ingest keys (rotating it invalidates every key) |
| `LLMI_ADMIN_TOKEN` (backend) = `LLMI_API_TOKEN` (dashboard server) | generated | Bearer token for `/api/v1/*`; never in a `NEXT_PUBLIC_*` variable, the browser or HTML |
| `LLMI_OTLP_MAX_BODY_BYTES` / `…_DECOMPRESSED_BYTES` / `…_SPANS_PER_REQUEST` | 4 MiB / 16 MiB / 2048 | ingest limits (`413` above them) |
| `LLMI_RATE_LIMIT_*` | ingest 20/s burst 40; API 10/s burst 20; auth failures 30/min | in-process token buckets (`429` + `Retry-After`) |
| `LLMI_ALLOW_REAL_LLM` | `false` | real experiments are refused; the backend has no real executor |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `…_HEADERS` | `http://127.0.0.1:8000/v1/traces` / `X-Project-Key=<key>` | SDK export target and ingest key |
| `LLMI_PROVIDER_MODE` | `mock` | demo app provider; `real` requires an OpenAI key and explicit approval and is not part of the MVP |
| `LLMI_DASHBOARD_PROJECT_ID` | `1` | default project of the dashboard (pages also accept `?project=<id>`) |

**Telemetry ingest from your own app:** install the SDK (see `sdk/README.md`), create a key with `create-key`, and set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_HEADERS=X-Project-Key=<key>` and `OTEL_SERVICE_NAME`. Any OTLP/HTTP **binary protobuf** exporter works within the supported subset (`docs/TELEMETRY_CONTRACT.md` §0); JSON and gRPC are not accepted. Prompts, completions and other content are never stored: non-allowlisted attributes are dropped and counted.

**API docs:** the backend serves its OpenAPI description on `http://127.0.0.1:8000/docs` (every `/api/v1` route needs the Bearer token); the contract is `docs/ARCHITECTURE.md` §5.

## Security boundaries

- **Local only.** Backend and dashboard bind `127.0.0.1`; the dashboard answers `421` to any non-loopback `Host`; compose publishes only on `127.0.0.1`.
- **No login.** The dashboard has no user accounts. Anyone who can reach the machine's loopback interface can read the dashboard and create reviews, datasets, test cases and mock experiments (R39, R54). Reviewer and approver names are self-declared labels, not authenticated identities (R55).
- **Credentials:** project ingest keys are stored as HMACs and compared in constant time; the admin token lives only in the backend and the dashboard server process.
- **Writes:** exactly five Server Actions, accepted only as same-origin POSTs (Origin, `Sec-Fetch-Site`, `Next-Action`) on their four form pages (UI entry points, not an authorization boundary: ADR-27), each calling one allowlisted backend write that re-validates everything.
- **Privacy:** metadata only, allowlist plus value checks at ingest; test-case and review text is redacted heuristically before insert. Heuristic redaction is **not** a privacy guarantee: test inputs must be synthetic.
- Public or multi-user deployment needs real authentication, TLS, per-project authorization, a distributed rate limiter, audit logging and a privacy review first (`docs/SECURITY.md` §7).

## Running the tests

Prerequisites: steps 1–2 of the quick start (PostgreSQL running, venvs installed). Integration tests create and drop their own disposable `*_test` databases.

```bash
cd backend && LLMI_TEST_REQUIRE_DB=1 .venv/Scripts/python -m pytest -q        # unit + PostgreSQL integration
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/alembic check
cd ../sdk && LLMI_TEST_REQUIRE_DB=1 .venv/Scripts/python -m pytest -q           # unit + live-backend tests
.venv/Scripts/ruff check . && .venv/Scripts/python -m mypy src && .venv/Scripts/python -m mypy --config-file pyproject.toml ../demo_app
cd ../frontend && npm run typecheck && npm run lint && npm test                 # strict tsc, ESLint (0 warnings), unit/component tests
npm run build && npm run check:bundle                                           # production build; no secret in the build output
npm run test:e2e                                                                # Playwright (Edge) against the live backend on :8000
cd .. && python -I scripts/verify_phase0.py --quiet                             # documentation consistency
```

The E2E suite needs the backend on `127.0.0.1:8000` with migrations at head and prices seeded, and free ports 3001–3004 and 8001; it creates and deletes its own run projects. Without `LLMI_TEST_REQUIRE_DB=1`, integration tests skip with a reason when PostgreSQL is unreachable. CI (`.github/workflows/ci.yml`) runs everything except the Playwright suite and the compose image build. The scenario-by-scenario status of the 48 acceptance tests is in `docs/TEST_MATRIX.md`.

## Known limitations

- **No real-provider validation.** The OpenAI adapter is tested offline only; no real OpenAI call has ever been made by this project; real mode is disabled; experiments are mock-only and SYNTHETIC (R62).
- **Costs are estimates** from provider-reported usage and a verified catalogue, not invoices; models without a verified price show `unknown` (R28, R29).
- **Best-effort telemetry:** no persistent queue; spans can be lost when the backend is down longer than the exporter's timeout, or on a crash (R26). Not a billing-grade system.
- **Per-process limits:** rate limits, experiment run bounds and counters live in the single backend process and reset on restart (R09, R23, R67).
- **Experiment runs are synchronous;** a lost response followed by a resubmission creates a second, independent run (no idempotency key) (R66).
- **Provenance is client-asserted** (R16); **redaction is heuristic** (R08, R56); **identities are self-declared** (R55).
- Local benchmark figures (`docs/reports/phase-5b.md` §6) describe one laptop, not production capacity.

Full risk register: `docs/SOURCES_AND_RISKS.md`.

## Documentation map

| Document | Content |
|---|---|
| `docs/PRD.md` | scope, P0/P1, non-goals |
| `docs/ADR.md` | binding architecture decisions |
| `docs/ARCHITECTURE.md` | data flow, DB columns, `/api/v1` contract, version pins |
| `docs/TELEMETRY_CONTRACT.md` | `/v1/traces` wire contract, attribute allowlist, usage identity, provenance |
| `docs/SECURITY.md` | privacy, credentials, limits, web guard, threat notes |
| `docs/METRICS_CATALOG.md` · `docs/PRICING.md` | metric definitions · verified price catalogue |
| `docs/TEST_MATRIX.md` | T01–T48 acceptance matrix with statuses |
| `docs/DEMO_SCRIPT.md` | demo script and clean-clone setup |
| `docs/PHASE_STATUS.md` · `docs/reports/` | phase gates and evidence reports |
| `sdk/README.md` · `frontend/README.md` | component details |
