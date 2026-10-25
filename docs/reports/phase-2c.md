# Phase 2C — Metrics, Demo Integration and Security Gate

- **Date:** 2026-10-24 to 2026-10-25 (UTC; the machine's local clock is UTC+3).
- **Baseline:** Phase 2B commit `720fedc`, tags `phase-0`, `phase-1` (backend 1024 passed, SDK 319 passed, re-run before any edit).
- **Scope:** the mandatory security gate (test-output leaks, credential rotation, R32), re-verification of the price catalogue, aggregate metrics M01–M15 and their read API, the FastAPI root-span demo, system acceptance and documentation.
- **Not done (by instruction):** no real OpenAI request, no paid service, no frontend, no LLM-as-judge, no Optimization Lab, no schema change, no deployment, no new infrastructure, no `phase-2` tag, no Phase 3.
- **Commit:** one project-only commit on `main` (hash in the closure message). The home-directory repository, the global Git config, the Windows PostgreSQL service on port 5432 and other containers were not touched. No database and no Docker volume was dropped.
- All telemetry in this phase is **SYNTHETIC**. Catalogue prices are the verified official ones; every token count is made up.

## 1. Start-of-phase verification

| Check | Result |
|---|---|
| Repository | `git rev-parse --show-toplevel` = the project folder; `main`, clean tree at `720fedc` |
| PostgreSQL | `llminsight-postgres-1` `Up (healthy)` on `127.0.0.1:55432`; dev DB `alembic current` = `0002 (head)`, 0 rows in every table |
| Baseline suites | backend `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` → **1024 passed**; SDK the same → **319 passed** |
| Compose | `docker compose config --quiet` exit 0 |
| Contracts read | CLAUDE.md, MVP master plan (§E, §F, §H Phase 2, §I), phase prompts §3, ADR, ARCHITECTURE, METRICS_CATALOG, SECURITY, TELEMETRY_CONTRACT (§3–5), TEST_MATRIX, PHASE_STATUS, PRICING, `reports/phase-2a.md`, `reports/phase-2b.md` |

**ADR check:** no change conflicts with ADR-01…21 or CR-01…09, so nothing required a stop. No column, table or migration was added (ADR-21), and `llminsight.attrmap.v1` is unchanged (ADR-15). The two endpoints are the ones `ARCHITECTURE.md` §5 already lists; `/api/v1/models/compare` was scheduled for Phase 3, and its backend half now comes early because M15 needs it (the UI stays Phase 3).

**Interruption:** the session was interrupted once. On resumption the tree was inspected, Docker Desktop was found stopped and was started again (local application only), and the container came back healthy with the rotated credential and its data intact. Work resumed at the first unfinished task (the metrics API); no completed step was repeated except the final gates.

## 2. Task A — security gate

### 2.1 Leak paths found

The Phase 2B disclosure was confirmed and traced to its mechanism with a synthetic probe: pytest prints the **arguments of every traceback frame** (`funcargs`, always on), the **repr of values in assertion explanations**, and with `--showlocals` every local variable. The integration fixtures handed the database URL (with its password) around as a plain `str`, so any failure inside a helper taking `url` printed it.

| Path | Where | Status |
|---|---|---|
| Helper arguments (funcargs) | `disposable_db`, `migrated_url`, `ingest_url`, `fresh_url`; helpers in `test_migrations.py`, `test_model_prices.py`, `test_exporter_roundtrip.py`, `test_ingest_concurrency.py`, `tests/helpers.py` | Fixed: `RedactedUrl` |
| Assertion reprs | any `assert` over a URL | Fixed: `RedactedUrl` |
| `--showlocals` | e.g. `raw` in the backend `_server_url()` | Fixed: report hook |
| A dataclass repr | SDK `LiveBackend.conninfo` (libpq URL with the password) | Fixed: `field(repr=False)` + `RedactedUrl` |
| f-strings, `pytest.fail` messages, skip reasons | any test | Fixed: report hook |
| Captured stdout/stderr/log | any test | Fixed: report hook |
| Driver error messages | psycopg / SQLAlchemy on bad or rejected URLs | Checked with 5 synthetic cases: no password in any message; no change needed |
| App configuration | `Settings` (`SecretStr`, `hide_input_in_errors`), engine repr (`***`), `hide_parameters` | Already safe |
| Operator commands | `docker compose config` without `--quiet` prints `POSTGRES_PASSWORD` | Documented (`SECURITY.md` §2); every command here uses `--quiet` |

**Two layers** (`backend/tests/redaction.py`, an identical copy in `sdk/tests/redaction.py`; the SDK never imports the backend, so a test fails if the copies drift):
1. `RedactedUrl`, a `str` whose repr is `<redacted database url>`, returned by every credential-bearing URL fixture and helper in both suites.
2. Report hooks (`pytest_runtest_makereport`, `pytest_make_collect_report`) that scrub, in every report, each local secret (values of `*PASSWORD`, `*PEPPER`, `*TOKEN`, `*SECRET`, `*API_KEY`, `*DATABASE_URL`, `*_HEADERS` from `.env` and the environment, plus URL-encoded and header-value variants) and the password of any `scheme://user:password@` URL.
Limits: not effective for `pytest -s` or live logging (stated in the module and `SECURITY.md`).

**Regression tests (synthetic credentials only):**
- `backend/tests/unit/test_output_redaction.py` (10): a real inner pytest run fails on purpose through 7 paths (helper argument, assertion repr, `pytest.fail` message, `--showlocals`, captured stdout/stderr/log, skip reason, collection error). The synthetic password and token never appear, and both redaction placeholders do. A **control run** without the protections prints both values, which proves the probe detects a leak. Building-block tests cover `RedactedUrl`, `scrub` and `local_secrets`.
- `sdk/tests/unit/test_redaction.py` (3): the copy matches the backend module; the `LiveBackend` repr shows no credential; an SDK-suite failure is redacted.
- Observed for real during this phase: after the Docker interruption, a `LLMI_TEST_REQUIRE_DB=1` failure report printed `reason = 'OperationalError'` and no URL.

### 2.2 Exposure scan and credential rotation

**Scan before rotation** (scratch script; it prints paths and counts only): the current password occurred **0** times in the working tree (excluding `.env`), in the full Git history (`git log --all --reflog -p`) and in the index, and 0 times in pytest's temporary directories. It occurred twice in **one** local Claude Code session transcript of the Phase 2B session, outside the repository. That file was not modified (it is the user's data); after rotation the value it holds is a dead credential.

