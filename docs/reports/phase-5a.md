# Phase 5A — MVP contract gap closure (T43, T47, R69) — Delivery Report

- **Instruction (user, Phase 5A, 2026-10-26):** start Phase 5 by closing the remaining locked MVP acceptance gaps, especially T43 and T47; resolve R69 with the stated architectural decision; improve the Optimization Lab only where a locked contract requires it; keep every Phase 1–4 guarantee; stop after Phase 5A.
- **Baseline:** `394920c` (Phase 4 closure), annotated `phase-4`, migration `0005`.
- **Commit/tag:** one Phase 5A commit on `main` (no tag, no push).
- **Environment:** Windows 11, Python 3.12.6, Node 22.23.4 (project-local), PostgreSQL 17 (compose), Microsoft Edge (Playwright). No real or paid provider call; `LLMI_ALLOW_REAL_LLM=false`.
- **Untouched:** the home-directory repository, the global Git and Node configuration, the native PostgreSQL on 5432, `docker-flowise-1`, the `llminsight-postgres-1` container and its volume (up for 37 hours before and after, see §5), the database schema (no migration), provider pricing.

## 1. Baseline verification

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository) |
| Branch, tree, HEAD | `main`, clean, `394920ce440c5ca213b29bb224412b8ff8151b12` |
| `phase-4` | annotated tag object `4d24a91` → `394920c` = HEAD; identical on `origin` (`git ls-remote`) |
| `origin/main` | `394920c` = local `main` |
| `phase-0`…`phase-3` | tag objects and targets identical locally and remotely (`a16d64b`, `cec668b`, `75ff8c3`, `e5a631c`) |
| Phase 4 CI | recorded in the `phase-4` annotation: GitHub Actions run 40372061697, success, jobs docs/python/frontend |
| PostgreSQL / Alembic | `llminsight-postgres-1` healthy; `alembic current` = `0005 (head)`; `alembic check`: no new operations |
| Backend / dashboard | `python -m app` on 127.0.0.1:8000 `/health/ready` 200; dashboard production build on 127.0.0.1:3001 |

## 2. Phase 5 requirements map (Step 2 and Step 4)

**What the locked contracts say Phase 5 is.** `MVP_MASTER_PLAN.md` §H and §I: Phase 5 is *Final QA and delivery*; "Freeze: after Phase 4 no architecture or feature change; the final phase is only defect fixes, tests, documents, presentation". `CLAUDE_PHASE_PROMPTS.md` §6: "Execute PHASE 5 ONLY. Do not add major features." Phase 5 acceptance: `docker compose up --build` + documented init → demo; a 48-scenario report with every case PASS/FAIL/SKIP; 0 FAIL on the 42 critical tests; no security blocker.

**T43 and T47, exactly as locked (`TEST_MATRIX.md`):**

| ID | Locked expected outcome | Status before | Evidence before 5A | Gap |
|---|---|---|---|---|
| T43 ★ | Burst over limit → `429` + Retry-After (ingest, auth-failure and API buckets); Level I | PARTIAL (phase-1c) | Bucket unit tests; integration tests on the Phase 1–3 routes (traces, metrics, projects, diagnostics); ingest per-key isolation; auth failures ingest → API | No rate-limit test on the Phase 4 routes (reviews, datasets, cases, experiments, options); "dashboard path re-verification" open since Phase 1C (`reports/phase-1c.md` §T43) |
| T47 | Clean clone → `docker compose up --build` → init → demo works; Level E | NOT RUN | Compose configs validated and images built (Phases 3C, 4 final); never run from a clean clone with a demo | The whole run |

**Feature review (Step 4).** Overview, Trace Explorer, span waterfall, metadata diagnostics, human failure review, trace-to-testset, golden datasets, deterministic evaluation, synthetic A/B experiments and experiment results all exist and are covered by the Phase 3–4 suites, which were re-run (§7).

