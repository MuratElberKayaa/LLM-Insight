# Phase 1A — Delivery Report (Backend foundation)

- **Date:** 2026-10-24
- **Baseline:** Phase 0 closure commit `0929a14`. The `phase-0` tag (annotated, → `a16d64b`) was not moved.
- **Commit:** recorded in the Phase 1A Completion Report message; a commit cannot contain its own hash (`git log -1 --grep "Phase 1A"`). **No tag** is created for Phase 1A: the Phase 1 tag belongs to the full Phase 1 gate after 1B.
- **Scope executed:** Phase 1A only. Not implemented, as instructed: OTLP receiver (`/v1/traces`), ingest-key command, `/api/v1`, Python SDK, OpenAI integration, cost engine, frontend and evaluation. No paid API was called.

## 1. Created / changed files

| File | Change |
|---|---|
| `backend/pyproject.toml` | New. Exact runtime pins (fastapi 0.143.0, uvicorn 0.54.0, pydantic 2.14.0, pydantic-settings 2.15.0, sqlalchemy 2.1.4, alembic 1.20.0, psycopg[binary] 3.3.6) and dev pins (pytest 9.1.1, httpx 0.28.1, ruff 0.17.0). Pytest and Ruff config |
| `backend/requirements.lock` | New. Full transitive freeze (33 packages), all first uploaded to PyPI ≤ 2026-10-10 |
| `backend/app/main.py`, `app/__main__.py` | New. App factory with lifespan-managed engine; `python -m app` runs uvicorn on the configured loopback host, single worker |
| `backend/app/core/config.py` | New. Typed `LLMI_*` settings (pydantic-settings, `.env` at repo root). Rejects `CHANGE_ME`, secrets < 32 bytes, wrong driver and non-loopback bind. `SecretStr` everywhere; `hide_input_in_errors` |
| `backend/app/core/db.py` | New. Engine (psycopg 3, `pool_pre_ping`, `hide_parameters`, 2 s connect timeout), session factory, `ping()` |
| `backend/app/api/health.py` | New. `/health/live` and `/health/ready` per `ARCHITECTURE.md` §5 |
| `backend/app/models/{base,project,telemetry,usage}.py` | New. ORM for §4.2–4.6 with a naming convention and the §4.1 CHECK shorthands |
| `backend/alembic.ini`, `alembic/env.py`, `alembic/script.py.mako` | New. The URL never goes through `alembic.ini`; it comes from settings or, in tests, from memory |
| `backend/alembic/versions/0001_phase1_core_schema.py` | New. First migration: 5 tables, 53 CHECK, 4 FK, 6 UNIQUE, 6 indexes |
| `backend/tests/**` | New. 31 unit tests and 87 integration tests (real PostgreSQL, disposable `*_test` DBs) |
| `docker-compose.yml` | New. `postgres:17`, `127.0.0.1:${POSTGRES_HOST_PORT:-55432}:5432`, `pg_isready` health check, named volume |
| `scripts/dev/init_env.py` | New. Creates `.env` from `.env.example` with generated hex secrets. Uses `O_EXCL` and never overwrites. Prints key names only |
| `.env.example` | Changed. Added `POSTGRES_HOST_PORT=55432`; `LLMI_DATABASE_URL` port 5432 → 55432 (R19). Still placeholders only |
| `scripts/verify_phase0.py` | Changed. The rule "no `.env` exists" became "`.env` may exist only if git-ignored **and** untracked", and `.env` is excluded from the content scan (§6) |
| `docs/ARCHITECTURE.md` | Changed. Notes only: §4.13 migration sequencing for the `model_price_id` FK, §6 install record, §8 dev port. No column, type or rule changed |
| `docs/SOURCES_AND_RISKS.md` | R18 resolved; new R19 (port conflicts) and R20 (TestClient deprecation) |
| `docs/PHASE_STATUS.md` | Phase 1 split into 1A (delivered) and 1B (not started); commands table |
| `docs/reports/phase-1a.md` | This file |
| `.env` (local, **not committed**) | Created by `init_env.py`; git-ignored (`.gitignore:2`), untracked |

## 2. Locked ADRs / contracts affected

- **No ADR or contract changed.** ADR-01…21 and CR-01…09 are untouched. Every column, type, nullability, default and check in §4.2–4.6 is implemented as written, and a test parses the doc to prove it (§3).
- **Contract gap resolved by sequencing (needs your review):** §4.6 declares `usage_records.model_price_id` with an FK to `model_prices` (§4.7), but §4.13 places §4.7 in Phase 2. An FK to a table that does not exist yet is impossible. Migration `0001` therefore creates the column **without** the FK, and the Phase 2 migration adds `FK model_prices(id) ON DELETE RESTRICT` together with §4.7. This is documented in `ARCHITECTURE.md` §4.13. Until then no `computed` cost can exist, because there are no prices. If you prefer creating `model_prices` already in Phase 1, it is a one-migration change.
- **Dev port:** PostgreSQL is published on `127.0.0.1:55432` rather than 5432, because a native PostgreSQL service on this machine already holds `0.0.0.0:5432` (R19). It stays loopback-only, as §8 requires.