**Rotation** (scratch script; no secret was printed, logged or committed):
1. Guards: the URL points at `127.0.0.1:55432` (the compose port), its user and password equal `POSTGRES_USER` / `POSTGRES_PASSWORD`, and the server reports the container's Debian build. The Windows PostgreSQL on 5432 (another process) was never contacted.
2. A new 48-hex-character password (the `init_env.py` format) was generated and the new `.env` staged as `.env.rotating` (git-ignored, `O_EXCL`).
3. `ALTER ROLE llminsight WITH PASSWORD '<SCRAM-SHA-256 verifier>'`: the verifier was computed client-side, so the plaintext never reached the server (`log_statement` was `none` anyway).
4. A fresh login with the new password succeeded and one with the old password failed with `password authentication failed`, while the admin session stayed open for a revert (not needed).
5. `.env.rotating` atomically replaced `.env` (2 lines: `POSTGRES_PASSWORD`, `LLMI_DATABASE_URL`).
6. `docker compose up -d --wait postgres` recreated the container so its stored environment holds the new value, not the dead one. It used the **same named volume** `llminsight_pgdata` (`CreatedAt 2026-10-24T16:25:03Z`, unchanged), and `alembic current` stayed `0002`.

| Verification | Result |
|---|---|
| New login / old login | succeeds / rejected (`OperationalError`, authentication failure) |
| Container `POSTGRES_PASSWORD` equals `.env`; `.env` URL carries the same password | True / True |
| Scan for the **new** password | only in `.env`: 0 in the working tree, Git history, index, pytest temp, Claude Code transcripts and the scratch directory |
| `.env` | git-ignored (`.gitignore:2`) and untracked |
| `alembic current` / `alembic check` after rotation | `0002 (head)` / no new operations |
| Backend suite right after rotation | 1034 passed (the 1024 baseline plus the 10 redaction tests) |
| SDK suite after rotation and the R32 fix | 322 passed |
| After the Docker Desktop restart | login with `.env` works, data intact (the rotation persisted in the volume) |