**"Optimization Lab".** The term does not occur in any locked document (`grep -ri "optimization lab"` over `docs/`: no match). The locked scope for the A/B workflow is Phase 4 (closed and frozen). There is no locked Phase 5 requirement for new Optimization Lab functionality, so **none was built**: no recommendation engine, no winner logic and no new page. Step 5 was carried out as an audit of the existing experiment results (§8). **Decision needed from the architectural review** if any Optimization Lab feature is wanted: it needs a locked contract (scope, acceptance tests, ADR) and an explicit exception to the Phase 4 freeze.

**Smallest coherent work package delivered:** T43 completion, T47 execution, the R69 decision as ADR-27, and the defects found while verifying (R71 security, R72 test races).

| Item | Source files | Tests | Dependencies / risks |
|---|---|---|---|
| T43 backend | `backend/app/api/admin.py`, `app/core/ratelimit.py`, `app/services/ingestion/service.py` (unchanged) | `backend/tests/integration/test_phase5a_rate_limit.py` (new) | CR-08 per-process buckets (R09, R67) |
| T43 dashboard | `frontend/lib/server/backend.ts`, `lib/api/result.ts`, `lib/action-state.ts` (unchanged) | `frontend/tests/e2e/phase5a.spec.ts` (new), `playwright.config.ts` | second backend and dashboard during E2E (ports 8001, 3004) |
| T47 | `docker-compose.yml`, Dockerfiles, `scripts/dev/*` (unchanged) | clean-clone run (§5) | R43 (no published ingest), R73 |
| R69 | `docs/ADR.md` (ADR-27) | existing replay E2E | ADR-22, ADR-26 preserved |
| R71 | `frontend/proxy.ts` (matcher) | static test, replay E2E, raw probe | Next.js 16.4.3 action dispatch on rendered paths |

## 3. R69 decision and route-bypass verification (Step 3)

**ADR-27** records the decision of the instruction: `/experiments/new` (and the three ADR-22 form pages) are UI entry points, not an authorization boundary; page paths and action ids are not authorization controls. The boundary rests on the loopback bind and `Host` check, server-side validation in the action and the backend, the same-origin/CSRF checks, the server-side admin token, project scope, mock-only execution and bounded processing. These checks do not establish a verified human identity; no login is introduced. ADR-27 supersedes only the ADR-26 sentence "accepts its POST on `/experiments/new` only" in so far as it reads as a binding; ADR-26 gets a one-line pointer note, its text and ADR-22 are unchanged, and `proxy.ts` keeps its page list and every check.

**R68 regression re-run:** a POST carrying an action id to `/favicon.ico` → `405` in every origin variant; in the live replay E2E nothing is stored.

**Raw-path probe (new, R71).** A raw-socket client (no client-side path normalization) sent a POST with the `submitDataset` action id and an empty argument list (a dispatched action refuses the form, so nothing can be written) to 34 path variants × 3 origin variants (same-origin, no `Origin`, cross-origin) on the production build. Dispatch is visible as an RSC response (`text/x-component`).