## 3. What works, confirmed by commands

- Isolated repository root, `main`, clean tree at `0929a14` before work.
- Docker Engine 28.5.1 is running. `postgres:17` resolved to PostgreSQL 17.11 (digest `sha256:b8faeff7…08fe`) and is `healthy`, published only on `127.0.0.1:55432`.
- The Alembic `0001` cycle upgrade → downgrade → upgrade passes on disposable databases, both through the CLI and in pytest. `alembic check` reports no drift between models and DB.
- The schema matches the doc column for column. `test_schema_matches_architecture_contract` parses `ARCHITECTURE.md` §4.2–4.6 and checks the column set and order, types (BIGSERIAL = `bigint` + `nextval`, `NUMERIC(20,10)`, `TIMESTAMPTZ`, `JSONB`), nullability, defaults and the generated `duration_ns`.
- FKs, UNIQUEs and indexes are asserted from `pg_constraint` and `pg_indexes`, including `started_at DESC` and the partial `WHERE (call_id IS NOT NULL)`.
- The real PostgreSQL rejects 72 constraint-violation cases, each asserted by its constraint name. Examples:
  - `cost_status=unknown` with `cost_usd=0` (unknown is never 0)
  - computed cost without a price version
  - upper-case or all-zero trace and span IDs
  - `parent_span_id = span_id`
  - non-object JSONB
  - `mixed` provenance on spans or usage
  - a duplicate canonical call key, and a second usage row for the same span
- Valid-data behaviour, also against the real PostgreSQL:
  - exact Decimal round trip
  - cascade delete from `projects`
  - storable orphan spans
  - the same trace and span IDs accepted in another project
  - a `2^63-1` ns timestamp fits
- `/health/ready` reflects the actual DB state: `200` with the DB up, `503` at the 1 s bound with the DB stopped, `200` again after restart. `/health/live` stays `200` with no DB access.

## 4. Commands and exact outcomes (final gate run, 2026-10-24)