### 2.3 R32 — the intermittent backend-log traceback: root cause found and fixed

**Reproduction.** A scratch probe started the real `python -m app` (SYNTHETIC settings, unreachable DB; only `/health/live` used) and opened 200 connections per pattern that the client aborted with RST (`SO_LINGER` 0). Before the fix, on Windows with uvicorn's default `ProactorEventLoop`:

| Pattern (200 connections, fresh server each) | Listener alive afterwards | Tracebacks in the backend log |
|---|---|---|
| reset after reading the response | yes | 0 |
| reset mid-request | **no** | 2 (`Accept failed on a socket`, WinError 64; `Task exception was never retrieved`) |
| reset with the response unread | **no** | 2 (same) |
| reset right after connect | **no** | 2 (same) |
| graceful close | yes | 0 |
| idle keep-alive closed by the server, then reset | yes | 0 |

**Root cause** (CPython 3.12.6 `asyncio/proactor_events.py`, `_start_serving`): when an IOCP accept fails because the client already reset the connection (WinError 64), the loop reports "Accept failed on a socket" with a traceback **and closes the listening socket**. The process keeps running but refuses every later connection. An in-process probe also once logged the second Proactor-only callback traceback (`ConnectionResetError` WinError 10054 from `_call_connection_lost`). The Phase 2B fragment (`asyncio/events.py`, `_run`, a loop callback) matches these Proactor paths. That attribution rests on the fragment: the full Phase 2B text was lost.

**Fix** (`backend/app/core/eventloop.py`): the backend always runs on asyncio's selector loop. `python -m app` passes `loop="app.core.eventloop:new_event_loop"` to uvicorn, and so does the integration test server. After the fix the same probe ran all six patterns × 200 with **0 tracebacks and the listener alive** every time. Limit: `select()` handles at most 512 sockets on Windows (R34; fine for the local MVP).

**Regression tests:**
- `backend/tests/integration/test_server_resilience.py` (4) drives the **real `python -m app` subprocess** (the race rarely triggers when client and server share one process; an in-process server survived 2000 resets in one trial). It runs 300 resets per pattern, then checks `/health/ready` is 200, ingest still writes, and the log has no `Accept failed` or `Traceback`.
- `backend/tests/unit/test_eventloop.py` (3): the factory builds a selector loop; uvicorn resolves the setting to it; `python -m app` passes it.
- **Mutation check:** with the `loop=` line removed from `app/__main__.py`, the resilience tests failed **4/4 in each of 3 runs**. They pass with it.

**Diagnostics:** the SDK live harness now checks the backend log of the **whole session** at teardown and fails, printing each 40-line traceback block (scrubbed), if any exists. Previously only two leak tests looked, and pytest's temporary directories rotate. No credential, user input or secret value is logged by this.

**R32 status: Resolved** (`SOURCES_AND_RISKS.md`), with the residual uncertainty above about the original Phase 2B text.

## 3. Task B — pricing catalogue verification

The six rows of `openai-2026-10-24` were re-checked at **2026-10-24T23:01Z** against the official pricing, model and deprecations pages. Each page was read through the page summarizer and also downloaded raw (2026-10-24T23:01:21Z–23:01:24Z) and grepped.

| Model / snapshot | Input | Cached input | Output | Summarizer | Raw model page | Raw pricing page |
|---|---|---|---|---|---|---|
| `gpt-5.4-mini` / `gpt-5.4-mini-2026-03-17` | 0.75 | 0.075 | 4.50 | match | match; only snapshot listed | first row 0.75 / 0.075 / 4.5 |
| `gpt-5-mini` / `gpt-5-mini-2025-08-07` | 0.25 | 0.025 | 2.00 | match | match; only snapshot (marked deprecated) | first row 0.25 / 0.025 / 2 |
| `gpt-4.1-mini` / `gpt-4.1-mini-2025-04-14` | 0.40 | 0.10 | 1.60 | match | match; only snapshot (marked deprecated) | first row 0.4 / 0.1 / 1.6 |