- **Before the fix**, the matcher `/((?!_next/static|_next/image).*)` excluded every path *starting with* those strings. Same-origin POSTs to `/_next/static`, `/_next/staticx`, `/_next/static-x`, `/_next/static%2f..%2fdatasets`, `/_next/imagex`, `/_next/images` and `/_next/image/x` were dispatched without any proxy check (`404` + RSC; the action body ran, its digest in the server log). Cross-origin requests to them were aborted by Next.js ("Invalid Server Actions request", `500`); requests with a foreign `Host` (DNS-rebinding shape) or without `Origin` were not dispatched (`404 {}`). **Confirmed with a real write:** the replay E2E, extended to these paths, captured a genuine `submitExperiment` request and replayed it same-origin to `/_next/static`; the experiment count went from 0 to 1 while the response was `404`.
- **Fix:** `/((?!_next/static/|_next/image$).*)`, whole segments only; the compiled regexp in `.next/server/functions-config-manifest.json` is `…(?!_next\/static\/|_next\/image$)…`.
- **After the fix**, every look-alike gets the proxy's `405` in all three variants; `/_next/static/x` stays the static handler's `404` (text, no dispatch); `/_next/image` stays the optimizer's `400`. The only dispatches left are the write page itself in normalized forms (`/datasets`, `/./datasets`, `/datasets.rsc`, `/datasets?_rsc=1`), which pass through the proxy and get `403` without a same-origin `Origin`. Trailing-slash, double-slash and dot-segment forms answer `308` or `405`; case and percent-encoded variants `405`. A real static chunk is still served (`GET` `200`); a `POST` to it is the static handler's `500` with no log line and no dispatch (a plain `PUT` behaves the same).
- **Tests:** static matcher test updated; the replay E2E now asserts "nothing stored" before the status for six look-alike paths (failed before the fix, passes after).

## 4. T43 — request rate limit (PASS, phase-5a)

Backend, real PostgreSQL (`test_phase5a_rate_limit.py`, 6 tests, injected clock so `Retry-After` values are exact):

| Test | What it shows |
|---|---|
| route list | the 16 `(method, path)` pairs of `/api/v1` from the app's OpenAPI listing equal the expected set; every `/api/v1` router declares `require_admin`; none is hidden from the listing |
| `/api/v1` bucket | rate 0.5/s, burst 1: after one request every route (including `POST`/`PATCH` writes with an unreadable 300 KiB body) gets `429`, body `{"error":{"code":"rate_limited",…}}`, `Retry-After: 2`, `Cache-Control: no-store`; 413/422 never appear (the body is not read); no row is written in 8 tables; after 2 s a valid dataset write succeeds once, then the bucket waits again |
| auth-failure bucket | 2 failures/min exhausted by two `/api/v1` failures: every `/api/v1` route and `/v1/traces` with a valid key get `429`, `Retry-After: 30`; nothing written; after 30 s both succeed |
| shared bucket, other direction | three ingest failures → `/api/v1/overview` `429`, `Retry-After: 20` |
| ingest bucket | rate 0.25/s, burst 1: second distinct batch `429` (binary Status, `Retry-After: 4`), nothing stored; after 4 s accepted |
| no echo | rate-limited bodies, headers and logs contain neither the admin token nor the ingest key |

Mutation checks: removing `require_admin` from the datasets router fails 3 tests; disabling the `/api/v1` bucket fails 2 tests (source restored with `git checkout`, verified clean).

Dashboard path, live (`phase5a.spec.ts`, 2 tests): Playwright also starts a second backend (`127.0.0.1:8001`, same database, `/api/v1` bucket of 5 tokens, one more every 1000 s) and a dashboard on `127.0.0.1:3004` reading through it. (1) The `/datasets` form is loaded, the bucket drained by direct requests (`429` with the documented body), and the submitted dataset is refused with "Too many requests in a short time. Nothing was stored; retry shortly."; the main backend confirms it was not stored. (2) `/`, `/traces`, `/datasets` and `/experiments` show the fixed `rate_limited` state ("Backend rate limit reached", "Too many API requests in a short time (429).", "Retry after N s."), no other error kind, and neither the token nor the backend's own message in the HTML. Mutation: a dashboard build that does not decode `429` fails both tests (each run separately; serial mode skips the second after the first fails).

## 5. T47 — clean-clone compose smoke (PASS, phase-5a)