| # | Command | Outcome |
|---|---|---|
| 1 | `git rev-parse --show-toplevel` | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi`, exit 0 |
| 2 | `git branch --show-current` / `git rev-parse --short HEAD` / `phase-0^{commit}` | `main` / `0929a14` / `a16d64b` (tag unchanged) |
| 3 | `docker info --format …` | `server=28.5.1 os=Docker Desktop`, exit 0 |
| 4 | `python -I scripts/verify_phase0.py --quiet` | `SUMMARY: 660 PASS, 0 FAIL`, exit 0 |
| 5 | `docker compose config --quiet` | exit 0. `--no-interpolate` is used for display so that no secret is printed |
| 6 | `docker inspect … llminsight-postgres-1` / `pg_isready` | `health=healthy`; `accepting connections`, exit 0 |
| 7 | `alembic upgrade head` → `downgrade base` → `upgrade head` (CLI, disposable `llminsight_gate_test`) | exit 0 each. Tables: 5 + `alembic_version` → only `alembic_version` → 5 + `alembic_version`; `current`: `0001 (head)` → empty → `0001 (head)` |
| 8 | `alembic check` | `No new upgrade operations detected.`, exit 0 |
| 9 | `python -m pytest tests/unit` | **31 passed**, 1 warning, exit 0 |
| 10 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration` | **87 passed**, 0 skipped, 1 warning, exit 0 |
| 11 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest` (full) | **118 passed, 0 failed, 0 skipped**, 1 warning, exit 0 |
| 12 | Integration tests with the DB unreachable (no `REQUIRE_DB`) | 87 skipped with reason `PostgreSQL unavailable: OperationalError`, exit 0. With `LLMI_TEST_REQUIRE_DB=1`: 1 failed + 86 errors, exit 1 (as designed) |
| 13 | `ruff check .` / `ruff format --check .` | `All checks passed!` / `24 files already formatted`, exit 0 |
| 14 | `pip check` | `No broken requirements found.`, exit 0 |
| 15 | `git diff --check` | exit 0. Only `LF will be replaced by CRLF` notices from the system `core.autocrlf=true`; the index keeps LF |
| 16 | `git status` (before commit) | 2 modified Phase 0 files, Phase 1A files untracked; `.env` not listed (ignored) |

The one warning is `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead` (R20).

**Failures found and fixed during the phase (root causes, not assertions):**
- **Secret echo in config errors.** A pydantic `ValidationError` printed the rejected pepper value (`input_value=…`). Fixed with `hide_input_in_errors=True`; the test is parametrized over pepper, token and URL.
- **FastAPI 0.143 route objects.** `app.routes` now contains `_IncludedRouter` objects. The test was rewritten to use the OpenAPI paths and a real `POST /v1/traces` → `404`.
- **Teardown error on the skip path.** The test teardown tried to reach an unreachable server to drop DBs it never created. It now drops only the databases it created.
- **Lint configuration.** Ruff treated `alembic` as first-party because of `backend/alembic/`, so `known-third-party` is now set.
- **Transitive pins.** `starlette` 1.10.0 (uploaded 2026-10-24), `certifi` 2026.10.23 and `pluggy` 1.7.0 violated the two-week rule. They were downgraded to 1.7.0, 2026.7.22 and 1.6.0, and `pip check` is clean.

### Test matrix relation (status column unchanged)

`TEST_MATRIX.md` stays **NOT RUN** for every ID. Phase 1A covers only parts of some tests, and their Phase 1 gate is after 1B. Partial evidence from this phase:

| Test | Partial evidence |
|---|---|
| T31 | Migration cycle plus schema = contract, for §4.2–4.6 |
| T30 | `/health/ready` `503` when the DB is down |
| T26 | ns `BIGINT` storage up to `2^63-1` |
| T27 | Orphan span rows are storable |
| T28 | Project-scoped uniqueness |

## 5. Manual test results

Server: `python -m app`. All requests below used `curl` against `127.0.0.1:8000`.

| Request | Result |
|---|---|
| `GET /health/live` | `{"status":"live"}` HTTP 200 (2 ms) |
| `GET /health/ready`, DB up | `{"status":"ready"}` HTTP 200 (29 ms) |
| `GET /health/ready` after `docker compose stop postgres` | `{"status":"not_ready"}` HTTP 503 (0.998 s) |
| `GET /health/live`, DB stopped | HTTP 200 |
| `GET /health/ready` after `docker compose start postgres` | HTTP 200 (14 ms) |
| `POST /v1/traces` | HTTP 404 (not implemented in 1A) |

Other checks:
- `netstat` shows the listener only on `127.0.0.1:8000`.
- The server log contains only access lines and `readiness check failed: TimeoutError`.
- A script compared the log against the generated `POSTGRES_PASSWORD`, `LLMI_KEY_PEPPER` and `LLMI_ADMIN_TOKEN`: none is present.

There are no screenshots, because there is no UI in this phase.

## 6. Security and cost

- **Secrets:**
  - `.env` is generated locally with `secrets.token_hex` and is never printed. It is git-ignored and untracked, which `verify_phase0.py` now asserts. Mutation test on a scratch copy: an un-ignored or a tracked `.env` → FAIL, exit 1.
  - `.env.example` holds placeholders only.
  - `docker compose config` was never printed with interpolation.
  - Alembic and the engine never log the URL (`hide_parameters=True`; no URL in `alembic.ini`).
- **Local-only posture:**
  - The backend refuses a non-loopback `LLMI_BIND_HOST`.
  - PostgreSQL is published on `127.0.0.1` only.
  - The pre-existing native PostgreSQL on `0.0.0.0:5432` and the `flowise` container on `0.0.0.0:3000` belong to the user and were not touched (R19).
- **Readiness responses** carry no driver message, and the log records only the exception class.
- **Cost:** no paid API calls; the infrastructure is local Docker only. Spend: $0.
- **Not yet in place (Phase 1B):** ingest key hashing and verification, request limits, rate limiting, `/api/v1` bearer auth. The typed settings for these exist and are validated but unused.

## 7. Deviations and ADR proposals

- No ADR proposal is needed. The `model_price_id` FK sequencing (§2) is a migration-order clarification with no column change, documented in `ARCHITECTURE.md` §4.13. **Please confirm or reject it.**
- The `scripts/verify_phase0.py` rule change keeps the Phase 0 intent (no committable secrets) and is stricter about tracking. It was needed because Phase 1A must create `.env`.
- Docker Compose runs only PostgreSQL. The backend runs from the venv. A backend container comes with the clean-clone smoke (T47, Phase 5) or earlier if you ask.

## 8. Open issues

| Priority | Issue |
|---|---|
| Medium | Your decision on the `model_price_id` FK sequencing (§2) |
| Medium | R19: port 3000 is taken by `flowise`; Phase 3 needs a free port or that container stopped |
| Low | R20: Starlette `TestClient` deprecation with `httpx`; re-check at the next pin review |
| Low | `opentelemetry-api` 1.45.1 is pulled in transitively by `fastapi`. It is the same version as the planned OTel pin, so there is no conflict expected in 1B |
| Info | The system git `core.autocrlf=true` produces CRLF notices. The global config is intentionally unchanged |

## 9. Phase 1B prerequisite checklist

- [ ] The user reviews Phase 1A (this report and the commit)
- [ ] Decision on the `model_price_id` FK sequencing
- [ ] Approval to install the OTel/protobuf pins from `ARCHITECTURE.md` §6 (`opentelemetry-proto`, `protobuf`, `googleapis-common-protos`, and `opentelemetry-sdk` plus `-exporter-otlp-proto-http` for the T01-S3 smoke), re-checked at install time
- [x] PostgreSQL container healthy and migration `0001` applied to the dev DB (`alembic current` = `0001 (head)`)

## 10. Verdict

**Phase 1A: PASS, pending user review.** The verdict rests on the final gate run in §4: 118/118 tests passed with 0 skipped while a real DB was required, Ruff is clean, the doc check shows 660/0, the migration cycle and `alembic check` exit 0, and the compose health status is `healthy`. **Phase 1B has not started.**