USD per 1M text tokens, Standard tier. The pages show `$` with no ISO code; `USD` is recorded because this is the US-dollar price list.

- **Tiers / combinations:** the page lists Standard, Batch, Flex, Fast (renamed from Priority on 2026-07-30) and Ultrafast; the other rows per model are those tiers (Batch/Flex at half price). The stored price applies only to `openai.response.service_tier = default` on `api.openai.com`. Regional (data-residency) endpoints carry a 10 % uplift for models released on or after 2026-03-05, and the GPT-5.4 mini page states it explicitly; FedRAMP also carries an uplift. Both stay `price_missing` (`PRICING.md` §3). No long-context or cache-write price exists for these models.
- **Lifecycle:** shutdown `gpt-5-mini` 2026-12-11 (replacement `gpt-5.6-terra`) and `gpt-4.1-mini` 2027-01-14 (replacement `gpt-6-luna`), both unchanged. The replacements are **not** catalogued, so their calls stay `price_missing`.
- **Verification time vs. historical validity:** `verified_at` = `effective_from` = 2026-10-24T21:40:00Z is when the evidence was first read. The 23:01Z re-check proves the same rates were still published then. Nothing is claimed for events before 21:40Z (they stay `price_missing`); no historical price was guessed.
- **Outcome:** every entry verified, so **nothing deactivated, re-dated or versioned**. R31 is further mitigated (the raw pricing HTML now confirms the rows independently).
- **Seed:** explicit, repeatable and idempotent (`python -m app.cli seed-prices [--dry-run]`; inserts missing rows, reports identical ones, refuses differing ones, never touches usage). Tested in `test_model_prices.py` (41) and `test_pricing_catalog.py` (6), 47 passed. Dev DB dry run: `would insert 6, unchanged 0 (catalogue openai-2026-10-24)`, exit 0. The dev database was **not** seeded (your decision, Phase 2B §10).
- **No repricing:** existing `unknown` rows are never repriced (unchanged code; T22 tests pass). Synthetic test prices use only `synthetic.*` providers with `https://example.invalid/synthetic` sources, so they cannot be mistaken for provider prices.

## 4. Tasks C–E — what was built

### 4.1 Aggregate metrics M01–M15 and the read API (Tasks C, D)

- `backend/app/repositories/metrics.py`: project-scoped, event-time (`[from, to)` on `started_at`) SQLAlchemy queries with bound parameters. Usage-level metrics join each usage row to its own span; trace-level metrics use `traces`, root spans and `EXISTS` for the model filter. `GROUP BY ROLLUP(provenance)` returns the per-provenance buckets **and** an exact PostgreSQL total in one statement. Bounded lists: error types ≤ 50, price versions ≤ 200, model groups ≤ `limit` (≤ 200), each with a truncation flag.
- `backend/app/services/metrics.py`: assembly with the catalog's null rules. Counts are true counts; unknown is `null` (never 0); money is a `Decimal` string; M12 is rounded once, half-even; latency is in ms with `n` and the low-sample flags; `cost_complete` is false whenever any unknown cost exists; `provenance_verified: false`; the provenance headline label; client TTFT reported as **unavailable** (not recorded); an hourly or daily UTC trend.
- `backend/app/api/overview.py`: `GET /api/v1/overview` (M01–M14 + trend) and `GET /api/v1/models/compare` (M15). Admin auth and the rate buckets run before any validation or query; one read-only snapshot per request; the §5.1 `404`; `422` names parameters without echoing values; `no-store`. Contract: `ARCHITECTURE.md` §5.2. Implementation choices for details the catalog left open: `METRICS_CATALOG.md` "Phase 2C implementation notes" (no formula changed).
- `app/api/traces.py`: the snapshot helper `_read` was renamed `read_snapshot` and is now shared; `app/main.py` registers the router.

**Data the MVP does not record** (reported, not fabricated): client time-to-first-chunk (`time_to_first_chunk.available = false`); OTLP metrics (ADR-04); server-side TTFT/TPOT.