| Step | Command (from the clone) | Result |
|---|---|---|
| Clean clone | `git clone https://github.com/MuratElberKayaa/LLM-Insight.git` | `394920c`, 0 changes, no `.env`, venv or `node_modules` |
| Code under test | the six Phase 5A code files copied from the working tree (`proxy.ts`, its static test, `playwright.config.ts`, two E2E specs, the backend T43 module) | only `proxy.ts` enters an image; the R72 test fixes (§6) came later and touch neither image nor demo |
| Init (1) | `python -I scripts/dev/init_env.py` | `.env` created, values not shown, git-ignored |
| Port relocation (this machine only) | PostgreSQL `55433`, dashboard `3011`, host backend `8010`, compose project `-p llminsight-t47` | needed because the dev stack holds the defaults and the compose `name: llminsight` would otherwise reuse the dev containers and volume |
| `up --build` | `docker compose -p llminsight-t47 --profile app up -d --build --wait` | exit 0; postgres, backend, frontend healthy; published on `127.0.0.1:55433` and `127.0.0.1:3011` only |
| Init (2), in the backend container | `alembic current`; `/health/ready`; `python -m app.cli create-key --project t47-demo`; `python -m app.cli seed-prices` | `0005 (head)`; `ready`; project 1, key stored in the clone's `.env` by a helper that never printed it; 6 prices inserted |
| Host tools (R43) | venvs from `backend/requirements.lock` and `sdk/requirements-dev.lock` (`pip check` clean), host backend on `:8010` | under a short Temp path: the scratch path exceeded the Windows 260-character limit (R73) |
| Demo traffic | `python -m demo_app.web --drive` (environment from `.env`) | 5 traces, 13 spans exported, delivered, provenance synthetic |
| Golden dataset | `seed_golden_dataset.py --project 1 --approved-by t47-smoke --api http://127.0.0.1:8010` | 12 cases; the first attempt without `--api` reached the dev backend on `:8000`, which refused the clone's token (`401`, nothing written) (R73) |
| Browser demo on the compose dashboard (Edge, headless) | overview → traces → failing trace → review → decision → conversion → approved case → mock A/B → results | all steps passed (second attempt; the first stopped at a selector of my script after the review, decision and an empty dataset were stored; re-run on fresh demo traffic) |

