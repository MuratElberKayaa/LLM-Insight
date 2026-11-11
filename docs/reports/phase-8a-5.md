# Phase 8A-5 — Multi-provider comparison integration verification (offline)

**Date:** 2026-11-11 UTC. **Status: implemented; locally verified where no database is needed; approved for one local commit and CI submission (§9). The first CI run of PR #8 failed in 4 backend database tests on a test-fixture defect; the correction is a second local commit, not pushed (§10). The second CI run failed in the SDK suite on two test-design defects; their correction was approved and is a third local commit, not pushed (§11). The 29 database-backed tests (21 SDK, 8 backend) are collected and NOT RUN LOCALLY; they require GitHub CI before this checkpoint closes.**

Provider API requests: **0**. No credential, billing account, `.env`, PostgreSQL server, database, volume, Docker container or deployment was touched: every database test was only collected, or run from copies holding no `.env` and with no database URL, where it skips or fails before any connection. `REAL_EXECUTION_ENABLED` is still `False`, `FREE_PLAN_MODELS` is empty and `FREE_PLAN_EVIDENCE_VERIFIED_AT` is `None` in the shipped code. No production code changed. The three user-owned untracked files were not opened, copied or changed. `.env.example` is unchanged (R127 open).

Base: `feature/phase-8a5-comparison-integration` at `b34a018` (= `main` = `origin/main`, the merge of PR #7).

**Synthetic integration tests do not verify live-provider telemetry.** Every provider response here is a SYNTHETIC fixture; the first real Groq response remains untested (R115, R120, R124).

---

## 1. What was built

Test code only, reusing the existing integration infrastructure. No new database, pipeline, fixture server or dependency.

| File | Tests | Path exercised |
|---|---|---|
| `sdk/tests/integration/test_live_compare.py` | 25 (21 database-backed, 4 wire; 20, 17 and 3 before §11) | `demo_app.live_compare` → Free Plan guard → the audited `httpx` client → SYNTHETIC wire (`HTTPTransport.handle_request` replaced) → `GroqTextAdapter` → SDK spans → official OTLP/HTTP protobuf exporter → live `python -m app` (the session's `live_backend`) → disposable PostgreSQL → authenticated `/api/v1` |
| `backend/tests/integration/test_multi_provider_read.py` | 8 (all database-backed) | SYNTHETIC OTLP protobuf (`tests/otlp.py`) → real `/v1/traces` → disposable PostgreSQL (the `ingest` database) → authenticated Overview, Traces and Models / Compare |

**Execution lock in the SDK file.** The runner has only the real path, and the shipped code refuses it twice. The tests lift both locks inside the test process only, through the same `released` fixture as the guard's unit tests. One test runs as shipped against the live backend and shows that execution is refused and nothing is stored. Because of this, the stored spans carry `llminsight.provenance = real` although the provider is a fake (R129): `provenance_verified` stays false, every name and response ID says SYNTHETIC, and the rows live only in the disposable CI database.

**OpenRouter.** The backend file contains one OpenRouter row, model `synthetic/compat-only:free`, built by the test. It is a synthetic representation for the read API only. **No OpenRouter adapter exists**, and the comparison runner still refuses `openrouter`.

## 2. Findings during implementation

- **Correction of the 8A-4 report.** Its §5 said the Traces view filters comparison traces "by provider, model and prompt version". `GET /api/v1/traces` has no `prompt_version` parameter, and FastAPI ignores unknown query parameters, so a request with one returns the unfiltered list. Only Overview and Models / Compare filter by prompt version; each trace item and detail carries it. The 8A-4 sentence is corrected, the new tests do not rely on the parameter, and R130 records the limit for 8A-6. This is a documentation error, not a production defect.
- **A weak assertion, found while choosing mutants.** The first draft compared the stored root span name with the runner's own constant, so renaming the root span would have passed. Both files now pin the literal `demo.live-compare` (mutant W3, §5.3).
- **OpenAI pricing applies only to the standard tier and the global host.** The backend file's OpenAI row sets `openai.response.service_tier = default` and `server.address = api.openai.com`, as the existing cost tests do; without them the row would be `price_missing`.
- **No production defect was found.** Nothing in the application code was changed.

## 3. Coverage of A–L

| | Requirement | SDK `test_live_compare.py` | Backend `test_multi_provider_read.py` |
|---|---|---|---|
| A | Prompt name, version, provider and model per scenario | `test_each_scenario_is_stored_and_read_back_with_its_exact_identity` (×3), `test_each_scenario_on_the_wire` (×3) | `test_each_comparison_span_is_stored_with_its_identity_and_unknown_cost` |
| B | Trace, root span, CLIENT span, attempt and usage in PostgreSQL | same, read with SQL: counts (1, 2, 1), parent link, attempt 1, call key `resp:groq:<id>`, tokens 20 / 32 | same, plus "the root never produces usage" |
| C | A resent export creates no duplicate | `test_a_reexported_comparison_trace_creates_no_duplicate` (the official exporter resends the finished spans twice) | `test_a_resent_comparison_export_adds_nothing` (the same body posted three times) |
| D | Groq `unknown` / `price_missing` with a matching catalogue row | the session's one canonical row for exactly `groq` and the model (`conftest.groq_price`, shared with `test_live_groq.py`, §11); every Groq row asserts `price_missing`, no price version | rows for `groq` and `openrouter` inserted once per session at a fixed date (§10); the read-back test asserts each is effective before the spans; both `price_missing` |
| E | Overview counts, tokens and cost completeness | §11: `test_one_runner_execution_gives_one_overview_and_compare_row` (×3, one real runner execution each): 1 attempt, 20 / 32 tokens, `price_missing`, `cost_complete: false`, no price version. `test_a_synthetic_comparison_set_keeps_one_compare_row_per_scenario` (SYNTHETIC adapter spans, not the runner): 3 attempts, 60 / 96 tokens, 3 × `price_missing` | `test_overview_counts_tokens_and_cost_completeness`: six rows from four providers, OpenAI cost 0.001575 computed, five unknown, incomplete; filters by provider and prompt version |
| F | Trace list and detail | identity test: detail roles, children, finish reason, dropped count, usage; list by provider and model | `test_trace_list_and_detail_keep_provider_model_status_and_relationships` |
| G | Compare keeps the three scenario versions apart | the synthetic set: three groups, one per `cmp-v1.*`, the `prompt_version` filter returns one; each runner execution: exactly its own group, no group for the other two versions | six groups (OpenAI, Gemini, three Groq, synthetic OpenRouter); Groq filter three; one scenario across two providers |
| H | Project isolation in Overview, Traces and Compare | `test_a_comparison_trace_never_appears_in_another_project` (detail 404 identical to a missing trace) | `test_no_view_of_one_project_shows_another_projects_trace` |
| I | Unsupported or incomplete data fails closed | nine runner cases: 429, timeout, malformed, usage missing, usage inconsistent, incomplete, empty, 402 halt, service-tier halt; each stored once, no zero tokens, no cost, never exit 0 | `test_incomplete_or_invalid_provider_data_manufactures_nothing`: no usage, a negative count (dropped, counted), an error without a response model (its own Compare row) |
| J | OpenAI and Gemini compatible; OpenRouter clearly synthetic | (Groq only) | OpenAI priced from the verified catalogue, Gemini `price_missing`, both in the same views |
| K | No prompt, response, reasoning or credential in metadata, storage or the API | stdout, stored rows, read API and the backend log checked for the prompt texts, the answer, reasoning, error and generation sentinels, the Groq key and the project key; exported protobuf bytes and headers | content and reasoning attributes plus a Groq-shaped key value injected; none in storage or any of the four API responses |
| L | `server_dropped_attributes_count` zero for valid attributes | asserted on every stored root and CLIENT span, and on the read API | asserted for every comparison span; the stored key set equals the shared Groq fixture's |

## 4. Tests executed locally

| Check | Command | Result |
|---|---|---|
| SDK integration, database-free | From a copy without `.env`: `pytest tests/integration/test_live_compare.py` (the three `*_on_the_wire` tests) | **3 passed** |
| SDK clean-install regression | Clean venv from `sdk/requirements-dev.lock`, the SDK installed non-editable (14 of 14 modules AST-identical to the checkout), `python -m pytest -q` in `sdk/` with `LLMI_BACKEND_PYTHON` pointing to a non-existent path | **2220 tests: 2122 passed, 0 failed, 98 skipped** (81 earlier and 17 new database-backed tests: backend interpreter unavailable, so no database or `.env` is reached) |
| Backend unit | Scratch copy without `.env`: `pytest tests/unit` | **1208 passed** |
| Backend privacy and cost | Scratch copy: `test_groq_contract`, `test_comparison_contract`, `test_pricing`, `test_metadata_privacy`, `test_redaction` | **481 passed** |
| Ruff | SDK `check .` and `format --check .`; backend `check .` and `format --check .` | PASS (57 and 145 files) |
| mypy strict | `mypy src`; `mypy --config-file pyproject.toml ../demo_app` | PASS (14 and 11 files) |
| Bandit gate | Bandit 1.9.4 over `backend/app sdk/src demo_app scripts`, baseline evaluate | PASS: 44 = 44, 0 problems, baseline unchanged |
| History secret gate, credential detectors, QA self-tests, doc check | §5.4 | §5.4 |
| Required-mode behaviour without a database | §5.2 | §5.2 |

## 5. Database tests

### 5.1 Collected but not executed locally

| File | Collected | Database-backed | Run locally |
|---|---|---|---|
| `sdk/tests/integration/test_live_compare.py` | 25 (20 before §11) | 21 (17 before §11) | **NOT RUN** |
| `backend/tests/integration/test_multi_provider_read.py` | 8 | 8 | **NOT RUN** |

**25 database-backed tests (29 after §11) were written and collected but not executed locally, which is not a pass.** Both suites' fixtures create disposable databases on the server named by `LLMI_DATABASE_URL` or the repository's `.env`; locally that is the developer's server and `.env`, which this checkpoint may not touch. Their expectations were checked against the code they exercise (the ingest attribute map, the usage ledger, the pricing rules, the trace summary recompute, and the Overview, Compare and Traces payloads), not against a database run.

### 5.2 Fail closed when the database is missing

Run from copies of `sdk/` and `backend/` that hold no `.env`, with no `LLMI_*` variable set, so no fixture could reach a database:

| Suite | Normal mode | `LLMI_TEST_REQUIRE_DB=1` |
|---|---|---|
| SDK `test_live_compare.py` | 3 passed, **17 skipped** ("live backend unavailable: LLMI_DATABASE_URL not set") | 3 passed, **17 errors** |
| Backend `test_multi_provider_read.py` | **8 skipped** ("PostgreSQL unavailable: LLMI settings invalid or .env missing") | **8 errors** |

A missing database in required mode is an error, never a silent skip.

### 5.3 Requiring GitHub CI

The `python` job (`LLMI_TEST_REQUIRE_DB: "1"`, the compose `postgres` service) runs both suites. On this branch: backend integration collects **696** tests (688 before), the SDK suite **2220** (2200 before), of which **135** are integration tests. Expected in CI: the 25 new database tests pass, nothing new is skipped. Not yet run.

### 5.4 Mutation testing and further gates

No production logic was added, so mutation testing targeted the code paths the new tests cover. The database-backed tests cannot be mutation-tested locally. Six mutants of the runner and the adapter were run against the three locally runnable wire tests, from private copies, after a passing control: **6 of 6 caught** (prompt version not sent; prompt name not sent; root span renamed, caught only after the literal pin of §2; server port not recorded; output cap other than the scenario's; CLIENT span not under the root).

| Gate | Result |
|---|---|
| mypy strict, `mypy src` and `--config-file pyproject.toml ../demo_app` | PASS (14 and 11 files; no source changed) |
| QA self-tests, `python -I -m unittest discover -s scripts/qa/tests` | 214 ran, OK, 15 skipped |
| Credential detectors over the 6 changed and new files | 0 matches |
| History secret gate, throwaway clone with this working tree committed there only and a PR-style merge ref | PASS: 39 findings = 39 entries, 0 problems, coverage COMPLETE, 0 findings in the new blobs; baseline unchanged |
| `python -I scripts/verify_phase0.py`; `git diff --check` | 828 PASS, 0 FAIL; clean |

## 6. Remaining limitations

- The 25 database-backed tests have not run anywhere yet. Until CI runs them, PostgreSQL storage and the read API responses for comparison telemetry are verified by reasoning against the code, not by execution.
- Synthetic fixtures only: the parser, usage mapping and outcome rules are untested against a real Groq response (R115, R120, R124). The integration rows are labelled `real` although synthetic (R129).
- The Traces list cannot filter by prompt version (R130); 8A-6 decides whether the dashboard needs it.
- Gemini comparison runs are not covered: the runner refuses Gemini until R123 is fixed. The backend test shows Gemini rows beside comparison rows, not the Gemini runner.
- OpenRouter has no adapter; its read-API row is a synthetic representation.
- The dashboard itself (frontend rendering) is 8A-6 and was not touched.

## 7. Architectural risks

| ID | State after 8A-5 |
|---|---|
| R123 | **Open**: Gemini real path findings; comparison refuses Gemini |
| R125 | Open: the comparison project is an operator statement. The isolation tests show that a trace stays in the project of its ingest key |
| R126 | Open: single samples; Compare marks n = 1 as low-sample on p95 and p99 (asserted) |
| R127 | Open: `.env.example` comment, required before the first real request; not edited here |
| R128 | Open, mitigated: the checkout match covers seven modules |
| R129 (new) | Accepted for tests only: integration rows labelled `real` are synthetic |
| R130 (new) | Open: no `prompt_version` filter on the Traces list |
| R115, R119–R122, R124 | Unchanged |

## 8. Recommendation

**Ready for architectural review, then one commit and CI submission.** Test code and documentation only. CI must show the 25 new database-backed tests passing with `LLMI_TEST_REQUIRE_DB=1`, and all seven jobs green, before 8A-6. Real Groq, OpenRouter and Gemini requests stay **NO-GO**.

```text
PHASE / DATE: 8A-5 comparison integration verification / 2026-11-11 UTC
BRANCH: feature/phase-8a5-comparison-integration on b34a018, uncommitted, not pushed
PROVIDER REQUESTS / DOCKER / DATABASE / .ENV: 0 / none / none touched / not read
PRODUCTION CODE: unchanged. EXECUTION PATH: disabled as shipped
NEW TESTS: 28 (SDK 20, backend 8); database-backed 25, NOT RUN LOCALLY
LOCAL: wire 3 passed; SDK clean install 2220 tests, 2122 passed, 0 failed, 98 skipped
BACKEND UNIT: 1208 passed. PRIVACY/COST: 481 passed. BANDIT: PASS 44 = 44
REQUIRED MODE WITHOUT DATABASE: 17 + 8 errors (no silent skip)
MUTATIONS: 6 of 6 caught (wire tests)
CI: NOT RUN on this branch. COMMIT: not authorized, not made. 8A-6: not started
```

## 9. Architectural approval (2026-11-11)

The reviewer accepted the implementation and verification of §1 to §8: **conditional GO for one local commit and then CI submission.** The checkpoint stays open until GitHub CI runs the 25 database-backed tests with `LLMI_TEST_REQUIRE_DB=1` and all seven jobs pass.

The final check before staging confirmed:
- exactly the six intended files differ from `b34a018`, with no production source, dependency lock or database configuration changed;
- every provider response is synthetic, and an autouse fixture blocks all non-loopback network access, failing the test on any attempt;
- the lock release is test-only `monkeypatch` state that pytest undoes after each test, and the shipped locks are unchanged;
- R129 and R130 are recorded;
- the three user-owned files are unchanged.

The only changes after §8 are documentation (this section, the status line and `PHASE_STATUS.md`), so no test was rerun; the doc check and `git diff --cached --check` were. §8 records the state before this approval. The commit is not pushed; its SHA is reported to the owner and not recorded in the commit itself.

## 10. PR #8 CI failure and fixture correction (2026-11-11)

**Failure.** As reported by the reviewer from GitHub Actions run `42944649002` on `2f3c698`: six jobs passed. The `python` job failed in backend integration setup, with 1900 passed and 4 errors, on `model_prices is append-only: a later price already exists`. This session did not read that run.

**Root cause: a defect in this phase's test fixture, not in production code.** `seed_unused_price()` was called by the function-scoped `seeded` fixture, so it ran once for each of the five tests that use `seeded`. Each call inserted a price for the same `groq` and `openrouter` models at a random `effective_from`. `model_prices` is not truncated between tests, and migration `0002` rejects an insert dated before an existing row of the same provider and model. Every call dated before the latest earlier row therefore failed in setup. The local verification could not show this, because these tests need PostgreSQL (§5). The 8A-1 safety tests avoid the trigger with a new model name per test; this fixture reused the comparison models.

**Correction** (`backend/tests/integration/test_multi_provider_read.py` only):
- the random insert is replaced by a session-scoped `unused_prices` fixture that inserts each row once, at one fixed `effective_from` (2026-01-01 UTC, before every span) under fixed versions `synthetic-8a5-groq` and `synthetic-8a5-openrouter`;
- `seeded` depends on that fixture instead of inserting;
- the read-back test now asserts its precondition: for each of the two models, exactly that row exists and is effective before the spans. The `unknown` / `price_missing` assertions for Groq and OpenRouter, and the OpenAI catalogue cost, are unchanged.

No production code, migration, constraint or trigger changed, and no price row is updated or deleted. No test is skipped or marked xfail, and no shared fixture changed.

**Order and isolation review.**
- The ingest database is created empty for each session (`disposable_db`), so the session-scoped insert runs once against an empty table. A rerun is a new session with a new database.
- No other test prices these two models. The 8A-1 safety tests price `groq` and `openrouter` under per-test model names, and the trigger compares rows only within one provider and model.
- The OpenAI catalogue seed is the same idempotent call that `test_ingest_cost.py` makes.
- The tests that count all `model_prices` rows run on their own migration databases.
- `pytest --setup-plan` shows `unused_prices` set up exactly once for the file, in file order and in reversed order, and once for each of the five tests that use it when run alone.

**Local verification** of the correction, from a copy holding no `.env` and with no database URL:

| Check | Result |
|---|---|
| Backend `ruff check .` / `ruff format --check .` | PASS (145 files) |
| Backend unit | 1208 passed |
| Collection | 8 in the file; 696 backend integration tests |
| Normal mode, no database | 8 skipped with a reason |
| `LLMI_TEST_REQUIRE_DB=1`, no database | 8 errors (no silent skip) |
| SDK test file | unchanged by the correction |

**NOT RUN LOCALLY:** the 8 corrected backend database tests and the 17 SDK database tests. The SDK tests did not run in the failed CI job either, because it stopped at the backend step. The CI retry must run with `LLMI_TEST_REQUIRE_DB=1`, execute all 8 backend tests in this file and all 20 SDK tests in `test_live_compare.py`, show no new database skip or error, and pass all seven jobs.

## 11. PR #8 second CI failure: two SDK test-design corrections (2026-11-11)

**Failure.** As reported by the reviewer from GitHub Actions run `42963391108` on `9f2a147`: backend 1904 passed; SDK 2213 passed, 1 failed, 6 errors; the other six jobs passed. This session did not read that run. Both defects are in this phase's SDK tests. Neither is a production defect, and no production code, guard, lock, dependency or migration changed.

**F1: two groq prices for one model in the shared SDK database.**
- **Cause.** The SDK `live_backend` database is session-scoped and shared by every integration module. `test_live_compare.py` (8A-5) inserted a groq price for the fixture model dated 2026-02-01. `test_live_groq.py` (8A-2) runs after it in file order and inserted an older one, dated 2026-01-01, for the same provider and model. The append-only trigger of migration `0002` correctly refused the second insert, so `test_live_groq.py`'s module fixture failed and all 6 of its tests errored.
- **Correction.** A session-scoped `groq_price` fixture in `sdk/tests/integration/conftest.py` inserts one canonical row, `synthetic-groq-it`, dated 2026-01-01 UTC (before every span). It then asserts that this row is the only groq price for the model. Both modules' price fixtures (`priced`, `priced_backend`) now only depend on it. The trigger, the price history and every `price_missing` assertion are unchanged.
- **Order and repetition.** The session database is dropped and created again for every session, so a repeated run starts empty. The three other modules that seed prices use the idempotent OpenAI catalogue CLI, which has no groq entry. `pytest --setup-plan` sets `groq_price` up exactly once in each case: compare then groq, groq then compare, each module alone, the whole integration directory, and every test of both modules in reversed order.

**F2: three runner executions inside one test.**
- **Cause.** `test_overview_and_compare_keep_one_row_per_scenario` ran the runner three times in one test. The runner takes `groq_live._PROCESS_SLOT` once and never releases it, so it allows one request per process. The `own_slot` fixture gives each test one fresh slot. The second execution in that test was therefore correctly refused, and the test failed. Its assertions about the aggregate never ran, in CI or locally.
- **Correction:**
  - The test is removed.
  - The slot is not reset, released or replaced inside any test. `own_slot` still gives each test case exactly one slot, so one test case stands for one process lifetime; its docstring now says so.
  - **Real runner executions, one per test case:** `test_one_runner_execution_gives_one_overview_and_compare_row` (×3, one scenario each, in a project of its own). It asserts exactly one Compare group (provenance `real`, low-sample), no group for the other two versions, and Overview with 1 attempt, 20 / 32 tokens, `price_missing` and no price version. The list holds only that trace.
  - **Synthetic aggregate, not the runner:** `test_a_synthetic_comparison_set_keeps_one_compare_row_per_scenario` sends three comparison-shaped traces into one project. They are made by the SDK's Groq adapter on a SYNTHETIC wire, with `synthetic` provenance and no send gate, and exported by the official OTLP exporter. It asserts three groups, one per `cmp-v1.*`, each with provenance `synthetic`, and the `prompt_version` filter returning one. Overview shows 3 attempts, 60 / 96 tokens, 3 × `price_missing`, no price version, no `real` attempt, and no content or key in storage or the log.
  - **Negative tests:**
    - `test_a_second_execution_in_the_same_process_is_refused_on_the_wire` needs no database. The first execution exits 0 and the second exits 2 with `request_limit_reached`, `not_sent` and 0 physical attempts. Exactly one request reaches the wire, and three spans are exported with one CLIENT span. The second execution's only span is the runner's root, with no `gen_ai.*` attribute.
    - `test_a_second_execution_in_the_same_process_is_refused_and_stores_no_call` uses the database. The second trace is stored as root only: (1 trace, 1 span, 0 usage rows). The project holds (2, 3, 1). Compare has one group, and Overview has 1 attempt and 2 traces.
- **Finding, not a defect (R131).** A send-time refusal leaves a root-only trace that counts in the Overview trace total but not in attempts, tokens or cost. This was measured locally on the wire and is recorded as R131 for 8A-6. The runner is unchanged.

**Local verification**, from copies holding no `.env` and with no database URL:

| Check | Result |
|---|---|
| Collection, `test_live_compare.py` + `test_live_groq.py` | 31 (25 + 6) |
| Database-free tests of the two modules (4 wire) | **4 passed** |
| Normal mode, no database | 4 passed, 27 skipped with a reason |
| `LLMI_TEST_REQUIRE_DB=1`, no database | 4 passed, **27 errors** (no silent skip) |
| `--setup-plan`, six orders and selections | `groq_price` set up exactly once in each |
| Mutants of the process-slot guard (slot ignored; slot released after the request), private copies, after a passing control | **2 of 2 caught**, both by the negative wire test |
| SDK clean-install regression (`LLMI_BACKEND_PYTHON` pointing to a non-existent path, so no database or `.env` is reached) | **2225 tests: 2123 passed, 0 failed, 102 skipped** (2220, 2122 and 98 before: +1 negative wire test passed, +4 database tests skipped) |
| SDK Ruff `check .` / `format --check .`; mypy strict `src` and `../demo_app` | PASS (57 files; 14 and 11 files) |
| Bandit gate | PASS, 44 = 44 |
| QA self-tests | 214 ran, OK, 15 skipped |

**NOT RUN LOCALLY:** the 21 SDK and 8 backend comparison database tests, and the 6 `test_live_groq.py` tests. The CI retry must run with `LLMI_TEST_REQUIRE_DB=1` and pass all seven jobs. All backend integration tests and all SDK integration tests must pass with no skip, including:
- both affected SDK modules, which share the one session database there;
- the 25 tests of `test_live_compare.py`;
- the 6 tests of `test_live_groq.py`.