### 4.2 FastAPI root-span demo (Task E)

`demo_app/web.py` is a FastAPI app whose middleware opens a **SERVER span** `POST {route}` per request with the SDK's tracer. Its attributes are metadata only (`http.request.method`, `http.route`, `http.response.status_code`, `url.scheme`, `llminsight.provenance=synthetic`), with no exception event and no status description. It honours an incoming W3C `traceparent`, and `/health` is untraced. The handlers call:

| Route | Inference spans (CLIENT children) |
|---|---|
| `POST /v1/summarize` | 1 mock success |
| `POST /v1/summarize/multi` | 2 async mock + 1 async OpenAI (offline) |
| `POST /v1/summarize/failure` | 1 mock 500, handled: the app answers 200 with a fallback |
| `POST /v1/summarize/retry` | 429 then success: 2 physical attempts, one `call_id` |
| `POST /v1/summarize/openai` | 1 OpenAI (offline) |

"OpenAI (offline)" is the real `openai` 3.28.0 client through the Phase 2B adapter, wired to an in-process `httpx2.MockTransport` that answers like `gpt-5.4-mini-2026-03-17` on the standard tier with SYNTHETIC usage (the T19 example). No request leaves the process; the key is a fake that is never checked. Provenance is `synthetic` throughout. Run with `sdk/.venv/Scripts/python -m demo_app.web [--drive]`.

**Dependencies:** `fastapi` 0.143.0 and `uvicorn` 0.54.0 (+ `starlette` 1.7.0, `click` 8.5.0, `annotated-doc` 0.0.5) were installed into `sdk/.venv` with `backend/requirements.lock` as the constraint file. They are byte-identical to the backend's already-approved pins, recorded in the SDK `dev` extra and `sdk/requirements-dev.lock` (`ARCHITECTURE.md` §6). `pip check` is clean.

## 5. Task F — acceptance: exact commands and results

Windows 11, Python 3.12.6, PostgreSQL 17.11 container. `LLMI_TEST_REQUIRE_DB=1` makes an unreachable database fail instead of skip. Final runs after all changes:

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (backend) | **1082 passed, 0 failed, 0 skipped, 1 warning (R20); 688 unit + 394 integration** | 0 |
| 2 | `ruff check .` / `ruff format --check .` (backend) | `All checks passed!` / `86 files already formatted` | 0 / 0 |
| 3 | `python -m pip check` (backend venv) | `No broken requirements found.` | 0 |
| 4 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs` (sdk, complete) | **343 passed, 0 failed, 0 skipped** | 0 |
| 5 | `python -m pytest tests/unit -q` / `LLMI_TEST_REQUIRE_DB=1 python -m pytest tests/integration -q` (sdk) | **314 passed** / **29 passed** (live backend) | 0 / 0 |
| 6 | `ruff check .` / `ruff format --check .` (sdk); the same with `--config pyproject.toml ../demo_app` | `All checks passed!` / `28 files already formatted`; `All checks passed!` / `3 files already formatted` | 0 |
| 7 | `python -m mypy src` (strict) / `python -m mypy --config-file pyproject.toml ../demo_app` | `Success: no issues found in 8 source files` / `… in 3 source files` | 0 / 0 |
| 8 | `python -m pip check` (sdk venv) | `No broken requirements found.` | 0 |
| 9 | `alembic current` / `alembic check` (backend, dev DB) | `0002 (head)` / `No new upgrade operations detected.` | 0 |
| 10 | Migration cycle `head → 0001 → base → head`, schema = contract, upgrade with data, refused downgrade (pytest, disposable DBs) | pass (in #1, `test_migrations.py`) | — |
| 11 | Official OTLP exporter 1.45.1 round trip and re-export (T01-S3) | pass (in #1, `test_exporter_roundtrip.py`) | — |
| 12 | `docker compose config --quiet`; `docker compose ps` | exit 0; `llminsight-postgres-1 Up (healthy)` | 0 |
| 13 | `python -m app.cli seed-prices --dry-run` (dev DB) | `would insert 6, unchanged 0 (catalogue openai-2026-10-24)`; still 0 price rows | 0 |
| 14 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 721 PASS, 0 FAIL` (each new `(phase-2c)` status is checked against this report) | 0 |
| 15 | `git diff --check` (root) | no whitespace errors (`core.autocrlf` notices only) | 0 |
| 16 | Mutation checks (scratch scripts; defect injected, tests run, file restored) | metrics **11/11** detected; event loop: 3/3 runs fail without the fix; redaction: the control run leaks | — |

