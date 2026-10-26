# Phase 4 — Final Architectural Acceptance, Security Verification and Release

- **Date:** 2026-10-26 (UTC; the machine's local clock is UTC+3).
- **Instruction (user, Phase 4 final):** freeze Phase 4, audit ADR-22…26 against the code, prove the trace-to-testset identity end to end, audit execution and integrity, document R66/R67, run every regression gate, push `main` fast-forward, verify the GitHub Actions run of the closure commit, then tag `phase-4`. Phase 4D was technically accepted for the offline-only MVP scope. Phase 5 not started.
- **Scope of change (feature freeze held):** one security correction (`frontend/proxy.ts` matcher, §3.1), one test-portability fix (§7.2), focused acceptance regressions (§4, §3.1) and documentation. No product feature, provider, diagnostic category, rule type, dashboard, schema, migration, ADR text, dependency, lock file or workflow change.
- **Not done (by instruction):** no real or paid OpenAI call, no public deployment, no history rewrite, no repository visibility change, no Phase 5 work.

## 1. Baseline (before any change)

| Check | Result |
|---|---|
| Repository | `git rev-parse --show-toplevel` = `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository; the home-directory repository untouched) |
| Branch / HEAD / tree | `main`, `62b6b0b` (4D) ← `5949ed7` (4C) ← `7fd1528` (4B) ← `888b188` (4A) ← `e5a631c` (`phase-3`); clean |
| Tags | `phase-0` `4c99fcc`→`a16d64b`, `phase-1` `500e97f`→`cec668b`, `phase-2` `200629e`→`75ff8c3`, `phase-3` `55d2f4e`→`e5a631c`; identical on the remote (`git ls-remote --tags`); no `phase-4` |
| Origin | `https://github.com/MuratElberKayaa/LLM-Insight` (fetch and push); after `git fetch`, `origin/main` = `e5a631c`, local 4 ahead / 0 behind (fast-forward possible) |
| PostgreSQL | `llminsight-postgres-1` healthy on `127.0.0.1:55432`; `alembic current` = `0005 (head)`; `alembic check`: no new operations |
| Dev DB | 2 projects, 28 traces, 846 spans, 43 usage rows, 6 prices, 5 keys; 0 datasets, reviews, cases, experiments, results; count and content md5 of every table recorded |
| Real-LLM settings | `LLMI_ALLOW_REAL_LLM=false`, `LLMI_PROVIDER_MODE=mock`, `OPENAI_API_KEY` empty, not set in the environment; `openai` not installed in the backend venv |
| Tracked env files | `.env.example` only (`.env`, `frontend/.env.local` git-ignored) |

Read: `CLAUDE.md`, `frontend/AGENTS.md`, `MVP_MASTER_PLAN.md` (§D, §E, §F, §H, §I), `PRD.md`, `ADR.md` (full text of ADR-22…26), `ARCHITECTURE.md` (§4.9–4.12, §5.8–5.10), `SECURITY.md`, `TEST_MATRIX.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md`, `reports/phase-4a…4d.md`; and the implementation of every audited claim (§2).

## 2. Completed Phase 4 capabilities (4A–4D)

| Sub-phase | Delivered | Evidence |
|---|---|---|
| 4A | Deterministic, metadata-only trace diagnostics `GET /api/v1/traces/{id}/diagnostics` (`llminsight.diagnostics.v1`), hypotheses `verified: false`, nothing persisted | `reports/phase-4a.md` |
| 4B | CR-03 Failure Review, human-approved Trace-to-Testset with server redaction, datasets, the dashboard review/decision/dataset/test-case forms (migration `0003`) | `reports/phase-4b.md`; T33–T35 |
| 4C | Rule contract `llminsight.rule.v1` (RE2, bounded JSON Schema subset, exact numbers), review freeze and eligibility in API and triggers (migration `0004`), experiments schema with append-only triggers (migration `0005`), 12-case SYNTHETIC golden dataset, mock-only A/B experiments and honest report | `reports/phase-4c.md`; T36–T40 |
| 4D | Experiment list/setup/results/case pages, the fifth write (ADR-26), bounded execution, report money fix, live loop checked against PostgreSQL | `reports/phase-4d.md`; T48 |

## 3. ADR-22 … ADR-26 audit

Verdict per ADR: **consistent with the implementation and with ADR-01…21**, with the correction in §3.1 (ADR-22's guarantee restored by a one-line fix) and the clarification in §3.2 (recorded for the architectural review, ADR text unchanged). No critical architectural conflict; no accepted ADR weakened.

| ADR | Verified in code | Key tests | Finding |
|---|---|---|---|
| **22** local Server Actions | Exactly five `"use server"` exports in three modules (`lib/actions/{reviews,datasets,experiments}.ts`), all `submit*` that re-validate the form and call one `lib/server` function; the build's `server-reference-manifest.json` lists exactly `submitReview`, `submitDecision`, `submitDataset`, `submitTestCase`, `submitExperiment`. `sendJson` allowlist: five (method, path) pairs, nothing else; no route handlers. Token read only in `lib/server/config.ts` (`server-only`), sent as a bearer header to a loopback origin only. `proxy.ts`: `Host` loopback or `421`; GET/HEAD or `405`; POST only on four write pages with `Origin` = host, `Sec-Fetch-Site` same-origin when sent, `Next-Action` present, else `403`. Backend re-validates, requires the admin token, scopes by project | `security-static.test.ts`, `request-guard` unit tests, E2E CSRF refusals, bundle scan, T41/T42 | **§3.1: the guard did not run on `/favicon.ico`, where a Server Action executed. Fixed.** |
| **23** rule contract | `version: 1` + exact keys per type, unversioned/unknown refused on write and `rule_unsupported` on read; RE2 only (`safe_regex.py`; `log_errors=False`, 1000 chars, 1500 instructions, 2 MiB, 32 KiB input; no `re` fallback); JSON Schema 2020-12 keyword allowlist at every depth, `$ref` only `#/$defs/…`, acyclic, expansion-bounded, no-retrieval registry, no format checker, keyword budget; exact `Decimal` JSON; outcomes `true`/`false`/`NULL` with `NULL` outside denominators; details carry no values | `test_case_rules.py`, `test_safe_regex.py`, `test_safe_schema.py` (sockets/`urlopen` blocked), `test_exact_json.py`, `test_evaluation_engine.py`, `test_evaluation_boundaries.py`; T36, T37, T40 | none |
| **24** review integrity | Eligibility (`hypothesis_supported`, or `hypothesis_rejected` + expected output), separate `approved: true`, decision frozen once cited, text and references immutable; API row locks (`FOR SHARE` / `FOR UPDATE`) plus triggers `failure_reviews_integrity`, `test_cases_review_eligibility` (SQLSTATE 23000 → controlled `409`); partial unique index `uq_test_cases_dataset_id_origin_review_id` | `test_phase4c_integrity.py` (HTTP races, raw-psycopg races, 8 concurrent conversions with the API lock disabled), `test_migrations.py`, `test_phase4d_api.py` (bypassed API checks end in the trigger's `409`); T33–T35 | none |
| **25** mock experiments | Only `mode: mock` executes; `real` validated then `409` (`real_llm_not_allowed` / `real_mode_unavailable`), nothing written; scripted provider keyed by (version, SHA-256 of input), unknown → `mock_no_scripted_output`; cases in id order, A then B; one transaction; append-only results and finish-once experiments (triggers); every result `synthetic` (service + trigger); budget pre-check (`budget_unverifiable`, `budget_exceeded`) and run-time guard; cost at the stored price in force at `started_at`, unknown never 0; report compares quality only on paired evidence and cost only when fully priced | `test_experiment_api.py`, `test_experiment_runner.py`, `test_experiment_report.py`, migration tests; T38, T39 | note in §5.4 (real-mode prerequisite) |
| **26** experiment write | `submitExperiment` → `POST /api/v1/experiments` only; mode constant `mock` in `lib/experiment-form.ts` (never read from the form); confirmation required; double-submit guard; options decoder refuses any non-mock mode; backend 4 concurrent / 15 s / controlled `500`; dashboard write deadline 30 s; no retry | `phase4d.test.tsx`, `security-static.test.ts`, `phase4d.spec.ts`, `test_phase4d_api.py` | **§3.2: action ids are not page-bound** |

### 3.1 Security correction: Server Action dispatch on `/favicon.ico` (fixed)

- **Found by a live probe** (production build, run-owned project): the browser's genuine `submitExperiment` request was captured (and aborted), then replayed. `proxy.ts`'s matcher excluded `_next/static`, `_next/image` and `favicon.ico`. The app has no `favicon.ico` (its icon is `app/icon.svg`), so a POST to `/favicon.ico` rendered the not-found page, and Next.js 16.4.3 dispatched the Server Action named in `Next-Action` before rendering. **A same-origin replay to `/favicon.ico` answered `404` and stored an experiment** (reproduced three times). None of the proxy checks ran for that path: loopback `Host`, required `Origin`, `Sec-Fetch-Site`, POST only on write pages. Only Next.js's own Origin/Host comparison remained, and that check lets a request without `Origin` through.
- **What was not exploited:** a cross-site `Origin` was refused by Next.js (`500`, no write). The probed DNS-rebinding and missing-Origin variants did not write either, but the reason was not established, so they are not claimed as a guarantee. `/_next/static/*` (`404`) and `/_next/image` (`421`) never dispatched an action. Exploitation from outside the local trust boundary was **not demonstrated**: a loopback-origin context is already inside R54. The defect broke ADR-22's stated guarantee ("everything else is `403` (or `405`)") and its defence in depth, so it was treated as a necessary security correction.
- **Fix:** the matcher is now `/((?!_next/static|_next/image).*)`. A POST to `/favicon.ico` now gets the proxy's `405`; a GET still gets the proxy's Host check, CSP and the not-found page. No ADR text changes, because the fix restores what ADR-22 already states.
- **Regression tests:** unit `security-static.test.ts`, "the request guard runs on every path except the static assets and the image optimizer" (mutation: putting `favicon.ico` back fails it). Live E2E `phase4d.spec.ts`, "a genuine experiment Server Action POST runs only on its write page": the captured real request replayed to `/favicon.ico` (`405`), `/experiments` (`405`), `/experiments/{id}` (`405`) and `/_next/static/…` (`404`) stores nothing, and the identical same-origin request on `/experiments/new` runs exactly once (positive control). The mutation check against the pre-fix build failed it (`/favicon.ico` expected 405, received 404).
- Probe results after the fix, same replay: unguarded paths now `405`/`404`/`421` with no write; write page cross-site `403`, rebinding `421`, no Origin `403`, no `Next-Action` `403`; control `200` with exactly one experiment.

### 3.2 Clarification for the architectural review: action ids are not bound to pages (R69)

The same probe showed that Next.js dispatches any of the five action ids on **any** of the four write pages: `submitExperiment` replayed to `/datasets` ran. ADR-26 says `proxy.ts` "accepts its POST on `/experiments/new` only". That is true of the proxy's page list, but the action is not refused on the other three write pages. The security effect is nil. Every write page applies the same origin checks, the same five actions are permitted from the same origin anyway, and each action re-validates its form and calls one allowlisted backend write. The narrowness guarantee therefore rests on the five-action set and the allowlist, not on page binding. The ADR text is left unchanged (no silent edit). Recorded as R69 for the architectural review: either clarify ADR-26's wording in a superseding note, or bind action ids to pages in the proxy (from the build manifest) in a later phase.

## 4. Trace-to-Testset evidence: the human-approved case's own id

The existing Phase 4D evidence proved the human case only **transitively**. The E2E captured `humanCase` from the API and compared both versions' database case ids with the dataset's 12 ids, but it never asserted that id itself, and the database read returned no lineage. No backend test followed a reviewed case into an experiment. Two focused regressions were added (no feature change):

1. **Backend integration** `test_experiment_api.py::test_the_human_approved_case_itself_is_run_under_both_versions` (real API, disposable PostgreSQL). The flow is: SYNTHETIC failing trace → review (`schema_violation`) → decision (`closed`, `hypothesis_supported`) → conversion of the golden G02 input with `origin_trace_id`/`origin_review_id` and `approved: true` → the other 11 golden cases standalone (G02 is **never** also seeded standalone) → mock experiment. Checked directly in PostgreSQL:
   - exactly one case carries that input, with that id, dataset, trace and review, and the review's own `trace_id` and `hypothesis_supported`;
   - `experiment_results` holds exactly two rows for that id (A `false` with `schema_violation: …`, B `true`), both `synthetic`, under an experiment on that dataset;
   - both versions' id arrays are equal and contain it, and the report lists it under both versions.

   **Mutation:** making the run skip review-originated cases (`cases_for_run` filtered on `origin_review_id IS NULL`) fails this test and **only** this test (26 others in the module pass), which shows the gap was real.
2. **Browser E2E** `phase4d.spec.ts` (the live loop): `expect(caseIds).toContain(humanCase.id)`. In addition, `scripts/dev/e2e_fixtures.py experiment-counts` (read-only, run-owned experiments only, ids and outcomes, no content) now returns `reviewed_cases`, and the test requires exactly `[{test_case_id: humanCase.id, origin_trace_id: traceId, origin_review_id: reviewId, passed: {v1: false, v2: true}}]` from PostgreSQL.

**Isolation and cleanup:** the backend test runs in its module's disposable database. The E2E data lives in run-owned projects deleted by the global teardown (`remaining: []` on every run); the probe's projects were removed with the same teardown. After every run, the count and content md5 of all 11 dev-DB tables were **identical** to the baseline.

## 5. Execution and integrity audit (ADR-25/26)

| Requirement | Verified | Evidence |
|---|---|---|
| Mock-only execution | Only `mode: mock` reaches the runner; the form sends the constant | `test_real_mode_is_refused_and_writes_nothing`, E2E refusal test, `phase4d.test.tsx` (crafted `mode=real` still sends `mock`) |
| Real mode refused | `422` without budget; `409` with one; nothing written | same |
| ≤ 4 concurrent runs per process | `threading.BoundedSemaphore(4)`, non-blocking acquire before the transaction → `429 experiments_busy` + `Retry-After: 5`, never queued. Released in `finally` only after the worker thread returns (Starlette's `run_in_threadpool` does not abandon a running thread on cancellation) | `test_runs_beyond_the_concurrency_bound_are_refused_not_queued`, `test_slots_are_released_after_every_outcome`, `test_concurrent_experiments_are_independent_and_complete` (4 × 201) |
| 15 s limit | Cooperative wall-clock checkpoint before each simulated call → `409 experiment_time_limit`. A run lasts at most 15 s plus one bounded evaluation (≤ ~0.5 s, R60) plus its DB writes. Lock waits and the database writes are not under this deadline (single local process, pool timeout 5 s) | `test_a_run_past_its_deadline_is_stopped_and_stores_nothing` |
| Rollback, no partial results | Every refusal raises inside `write_transaction`, so the experiment row and all results roll back together; storage errors → `503`, unexpected → fixed `500 internal_error` (type logged only) | `test_storage_failure_rolls_the_whole_run_back`, `test_an_unexpected_evaluation_error_is_a_controlled_500`, deadline and budget tests (`experiments`/`experiment_results` counts 0) |
| Controlled HTTP errors | `422` (no echo), `404` indistinguishable, `409` codes, `429`, `503`, `500` fixed body | `test_request_validation`, `test_project_isolation_and_unknown_records`, above |
| Budget pre-flight | From the provider's estimate before any call; unknown estimate → `budget_unverifiable`, over → `budget_exceeded` | `test_budget_is_enforced_before_execution` |
| No uncontrolled evaluation work | ≤ 200 cases / 400 evaluations per run, 4 runs, RE2/schema bounds per evaluation | as above, R60 |
| No paid-provider network | `openai` not installed in the backend venv (`pip show`); static test forbids importing it in `app/`; an experiment ran with every non-loopback socket and DNS lookup blocked | `test_no_external_network_is_used`; §9 |
| Exact Decimal, unknown stays unknown | `NUMERIC(20,10)`, single half-even rounding, plain decimal strings; missing usage/price → `NULL` + reason, never `0` (`cost_usd = 0` rows: 0) | `test_golden_experiment_end_to_end`, `test_money_below_one_millionth_is_a_plain_decimal_string` |
| Same case ids across variants | A then B per case in id order; `ids_identical_across_versions` | T38; §4 |
| Append-only results | Trigger `experiment_results_integrity`; `experiments_integrity` (only `finished_at`, once) | `test_results_are_append_only_and_experiments_finish_once`, `test_result_trigger_keeps_results_inside_their_experiment` |
| No winner on incomplete evidence | `higher_pass_rate: null` when any case is one-sided; cost not compared unless fully priced; the dashboard decoder rejects a violating report | `test_golden_experiment_end_to_end` (`partial`), E2E verdict panels, 4D mutation checks |
| Phase 4C trigger mutation regressions | Both still pass on the final code | `test_bypassed_eligibility_check_still_ends_in_a_controlled_409`, `test_bypassed_freeze_check_still_ends_in_a_controlled_409` |

### 5.4 Note for any future real executor (not a defect in mock mode)

The run-time budget guard stops issuing calls only **after** the recorded known spend exceeds the budget, so it can overshoot by one call. Calls with unknown cost do not count towards it. For the deterministic mock this cannot happen: the pre-check uses the exact outcomes and refuses unknown estimates. A real executor must check the projected cost **before** each call and stop on any unknown cost. This is recorded among the Phase 5/real-mode prerequisites (§10).

## 6. Accepted risks R66 and R67

- **R66, duplicate on a lost response (accepted, local mock-only).** A run is one synchronous request. If the answer is lost (the dashboard's 30 s deadline, a stopped process, a dropped connection), the backend still commits or refuses atomically and the form says "may or may not have been stored: refresh the page and check before submitting again". Nothing retries automatically: `sendJson` makes exactly one `fetch` and the action never resubmits. A second explicit submission creates a second, identical, independent experiment, since there is no idempotency key. **Safe inspection:** the Experiments page (`/experiments`, side navigation; newest first with dataset, versions, model, mode, state, result count and start time) and the read-only `GET /api/v1/experiments?project=<id>`. **Durable idempotency** (a client-generated key stored with a unique constraint, or a two-step create/execute) **is required before any paid or real execution mode.**
- **R67, process-local bounds (accepted, local MVP).** The 4-run semaphore, the 15 s deadline and the rate-limit buckets live in one process and reset on restart. Verified single-process paths: `python -m app` runs `uvicorn.run(..., workers=1)`, and the backend container's `CMD` is `alembic upgrade head && exec python -m app` (same entry point). No other server entry point exists. **A distributed or multi-worker deployment needs a new concurrency-control design** (for example a database-backed run lease or a queue with a global limit) and a superseding ADR.
- **Preserved:** no public login (R39, R54), heuristic redaction (R08, R56), self-declared review and approval identities (R55, D8 deferred), the loopback-only security boundary (ADR-08, ADR-22), client-asserted provenance (R16), process-local counters (R23, CR-08), and slow headers/connection exhaustion (R24, R34). **No public deployment is authorized** (`SECURITY.md` §7).

## 7. Regression results (final code; commands from the named directory)

### 7.1 Gates

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1720 passed**, 0 failed, 0 skipped, exit 0 (189 s; 1 pre-existing Starlette/httpx deprecation warning, R20). +1 versus 4D: the §4 identity test |
| same (sdk, unit + live backend) | **350 passed**, 0 failed, 0 skipped, exit 0 (77 s) |
| `npm run typecheck` / `npm run lint` (frontend) | exit 0 / exit 0 (`--max-warnings 0`) |
| `npm test` (frontend) | **311 pass**, 0 fail, 0 skipped, exit 0 (+1: the §3.1 matcher test) |
| `npm run build` / `npm run check:bundle` | exit 0 / 632 files (23 in the browser bundle), findings **0** |
| `npm audit --omit=dev` / `npm audit` | 0 / 0 vulnerabilities |
| `npx playwright test` (real backend on `:8000`, Edge, production build) | **53 passed**, 0 failed, 0 skipped, 0 retries, exit 0 (1.8 min); teardown `remaining: []` (+1: the §3.1 replay test). The run before the fix: 52 passed |
| `ruff check .` / `ruff format --check .` (backend, sdk, the two dev scripts) | exit 0 / exit 0 |
| `python -m mypy src`, `--config pyproject.toml ../demo_app` (sdk, strict) | no issues (8 and 3 files) |
| `python -m pip check` (backend, sdk) | no broken requirements |
| `alembic current` / `alembic check` (dev DB) | `0005 (head)` / no new upgrade operations |
| migration cycle | `test_migrations.py` within the backend run: base ↔ head through `0003`–`0005`, data preservation, refused upgrades and downgrades (T31) |
| `docker compose config --quiet` / `--profile app config --quiet` | exit 0 / exit 0 |
| `docker compose --profile app build` | exit 0: `llminsight-backend` and `llminsight-frontend` built; no container started (only `postgres` runs) |
| `python -I scripts/verify_phase0.py --quiet` | see §11 |
| Credential/privacy scan (4 local secret values from the git-ignored env files, never printed; key-shaped patterns `sk-…`, `lli_…`, bearer, private key, AWS, GitHub) | working tree 426 files: **0**; all 736 history blobs: **0**; all commit messages: **0**; key-shaped: **0**; tracked env files: `.env.example` only. Re-run before the push (§8) |
| Backend log of the live runs | 0 tracebacks, 0 occurrences of golden case content |
| Dev DB after all live runs | count and content md5 of all 11 tables identical to the baseline |
| `git diff --check` | see §11 |

### 7.2 Test-portability fix (Windows checkouts)

The first local `npm test` run on this working tree failed one test, `security-static.test.ts` "Server Actions exist only in the listed action modules" (`lib/actions/datasets.ts: the directive must be the first statement`). **Root cause:** the system Git configuration sets `core.autocrlf=true`. A `git stash`/checkout during the Phase 4D flaky-test investigation rewrote 136 working-tree files with CRLF (their timestamps match to the second; the index holds LF), and the static test's line-anchored regexes (`^"use server";\n`, `$` with `/m`, `^export .*$`) assume LF. This is not a product defect: the committed blobs are LF and Linux CI checks out LF. It is a test defect that would fail on any fresh Windows clone. **Fix:** the test's `read()` normalizes CRLF to LF before matching. Every assertion keeps its meaning; a CRLF mutant (an export placed before the directive) still fails the test. The Phase 4D report's "310 pass" was therefore true of an LF working tree but not reproducible on this one before the fix.

### 7.3 Test matrix (exact `TEST_MATRIX.md` criteria)

| ID | Status (unchanged) | Phase 4 final evidence |
|---|---|---|
| T33★ | PASS (phase-4b) | `test_review_api.py`, the browser review in the live loop |
| T34★⛔ | PASS (phase-4b) | `test_dataset_api.py` (missing/false `approved` → `422`, 0 rows); approval box in the live loop |
| T35★⛔ | PASS (phase-4b) | `test_dataset_api.py` masking before insert, `redaction_applied = true` |
| T36★ | PASS (phase-4c) | engine suite; G01/G05 pass |
| T37★ | PASS (phase-4c) | G02 v1 `schema_violation` with detail, also for the human case (§4) |
| T38 | PASS (phase-4c) | identical ids in API, page and PostgreSQL; the human case's id under both versions (§4) |
| T39★ | PASS (phase-4c) | every stored result `synthetic`; trigger refuses `real` in a mock run |
| T40 | PASS (phase-4c) | G11 `NULL`, outside the denominator (7/11, 4/10) |
| T48★ | PASS (phase-4d) | SYNTHETIC banner/badges on every experiment page in the 53-test E2E run |

Also re-run with unchanged statuses: T31, T41, T42 (bundle scan 0; no token in any E2E response). Still open and **not claimed**: T43 (PARTIAL, Phase 5), T47 (NOT RUN, Phase 5). The Phase 5 gate (0 FAIL on all 42 critical tests, every one of the 48 reported) is not claimed here.

## 8. Push and CI

The closure commit was pushed to `origin/main` with a normal fast-forward push only after these checks: origin URL exact; `origin/main` (`e5a631c`) is an ancestor of the commit; no tracked `.env` or secret file; credential scan of the changed files and the full history 0; only the four Phase 4 commits and this closure commit added. No `--force`, `--force-with-lease`, `--mirror` or `--all`. The GitHub Actions run of that commit, its jobs and its result are recorded in the annotation of the `phase-4` tag, which is created only after that run passes. A CI result cannot be written into the commit it tests; the Phase 5 baseline re-verifies it.

**CI coverage** (`.github/workflows/ci.yml`, `ubuntu-24.04`): `docs` (documentation verifier); `python` (compose validation of both profiles, PostgreSQL via compose, backend lint/format, dev-script lint, `alembic upgrade head` + `alembic check`, full backend suite and full SDK suite with `LLMI_TEST_REQUIRE_DB=1`, so a missing database fails instead of skipping, SDK strict mypy); `frontend` (`npm ci`, `npm audit`, typecheck, lint, unit tests, production build, bundle secret scan). **Excluded from CI, local gates only:** the Playwright live E2E (needs a seeded live backend and Edge), the compose image build, the credential/history scan of this report, and Windows-only behaviour (R32, R34).

## 9. Privacy, provenance and real-provider confirmation

- No prompt, completion or model output is stored or returned (`test_outputs_are_never_persisted_or_returned`); test-case content is human-typed SYNTHETIC text after redaction (heuristic, R56).
- Every experiment result is `synthetic`, and the report carries `simulated_provider: true`. Costs are labelled synthetic estimates and never enter the usage ledger. Telemetry provenance remains client-asserted (`provenance_verified: false`, R16).
- **No real or paid provider call was made in this phase:** `OPENAI_API_KEY` empty, `LLMI_ALLOW_REAL_LLM=false`, `openai` absent from the backend venv, the SDK's OpenAI tests are offline behind a network block, and experiments run with non-loopback sockets blocked in their test.

## 10. Remaining Phase 5 requirements and prerequisites

1. T47 clean-clone `docker compose up --build` → init → demo; T43 full rate-limit verification; all 48 tests reported, 0 FAIL on the 42 critical tests.
2. `pip-audit`, `bandit`, final secret scan; benchmark with N, hardware and method.
3. README, final `DEMO_SCRIPT.md`, `FINAL_DELIVERY_REPORT.md`.
4. Architectural decision on R69 (ADR-26 wording, or page-bound action ids).
5. **Before any real or paid execution mode** (each needs its own approval): durable idempotency (R66), a pre-call budget guard that stops on unknown cost (§5.4), `LLMI_ALLOW_REAL_LLM`, a per-run approval, a real executor, and possibly a background worker (R60).
6. **Before any shared or public deployment:** the `SECURITY.md` §7 list (real authentication, TLS, per-project authorization, a distributed rate limiter and concurrency control (R67), audit logging, a retention and privacy review) and decision-actor history (D8, R55).

## 11. Files and final checks

Changed: `frontend/proxy.ts` (matcher), `frontend/tests/unit/security-static.test.ts` (matcher test, CRLF-safe reads), `frontend/tests/e2e/phase4d.spec.ts` (human-case id assertions, replay test), `backend/tests/integration/test_experiment_api.py` (identity test), `scripts/dev/e2e_fixtures.py` (`reviewed_cases`, read-only), `docs/SECURITY.md`, `docs/SOURCES_AND_RISKS.md` (R66, R67 updated; R68–R70 new), `docs/TEST_MATRIX.md` (re-verification note), `docs/PHASE_STATUS.md`, this report.

Final checks after this report was written are recorded in `PHASE_STATUS.md` (Phase 4 final section): `verify_phase0.py`, `git diff --check`, the compose image build and the pre-push credential scan.

**Verdict: PASS for the Phase 4 scope** (offline-only MVP, mock-only experiments, local loopback deployment) on the evidence above, subject to the GitHub Actions run of the closure commit, which gates the `phase-4` tag. Public deployment remains prohibited. Phase 5 not started.