Browser evidence (`docs/reports/phase-5a-screenshots/`): `t47-01-overview.png` (notice `synthetic_only`, no error state), `t47-02-traces.png` (the failing trace listed), `t47-03-trace-detail.png` (root → 2 children, diagnostics labelled "Automatic · Unverified · Not a human review"), `t47-04-review-decided.png` ("not a verified root cause"; decision "Hypothesis supported by the evidence"), `t47-05-test-case-approved.png` (empty input on load, refused without the approval box, stored after it), `t47-06-experiment-setup.png` ("mock only", "12 test cases → 24 simulated calls"), `t47-07-experiment-results.png` ("Experiment #1 ran in mock mode and stored 24 results (24 SYNTHETIC, 0 real)"; SYNTHETIC banner; quality not compared and no better version named because G12 timed out under v2; cost not compared because v2's total is unknown; 7/11 vs 4/10 with denominators; identical 12 case ids under both versions).

**Teardown:** the clone's host backend stopped; `docker compose -p llminsight-t47 --profile app down -v --rmi local` removed exactly its containers, network, volume and two images. `llminsight-postgres-1` (up 37 hours) and `llminsight_pgdata` were untouched. The documented procedure for default ports is in `DEMO_SCRIPT.md` ("Clean-clone setup").

**Interpretation:** T47 reads `docker compose up --build`; the dashboard needs the documented opt-in `--profile app` (Phase 3C decision; the default still starts PostgreSQL only), and ingest needs the host backend (R43). Both are the repository's documented init, as the Phase 5 acceptance requires.

## 6. R72 — timing-dependent tests (fixed, no product change)

- **Trace-tree E2E** (`traces.spec.ts`, "orphans, parent cycles and equal start times"): the first full run had 54 passed, 1 failed; repeated alone it failed 5 of 8 runs, and 8 of 8 with the Phase 4 `proxy.ts` restored, so the matcher change is not the cause. The page snapshot showed the "Loading trace…" fallback: `evaluateAll` does not wait, and React reveals the streamed tree after `load`. Fix: wait for the full tree (`toHaveCount(spans)`) before each read; `uiTraceIds` waits for the trace table (same latent race). 10 of 10 passes; full suite 55/55.
- **SDK live diagnostics** (`test_retry_then_success_is_a_recovered_call`): 1 failed of 350, reproducible. The test expected evidence in attempt order; `ARCHITECTURE.md` §5.6 orders evidence by (start ns, span id), and the two mock attempts can share a start time on Windows (failing runs had the evidence in span-id order). Fix: expect the documented order. 6 of 6 passes; full suite 350/350.

## 7. Commands and outcomes (after the last code change)

| Command (from) | Outcome | Exit |
|---|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q` (backend, real PostgreSQL) | **1726 passed**, 0 failed, 0 skipped (1720 + 6 new; includes the migration tests) | 0 |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q` (sdk) | **350 passed**, 0 failed, 0 skipped (before the R72 fix: 349 passed, 1 failed) | 0 |
| `npm test` (frontend) | **311 passed**, 0 failed, 0 skipped | 0 |
| `npx playwright test` (frontend, real backend) | **55 passed**, 0 failed, 0 skipped (53 + 2 new; before the R72 fix: 54 passed, 1 failed) | 0 |
| `npm run typecheck` / `npm run lint` / `npm run build` | clean / 0 warnings / built | 0 / 0 / 0 |
| `npm run check:bundle` | 632 files, 23 in the browser bundle, 1 secret value + 5 names searched, 0 findings | 0 |
| `npm audit` | 0 vulnerabilities | 0 |
| `ruff check .`, `ruff format --check .` (backend; sdk) | all passed; 140 / 29 files formatted | 0 |
| CI dev-script lint (`ruff check` / `format --check` on `seed_trace_fixtures.py`, `e2e_fixtures.py`) | passed | 0 |
| `python -m mypy src`; `mypy --config pyproject.toml ../demo_app` (sdk) | no issues (8 / 3 files) | 0 |
| `pip check` (backend; sdk) | no broken requirements | 0 |
| `alembic current` / `alembic check` (backend) | `0005 (head)` / no new operations | 0 |
| `docker compose config --quiet`; `--profile app config --quiet` | valid | 0 / 0 |
| T47 clean-clone run (§5) | PASS | — |
| Credential scan (§9) | 0 hits; control run detects planted values | 0 |
| `python -I scripts/verify_phase0.py --quiet`; `git diff --check` | recorded in `PHASE_STATUS.md` (final checks) | — |

Database integration evidence: the backend integration tests and the T43 module run against disposable `*_test` databases on the compose PostgreSQL; the E2E suite creates and deletes its own run projects in the dev database (teardown `remaining: []`; after the runs the dev database held its original 2 projects, 0 datasets, 0 experiments).

## 8. Optimization Lab correctness audit (Step 5, no change)

The existing report (`backend/app/services/experiments/report.py`) and its tests already enforce every invariant of the instruction: identical case ids under both versions (T38, also in PostgreSQL); variant identity by `prompt_version`, enforced by a trigger; rule versions explicit (`llminsight.rule.v1`); deterministic outcomes; exact `Decimal` cost with unknown never 0 and cost compared only when both versions are fully priced; latency reported per version only, from recorded durations, no difference claimed; SYNTHETIC provenance on every mock result (T39); not-evaluated results outside the denominator (T40); quality compared only on paired cases, with no better version named when any case is unpaired or the run unfinished; limitations listed from the backend. No LLM judge, semantic score, invented usage, cost, latency or real provenance exists. The T47 run shows the same behaviour on a clean clone (§5). Any "recommendation" beyond this report needs a locked contract (§2).

## 9. Security verification

- Phase 1–4 guarantees re-run through the suites: metadata-only telemetry (no prompt or completion), server-only admin token (T42: bundle scan 0; no token in any E2E response, also in the rate-limited states), loopback-only binds and `Host` checks, project isolation (one `404`), safe review and test-case writes (approval, redaction), RE2-only regexes, the restricted JSON Schema subset, budget and run bounds, mock-only execution (real refused).
- New: R71 fixed (§3); every `/api/v1` route is rate-limited before its body is read (§4).
- **Credential scan:** the 9 local secret values of the dev `.env`, `frontend/.env.local` and the T47 clone's `.env` (with the database password part and the ingest key) plus OpenAI, GitHub, AWS, Slack, private-key, long `Bearer` and ingest-key patterns, over all 746 blobs reachable from all refs (20 commits), all commit and tag messages, and the 436 files to be committed (tracked and new): **0 hits**; `.env` and `.env.local` are not tracked. The test dummies `t…t` / `w…w` and the documented `Bearer $LLMI_ADMIN_TOKEN` are exempt from the `Bearer` pattern. Control run on a throwaway repository with a planted synthetic value and an OpenAI-shaped key: detected in the blob, the file and by pattern. Values were never printed.
- **R66** (duplicate on a lost response) is retained as an accepted local MVP limitation: no locked contract requires durable idempotency for mock runs; nothing retries automatically. **R67** (process-local bounds) unchanged; no distributed safety is claimed. Real-provider mode stays disabled; public deployment stays prohibited.

## 10. Risks and open issues

| ID | State after 5A |
|---|---|
| R69 | Resolved (ADR-27) |
| R71 | New, resolved: matcher prefix bypass (Low; same-origin only, as R68) |
| R72 | New, resolved: two test races |
| R73 | New, accepted: the clean-clone demo needs host venvs and `--api` on relocated ports; Windows long paths |
| R43 | Accepted; exercised by T47 |
| R66, R67 | Accepted, unchanged |
| R09, R23 | Unchanged: per-process limiter and counters |

Not done in Phase 5A (remaining Phase 5 work): the full 48-row PASS/FAIL/SKIP report and the 0-FAIL gate on the 42 critical tests; `pip-audit` and `bandit`; the benchmark (N, hardware, method, p50/p95); README quickstart, final `DEMO_SCRIPT.md`, `FINAL_DELIVERY_REPORT.md`, project pitch; Linux CI of the Phase 5A commit (needs a push); the `phase-5` tag.

## 11. Files

Changed: `frontend/proxy.ts` (R71), `frontend/tests/unit/security-static.test.ts`, `frontend/tests/e2e/phase4d.spec.ts` (R71 paths, order of assertions), `frontend/tests/e2e/traces.spec.ts` (R72), `frontend/playwright.config.ts` (backend :8001, dashboard :3004), `sdk/tests/integration/test_diagnostics_live.py` (R72), `docs/ADR.md` (ADR-27, ADR-26 note), `docs/SECURITY.md`, `docs/ARCHITECTURE.md` (§5.8 note), `docs/SOURCES_AND_RISKS.md` (R43, R66–R69 updated; R71–R73 new), `docs/TEST_MATRIX.md` (T43, T47), `docs/DEMO_SCRIPT.md` (clean-clone setup), `docs/PHASE_STATUS.md`.
Added: `backend/tests/integration/test_phase5a_rate_limit.py`, `frontend/tests/e2e/phase5a.spec.ts`, `docs/reports/phase-5a-screenshots/t47-0{1..7}-*.png`, this report.
No production backend, SDK or schema change; the only production change is the `proxy.ts` matcher.

## 12. Verdict

**PASS for the Phase 5A scope**, on the evidence above: T43 and T47 PASS (phase-5a) against their locked definitions; R69 decided (ADR-27); R71 found and fixed; all suites green. The Phase 5 gate is **not** claimed. Awaiting architectural review; no `phase-5` tag, no push.