**One intermittent failure found by the gate (fixed, not hidden).** A full backend run failed in `test_m15_groups_by_provider_models_and_prompt_version`: the random test provider name `synthetic.md47154886152` contained an 11-digit run, which the R08 phone/ID rule drops, so the provider became `unknown`. 12 random hex characters give such a run about 0.8 % of the time, and Phase 2B's `test_ingest_cost.py` had the same latent flake. Both now use `tests.helpers.letters()` (100,000 draws: 0 digit runs), recorded as R35. The same run also updated `tests/unit/test_health.py` for the two new routes, an intended change.

**New and changed tests:**
- backend `tests/integration/test_metrics_api.py` (38): a hand-computed dataset through real `POST /v1/traces` with 9 traces (8 in project A, 1 in project B) including a mixed one, an orphan-only trace, boundary traces at `from` and `to`, and a second project. It checks T32 counts, T48-S1 at the API, tokens and subsets, exact Decimal cost with the unknown counts, `NULL` cost in the DB, T24 on the dataset and on a 37-sample set, T29 for an empty project and an empty window, project isolation, the identical `404`, window boundaries, 7 filter cases, re-export idempotency, trend sums, M15 grouping and truncation, auth before validation, 8 validation cases without echo, the limit bounds, the API rate bucket (T43 for these endpoints) and no IDs, keys or content in responses.
- backend `tests/integration/test_metrics_plan.py` (3): EXPLAIN on 36,000 generated rows shows the window indexes and no sequential scan; the results are correct.
- backend `test_server_resilience.py` (4), `test_eventloop.py` (3), `test_output_redaction.py` (10); `tests/helpers.py` (`letters`), fixture changes in `tests/integration/conftest.py`, `test_health_db.py`, `test_model_prices.py`, `test_ingest_cost.py`, `test_health.py`.
- SDK `tests/unit/test_demo_web.py` (13): T10 per route (root SERVER, direct CLIENT children, same trace, time nesting); failure as an error child with a handled answer; recovered retry as 2 attempts of one call; OpenAI attributes and usage; `traceparent` continuation; no content, key, completion or error text in the raw OTLP bodies; no exception events or status messages; a traced 422 without the input; untraced `/health`; telemetry outage (every route 200 within 2 s, spans counted as failed). The network is blocked in every test.
- SDK `tests/integration/test_demo_web_live.py` (8): the live round trip; `test_redaction.py` (3); `tests/integration/conftest.py` (redacted URLs, session traceback check).

## 6. Test matrix

| ID | Status | Evidence | Still missing |
|---|---|---|---|
| T10 | **PASS (phase-2c)** | The FastAPI request span is the SERVER root and every inference span is its direct CLIENT child in the same trace, for all five routes, sync and async (unit, OTLP stub). Live: one trace per request in PostgreSQL, parent = the request span, roles `root`/`child` in the trace API, no usage row for the request span. An incoming `traceparent` is continued | UI tree in Phase 3 (T46) |
| T24 | **PASS (phase-2c)** | `percentile_cont` p50/p95/p99 equal the hand-computed values and an exact `Fraction` implementation of NumPy's documented `linear` method (Hyndman–Fan type 7), on the dataset (n = 8, n = 5 roots) and on 37 irregular samples; `n` and the low-sample flags returned; `percentile_disc` mutation detected | **Deviation for review:** NumPy itself is not installed (not an approved pin). Adding it as a dev-only pin and comparing directly is possible on request. Re-verified in Phase 3 |
| T29 | **PASS (phase-2c)** | Empty project and empty window: `200`; counts 0; every rate, percentile, token sum, known cost and `cost_complete` is `null`; empty lists; empty trend buckets have zero counts and `null` cost | UI in Phase 3 |
| T32 | **PASS (phase-2c)** | M01 = 6 traces vs M02 = 8 physical attempts vs M03 = 6 logical calls (a retry counted as 2 attempts, 1 call); M04 = 1 failed / 8 with `by_error_type`; M05 = 1 / 6, labelled separately; boundary, other-project and re-export rows excluded; live demo traffic: 5 traces, 8 attempts, 7 calls, 2 failed | UI in Phase 3 |
| T48 | PARTIAL (phase-2c) | T48-S1 at the aggregate API: the mixed trace is not counted as a real trace (`provenance=real` → 0 traces) while its real usage row counts as 1 real attempt and its cost as real; `mixed` and `unknown` buckets are always separate; headline label "all provenance" | Dashboard badges (Phase 3), E2E |

Unchanged and re-exercised: T13/T14 (`PASS (phase-2a)`: fail-open and per-attempt rows, also through the demo), T17–T23 (`PASS (phase-2b)`: costs in the demo round trip), T28 (`PASS (phase-1c)`: the new endpoints share its `404`), T43 (stays `PARTIAL (phase-1c)`; the new endpoints are covered by the API bucket, but T43 is owned by Phase 3→5). **Not run:** the real smoke run (no approval, no key).

**Remaining critical tests not yet PASS (12 of 42; counted from `TEST_MATRIX.md`):** NOT RUN: T33, T34, T35, T36, T37, T39 (Phase 4), T41, T42, T45, T46 (Phase 3); PARTIAL: T43, T48. **Non-critical NOT RUN:** T38, T40 (Phase 4), T47 (Phase 5). The other 30 critical tests have a PASS status.

## 7. Requirement checklist

| Task | Requirement | Evidence |
|---|---|---|
| A | Every leak path identified; unsafe traceback, assertion and logging paths fixed; regression tests with synthetic credentials; failing tests verified not to expose secrets | §2.1 |
| A | Project credential rotated safely; `.env` updated without printing; role and app verified; port 5432 untouched; nothing dropped; backend, Alembic and integration tests after rotation | §2.2 |
| A | R32 reviewed; diagnostics improved without logging secrets; resolution claimed only with evidence | §2.3 |
| B | Names, snapshots, standard and cached prices, currency, unit, tier, UTC dates, combinations; verification time vs validity; no guessed history; explicit, repeatable, idempotent seed; no automatic repricing; unverifiable entries not activated (none); no synthetic price resembling an official one | §3 |
| C | M01–M15 as defined; isolation; UTC event-time windows; validated, consistent boundaries; grouping; attempts vs calls vs traces; success/failure; no token double count; Decimal; unknown cost; percentiles; TTFT unavailable; provenance and synthetic labels; retries as physical calls; indexed, bounded queries | §4.1, §5, §6 |
| D | Auth before work; local admin access; validated filters; no cross-project leakage; bounded results; stable schemas; Decimal as strings; no telemetry content | §4.1, `ARCHITECTURE.md` §5.2 |
| E | Root plus children through the SDK; mock and offline OpenAI only; parent/child and IDs; success, failure, recovered retry; synthetic; usage with computed and unknown cost; no content or credentials; telemetry failure harmless; ingest and query round trip | §4.2, §5, §6 |
| F | All listed gates with real outputs | §5 |
| G | Status, matrix, risks, contracts, this report | §8 |

## 8. Files

**New:** `backend/app/core/eventloop.py`, `backend/app/repositories/metrics.py`, `backend/app/services/metrics.py`, `backend/app/api/overview.py`, `backend/tests/redaction.py`, `backend/tests/unit/test_output_redaction.py`, `backend/tests/unit/test_eventloop.py`, `backend/tests/integration/test_server_resilience.py`, `backend/tests/integration/test_metrics_api.py`, `backend/tests/integration/test_metrics_plan.py`, `demo_app/web.py`, `sdk/tests/redaction.py`, `sdk/tests/unit/test_redaction.py`, `sdk/tests/unit/test_demo_web.py`, `sdk/tests/integration/test_demo_web_live.py`, `docs/reports/phase-2c.md`.

**Changed:** `backend/app/__main__.py` (loop), `backend/app/main.py` (router), `backend/app/api/traces.py` (`read_snapshot`), the backend test files listed in §5, `sdk/tests/conftest.py`, `sdk/tests/integration/conftest.py`, `sdk/pyproject.toml`, `sdk/requirements-dev.lock`, `sdk/README.md`; docs `PHASE_STATUS.md`, `TEST_MATRIX.md`, `SOURCES_AND_RISKS.md` (Phase 2C sources; R31, R32; new R33–R35), `ARCHITECTURE.md` (§5 table, §5.2, §6, §7, §8), `METRICS_CATALOG.md` (implementation notes), `PRICING.md` (re-verification), `SECURITY.md` (test output, rotation), `DEMO_SCRIPT.md`. Not committed: `.env` (rotated, git-ignored).

## 9. ADR compliance

- **ADR-01:** the SDK still never imports the backend; the redaction module is duplicated (drift-tested), not shared. The demo lives outside the SDK package.
- **ADR-02 / ADR-21:** no schema change; the metrics read existing columns and indexes only.
- **ADR-03 / ADR-04:** the demo exports over the unchanged OTLP/HTTP protobuf path; metrics are computed from spans and the ledger, no OTLP metrics.
- **ADR-05:** OpenAI stays the only adapter; the demo uses it offline.
- **ADR-06 / ADR-09:** fail-open demo (outage test); best effort only.
- **ADR-07 / ADR-19:** metadata only; no content, key or error text in OTLP, the DB, the APIs or the logs (tested).
- **ADR-08:** loopback only; the admin token guards the new endpoints.
- **ADR-10:** nothing about quality or causes is asserted.
- **ADR-11:** unknown cost is `NULL` in the DB and `null` in the API, never 0, and is always shown next to the known cost.
- **CR-06 / CR-08:** same server-side token and in-process buckets. **CR-09:** no new version; pins recorded.

## 10. Risks and limitations

- **R33** (resolved): the credential exposure. The dead old password remains in one local Claude Code transcript (outside the repository, not modified). The redaction layers do not cover `pytest -s`.
- **R32** (resolved, residual attribution uncertainty) and **R34** (accepted): the selector loop limits Windows to 512 sockets; local single-user use only.
- **R35** (resolved): the random-identifier flake.
- **R28 / R29 / R31:** the catalogue is correct as of 2026-10-24T23:01Z only; coverage is narrow; re-verify before any cost-relevant demo, ideally with a human look at the pricing page.
- **Metrics:** provenance is client-asserted (`provenance_verified: false`, R16); a recovered retry marks its trace `error` (R27); rates are rounded to 6 places; the trend's first and last buckets may be partial; M07 counts each root of a multi-root trace; `models/compare` is a bounded top-N, not paginated; no benchmark yet (Phase 5).
- **Unchanged:** R08 residual, R16, R20 (the Starlette `httpx` deprecation warning), R23, R24, R26, R27, R30. Public deployment is not authorized. Costs are estimates, not invoices.

## 11. Phase 2 acceptance prerequisites

1. Your review of Phase 2C, including the T24 cross-check method (§6), the early backend half of `models/compare`, and the R32 attribution (§2.3).
2. The real smoke run stays **NOT RUN** unless you approve it with an `OPENAI_API_KEY` and a budget. The master plan accepts the SYNTHETIC fake-adapter path when no key is available.
3. Decide whether to seed the dev database (`python -m app.cli seed-prices`); re-verify the catalogue first if time has passed (R28).
4. After acceptance: the `phase-2` tag on the accepted commit (not created here).

## 12. Verdict

**Phase 2C: complete, pending your review.** The security gate is closed with evidence: the leak paths are fixed and regression-tested, the credential is rotated and verified, and R32's mechanism is reproduced and fixed. Pricing is re-verified and unchanged. M01–M15 and their API are implemented and tested, and the FastAPI root-span demo works end to end. Every final run in §5 passed with 0 failures and 0 skips; the one intermittent failure the gate found was a test-data flake, fixed at its root. No real OpenAI request was made, no paid service was used, nothing was repriced, and no `phase-2` tag exists. Phase 3 starts only on your instruction.
