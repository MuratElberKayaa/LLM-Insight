# Phase 4D — Experiment Dashboard & Integration Acceptance

- **Date:** 2026-10-26 (UTC; the machine's local clock is UTC+3).
- **Instruction (user, Phase 4D):** deliver the user-facing, mock-only synthetic A/B experiment workflow on the existing deterministic evaluation backend; preserve the locked architecture, privacy and human approval; re-verify the Phase 4C guarantees, including the mutation that only the database trigger caught; run the live end-to-end loop on approved synthetic fixtures; stop after Phase 4D.
- **Delivered:** experiment list, setup, results and per-case pages; one new dashboard write (ADR-26); two additive read-only endpoints; bounded execution (concurrency, deadline, controlled evaluation failure); a Phase 4C money-notation fix; the full live loop verified against PostgreSQL; T48 re-checked for Phase 4.
- **Not done (by instruction):** no real OpenAI or paid call, no public deployment, no `phase-4` tag, no push, no Phase 5, no Optimization Lab feature. **Linux CI was not run** (nothing was pushed); every result below is from the local Windows machine.
- **Untouched:** the home-directory repository, global Git/Node configuration, credentials, the PostgreSQL container and volume, the schema (no migration; dev DB stays `0005`), the price catalogue, the SDK, all dependencies and lock files, the CI workflow, `frontend/AGENTS.md`.

## 1. Baseline verification (before any change)

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own repository) |
| Branch / HEAD / tree | `main`, `5949ed7` (Phase 4C), clean; `7fd1528` (4B) and `888b188` (4A) before it |
| Tags | `phase-0` … `phase-3` present and unchanged at the end; no `phase-4` |
| PostgreSQL | `llminsight-postgres-1` healthy on `127.0.0.1:55432`; dev DB `0005 (head)` |
| Dev DB | 2 projects, 28 traces, 846 spans, 43 usage rows, 6 prices; 0 datasets, reviews, cases, experiments, results. Content checksum of every table recorded |
| Backend / dashboard | not running at the start (started for the live runs: backend `python -m app` on `127.0.0.1:8000`, dashboards by Playwright) |
| Real-LLM settings | `LLMI_ALLOW_REAL_LLM=false`, `LLMI_PROVIDER_MODE=mock`, `OPENAI_API_KEY` empty (values not printed) |
| Recorded baseline | backend 1705, SDK 350, frontend unit 274, E2E 48 passed (Phase 4C report §9) |

## 2. Contract inspection and gaps (documented before implementing)

Read: `CLAUDE.md`, `frontend/AGENTS.md`, `MVP_MASTER_PLAN.md` (§E, §F, §H Phase 4), `PRD.md`, `ADR.md` (ADR-22…25), `ARCHITECTURE.md` (§4.11–4.13, §5, §5.3, §5.7–5.9), `SECURITY.md`, `TEST_MATRIX.md`, `PHASE_STATUS.md`, `SOURCES_AND_RISKS.md`, the 4A–4C reports; and the implementation: `api/{experiments,datasets,reviews,human_input,admin}.py`, `services/experiments/*`, `repositories/{experiments,pricing}.py`, `models/experiment.py`, the Phase 4B Server Actions, `proxy.ts`, `request-guard.ts`, `lib/server/backend.ts`, the decoders and the E2E harness.

What the existing contract already provided: datasets with case counts (`GET /datasets`), cases by keyset (`GET /datasets/{id}/cases`), `POST /experiments` (mock only, budget pre-check, atomic run), `GET /experiments/{id}` (`llminsight.experiment_report.v1`).

| # | Gap | Resolution (additive, no column, no change to existing bodies or codes) |
|---|---|---|
| G1 | No way to list a project's experiments | `GET /api/v1/experiments` |
| G2 | Which prompt versions the mock answers, which models are priced: unknown to the UI ("select approved variants", "do not invent variant parameters") | `GET /api/v1/experiment-options`: versions from the mock script, models from `model_prices` in force, limits |
| G3 | Concurrent runs unbounded (each holds a DB connection) | 4 per process, `429 experiments_busy`, refused not queued |
| G4 | No wall-clock bound per run (R60: up to 400 evaluations in one request) | 15 s deadline, `409 experiment_time_limit`, nothing stored |
| G5 | An unexpected engine exception escaped as Starlette's plain-text 500, its message logged | fixed `500 internal_error`, exception type logged only, nothing stored |
| G6 | ADR-22's write list is exhaustive | ADR-26: one more Server Action, mode constant `mock` |
| G7 | No single-case read | none needed: `GET /datasets/{id}/cases?after=<id−1>&limit=1`, accepted only for the exact id |

Found while reading (fixed): **report money used `str(Decimal)`**, which writes amounts below 1e-6 as `1.500E-7`, not the decimal string `ARCHITECTURE.md` §5.2 requires; the dashboard decoder would have rejected such a report. Now `format(v, "f")`, with unit and integration regressions.

## 3. Experiment UI architecture

| Page | Content | Writes |
|---|---|---|
| `/experiments` | project's experiments newest first (dataset, A vs B, model, mode, state, results, start) | none |
| `/experiments/new` | datasets with case counts, options, execution-mode panel, review summary, confirmation | the one Server Action, on submit only |
| `/experiments/{id}` | SYNTHETIC banner, summary, warnings, verdict panel, comparison table, outcome chart, per-case table, limitations | none |
| `/experiments/{id}/cases/{caseId}` | both recorded outcomes with `evaluation_detail`, duration/tokens/cost/provenance, the stored case (rule type and version, input, approved expected output, origin trace and review) | none |

- Navigation: "Experiments" in the side navigation (was "Not built yet"); a dataset page links to the setup with `?dataset=<id>` (validated, only preselects an entry of the listing).
- Setup: choices only from the backend (datasets; scripted prompt versions; priced models); empty and >200-case datasets are disabled; A = B is flagged; optional budget parsed exactly (≤ 10 decimal places, refused when a JSON number would round it); a live "review before running" summary; an unticked confirmation box; "Run mock experiment" disabled while pending, with a synchronous in-flight guard (a double click starts one run, verified live). No mode control exists.
- Results: the verdict panel repeats the backend's decision. Partial quality → "No better version is named" with the backend's reason; incomplete cost → "Not compared"; latency per version only. Per-version cost is a total only when every result is priced, otherwise "Total unknown" with the known part, counts and reasons; it is never shown as $0. Pass rates are shown with their numerator and denominator.
- Chart (validated with the dataviz palette script on `#15171c`): outcome counts per version, drawn only for a finished run on identical case ids, where both bars have the same total. Series-1 = passed, critical = failed (deutan ΔE 25.7), neutral gray = not evaluated (ΔE 8.5). Green/red was rejected (deutan ΔE 4.1). Legend, tooltip and a data table twin; no rate or cost chart.
- Decoding: the report decoder enforces the report's own definitions and rejects the whole response on a mismatch (`ARCHITECTURE.md` §5.10); 17 refusal cases are unit-tested.
- Other changed UI: the stale "Not evaluated yet (Phase 4C)" copy on dataset pages now reads "Evaluated only by experiments"; `SubmitButton` takes a pending label ("Running…").

## 4. Backend endpoints used

| Endpoint | Method | Used by | Status |
|---|---|---|---|
| `/api/v1/projects` | GET | selector | existing |
| `/api/v1/datasets` | GET | setup | existing |
| `/api/v1/datasets/{id}/cases` | GET | case page (`after`, `limit=1`) | existing |
| `/api/v1/experiment-options` | GET | setup | **new, additive, read-only** |
| `/api/v1/experiments` | GET | list | **new, additive, read-only** |
| `/api/v1/experiments` | POST | `submitExperiment` only | existing contract; now bounded |
| `/api/v1/experiments/{id}` | GET | results, case page | existing (money fix) |

## 5. Security guarantees (verified)

| Guarantee | Evidence |
|---|---|
| Server-only token, no client credentials | bundle scan 632 files / 0 findings; static tests (token read in one module; no `NEXT_PUBLIC_`, storage or raw HTML); live E2E scans every response body: 0 leaks |
| Loopback only, Host validation | unchanged `proxy.ts` guard; backend binds `127.0.0.1`; dashboard backend URL loopback-only |
| Origin / CSRF | live E2E: cross-site, origin-less and header-less POSTs to `/experiments/new` → `403`; POST to `/experiments` and `/experiments/{id}` → `405`; nothing stored |
| Path and method allowlists, no generic proxy | `PATH_RE` + `WRITE_ROUTES` (one new POST); unit test: experiment paths allowed, `experiments/42/cases` and `PATCH /experiments` refused before any fetch; still no route handlers (static) |
| Exactly five writes in three action modules | static test pins `lib/actions/{datasets,experiments,reviews}.ts` and the `sendJson` callers |
| Mock only | parser constant (crafted `mode=real` still sends `mock`, unit + mutation); options decoder refuses any other mode; backend `409` for `mode: real` (integration and live E2E) |
| Explicit writes only | opening setup/results/list writes nothing (E2E checks the list stays empty); unconfirmed submit refused; double click → one experiment |
| Strict input/output validation | form re-validation in the action; backend re-validation; strict report/list/options decoders |
| Safe text rendering | hostile dataset name rendered escaped (unit); all stored strings as React text |
| Project isolation | foreign project: results page `not_found`, case page error, API `404`, empty list; a foreign dataset id in `POST /experiments` → `404` (live E2E; integration list scoping, killed mutation) |
| Safe URL state | ids format-checked before any request; `?dataset=` only preselects a listed dataset; no secret in any URL |
| Bounded workload | 4 concurrent / 15 s / 200 cases; slots released after 201, 404, 409 and 500 (integration) |

Public deployment remains prohibited (ADR-08, ADR-22, R54).

## 6. Real end-to-end workflow (live, nothing mocked)

`frontend/tests/e2e/phase4d.spec.ts`, browser (Edge) → Next.js production build → FastAPI on the dev DB → PostgreSQL. Run-owned SYNTHETIC projects (global setup), deleted by the teardown.

1. Opened a failing seeded trace; the automatic diagnostics panel is labelled "Automatic · Unverified · Not a human review" and holds no form.
2. Recorded a human failure review (`schema_violation`, evidence, hypothesis, reviewer).
3. Recorded the decision (`closed`, `hypothesis_supported`).
4. On the conversion page created dataset `e2e-<run>-golden-ab` and approved a test case: typed input = the approved SYNTHETIC fixture input `SYNTHETIC G02: …`, the fixture's JSON Schema rule and an explicit expected output. Stored with `origin_trace_id` and `origin_review_id` (checked through the API). The scripted mock answers only golden inputs (R65), which is why the typed input is a golden one.
5. Added the other 11 golden fixtures through the API, approved by the test (as `seed_golden_dataset.py` does).
6. Opened the dataset (12 cases) → "Set up a mock experiment" (dataset preselected); the experiment list was still empty.
7. Chose `golden-prompt-v1` / `golden-prompt-v2` and `gpt-5-mini`; A = B flagged; an unconfirmed submit refused; confirmed and double-clicked → exactly one experiment, 24 results (24 SYNTHETIC, 0 real).
8. Results page: SYNTHETIC banner; quality `partial`, no better version named (G12 timed out under v2); cost not compared; B "Total unknown"; outcome chart; comparison table equal to the API report; 12 case rows with identical ids under both versions; every cell's outcome equal to the report row.
9. **PostgreSQL directly** (`e2e_fixtures.py experiment-counts`, read-only, refuses experiments outside the run): v1 7 passed / 4 failed / 1 not evaluated, v2 4 / 6 / 2, both on the dataset's 12 case ids, 24 `synthetic`, `mode = mock`, finished. Report pass rates 0.636364 (7/11) and 0.4 (4/10). These are the documented Phase 4C golden outcomes.
10. Case page of the human case: A failed (`schema_violation: … required at (root)`), B passed, both `SYNTHETIC`; rule `json_schema` version 1; origin trace and review #; input as stored; "model output is not stored".
11. A second dataset (G02 + G03) with budget 0.01 run by keyboard only: quality `complete`, "version B (golden-prompt-v2) passed more" with "no significance test"; cost complete `B − A`; PostgreSQL v1 0/2/0, v2 2/0/0 on identical ids.

Screenshots: `reports/phase-4d-screenshots/phase4d-01-trace-diagnostics.png`, `-02-experiment-setup.png`, `-03-experiment-results.png`, `-04-case-detail.png`, `-05-complete-comparison.png`, `-06-results-mobile.png` (390 px).

Dev DB after every run: content checksums of all 11 tables identical to the baseline (all run-owned rows removed by the teardown); backend log 0 tracebacks, 0 occurrences of case content.

## 7. Rule engine safety regression and the Phase 4C mutation follow-up

Re-verified by the full backend suite on the final code: rule contract `llminsight.rule.v1` and approved types only (`test_case_rules.py`), no Python `re` fallback and RE2 pathological patterns bounded (`test_safe_regex.py`, `test_evaluation_boundaries.py`), JSON Schema reference restrictions and no remote loading with sockets blocked (`test_safe_schema.py`), schema/output size limits and evaluation budgets (`test_evaluation_engine.py`), determinism, exact numbers (`test_exact_json.py`, `test_phase4c_integrity.py`), missing expected output → `passed = NULL` and outside the denominator (T40), review decision freeze, the partial unique index, append-only results and experiment triggers (`test_migrations.py`, 45 tests incl. the full up/down cycle).

**Follow-up of the surviving Phase 4C mutation** (the API's eligibility set accepting `inconclusive`):
- `test_bypassed_eligibility_check_still_ends_in_a_controlled_409`: with the API check bypassed (an `inconclusive` review accepted), the `test_cases_review_eligibility` trigger still refuses the insert and the API answers **`409 review_not_eligible`**, not a 500; no row stored.
- `test_bypassed_freeze_check_still_ends_in_a_controlled_409`: with the API freeze check bypassed, the `failure_reviews_decision_frozen` trigger refuses the change, the API answers **`409 review_decision_frozen`**, the review is byte-identical.
- Remaining limitation: the API-level eligibility tests alone do not kill that mutation; the database trigger is the deciding guard, and its mapping to a domain error is now pinned by these regressions.

Phase 4D mutation checks (scratch harness; each file restored byte for byte and md5-verified): **16/16 killed.** Backend: report money via `str()`, deadline checkpoint never called, no concurrency bound, slot not released, evaluation error escapes, options offer `real`, options list future prices, list not project-scoped. Frontend: mode read from the form, confirmation not required, winner accepted on partial evidence, NULL in the pass-rate denominator, incomplete cost shown as a total, verdict shown on partial evidence, setup page not a write page, chart on non-identical case ids.

## 8. Tests and results (final code; commands from the named directory)

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1719 passed**, 0 failed, 0 skipped, exit 0 (256 s; 1 pre-existing Starlette/httpx deprecation warning) |
| same (sdk) | **350 passed**, 0 failed, 0 skipped, exit 0 (92 s) |
| `npm run typecheck` / `npm run lint` (frontend) | exit 0 / exit 0 (0 warnings) |
| `npm test` (frontend) | **310 pass**, 0 fail, 0 skipped, exit 0 |
| `npm run build` / `npm run check:bundle` | exit 0 / 632 files scanned (23 in the browser bundle), findings 0 |
| `npm audit --omit=dev` / `npm audit` | 0 vulnerabilities / 0 vulnerabilities |
| `npx playwright test` (real backend on :8000, Edge) | **52 passed**, 0 failed, exit 0 (1.8 min); teardown `remaining: []` |
| `ruff check .` / `ruff format --check .` (backend, sdk) | exit 0 / exit 0 |
| `python -m mypy src` and `--config pyproject.toml ../demo_app` (sdk, strict; the only strict mypy configuration) | no issues (8 and 3 files) |
| `python -m pip check` (backend, sdk) | no broken requirements |
| `alembic current` / `alembic check` (dev DB) | `0005 (head)` / "No new upgrade operations detected" (no migration in Phase 4D) |
| migration cycle (disposable DBs) | `test_migrations.py` within the backend run: base ↔ head, data preservation, refused up/downgrades |
| `docker compose config --quiet` / `--profile app config --quiet` | exit 0 / exit 0 |
| `python -I scripts/verify_phase0.py --quiet` | see §13 |
| Credential/privacy scan (426 tracked and new files incl. this report and screenshots, raw bytes) | 0 occurrences of the 3 distinct local secret values; 0 key-shaped strings (`sk-…`, `lli_…`, bearer, private key) |
| `git diff --check` | see §13 |

**New and changed tests.** Backend +14 (1705 → 1719): new `test_phase4d_api.py` 11 (list, options ×2, concurrency, slot release, deadline, controlled 500, money notation, the two mutation follow-ups); `test_experiment_runner.py` +2 (checkpoint, scripted versions); `test_experiment_report.py` +1 (money); `test_health.py` route set updated for the two documented additive endpoints. Frontend unit +36 (274 → 310): new `phase4d.test.tsx`; `security-static.test.ts` updated for the fifth write; `phase4b.test.tsx` copy assertion updated. E2E +4 (48 → 52): new `phase4d.spec.ts`; `phase4b.spec.ts` copy assertion updated and keyboard test synchronized (below).

**A flaky Phase 4B E2E test, root-caused.** "Phase 4B pages: keyboard reachable controls…" failed once in the first full run. Repetition: 2/8 failures on the 4D build and **5/8 on the unmodified 4C build** (changes stashed, rebuilt), so it is pre-existing. A probe (MutationObserver plus `activeElement`) showed `document.activeElement` = `BODY` right after `focus()`: streamed page content sits in a hidden container (`<div hidden id="S:1">`) until React reveals it ~0.6–1.2 s after `load`, and Playwright's `locator.focus()` does not wait for visibility, so the test focused a hidden copy. It is not a product defect (hidden content cannot be used). Fix: wait for visibility before focusing (also in the new 4D keyboard test); then 12/12 repeated runs passed, including the cold first run, and the full suite passed.

### Test matrix (exact `TEST_MATRIX.md` criteria)

| ID | Status | Phase 4D evidence |
|---|---|---|
| T33★, T34★⛔, T35★⛔ | PASS (phase-4b), re-run | backend suite; the browser flow of §6 (review, approval required, redaction) |
| T36★, T37★ | PASS (phase-4c), re-run | engine suites; G01/G05 pass, G02 v1 `schema_violation` with detail shown on the case page |
| T38 | PASS (phase-4c), re-run | identical case ids under both versions on the results page, in the API and **in PostgreSQL directly** |
| T39★ | PASS (phase-4c), re-run | 24/24 `synthetic` in PostgreSQL; decoder refuses a mock report with a real result |
| T40 | PASS (phase-4c), re-run | G11 not evaluated, outside the denominator (7/11, 4/10) on the page and in PostgreSQL |
| T41★ | PASS (phase-3c), re-run | escaped hostile dataset name (unit); all new pages render stored strings as text |
| T42★⛔ | PASS (phase-3c), re-run | bundle scan 0 findings; 0 token in E2E responses |
| **T48★** | **PASS (phase-4d)** | Phase 4 re-check (3→4,5): SYNTHETIC banner and `synthetic` badge on every experiment page; "24 synthetic, 0 real"; per-result `SYNTHETIC`; no synthetic experiment labelled real (unit + live E2E) |

Relevant T48 browser requirements met: provenance shown as text beside a swatch (never color alone), mixed/unknown never counted as real (decoder label check), synthetic experiments never labelled real.

## 9. Risks (`SOURCES_AND_RISKS.md`)

| Risk | Status after Phase 4D |
|---|---|
| R60 regex worst case, synchronous runs | Accepted, **mitigated**: 15 s deadline, 4 concurrent runs; a dataset needing more than 15 s is refused, not run |
| R61 JSON Schema subset | Accepted, unchanged |
| R62 mock-only synthetic results and costs | Accepted; every experiment page now carries the SYNTHETIC banner and badges; incomplete totals shown as unknown |
| R63 no experiment page | **Resolved** |
| R64 decimals JavaScript would round | Accepted; also applies to the budget field |
| R65 mock knows only golden inputs | Accepted; the UI offers only scripted versions; a reviewed case is evaluable only with a golden input (as in the live E2E) |
| R66 (new) | a lost answer after sending → "outcome unknown"; a repeated explicit submission creates a second, identical experiment (no idempotency key) |
| R67 (new) | experiment bounds are per process and reset on restart (like CR-08) |

Still open from earlier: R54 (no login, loopback trust), R55 (self-declared identities; D8 deferred), R56 (heuristic redaction).

## 10. Remaining security limitations

- No user authentication or per-user authorization: any local process can start mock runs through the dashboard or the admin API (R54); public or shared deployment stays prohibited (`SECURITY.md` §7).
- `approved_by` / `created_by` remain self-declared labels (R55).
- Experiment bounds and the rate limit are in-process (CR-08, R67).
- Runs are synchronous HTTP requests (R60, R66); a background worker is post-MVP.
- `style-src 'unsafe-inline'` remains for the chart library (R38); no stored string is placed in a style.

## 11. Files

New backend test: `tests/integration/test_phase4d_api.py`. Changed backend: `app/api/experiments.py`, `app/main.py`, `app/repositories/{experiments,pricing}.py`, `app/services/experiments/{mock_provider,report,runner}.py`, `tests/unit/{test_experiment_report,test_experiment_runner,test_health}.py`. New frontend: `app/experiments/{page,loading}.tsx`, `app/experiments/new/page.tsx`, `app/experiments/[experimentId]/{page,loading}.tsx`, `app/experiments/[experimentId]/cases/[caseId]/page.tsx`, `components/experiments/{experiment-form,experiment-report,values}.tsx`, `components/charts/outcome-chart.tsx`, `lib/actions/experiments.ts`, `lib/experiment-form.ts`, `lib/server/experiments.ts`, `lib/api/experiment-{types,schema}.ts`, `tests/unit/phase4d.test.tsx`, `tests/e2e/phase4d.spec.ts`. Changed frontend: `lib/{action-state,request-guard}.ts`, `lib/api/result.ts`, `lib/server/backend.ts`, `lib/actions/datasets.ts`, `components/{side-nav,datasets/case-list,reviews/form-parts}.tsx`, `app/datasets/page.tsx`, `app/datasets/[datasetId]/page.tsx`, `README.md`, `tests/unit/{security-static,phase4b}.test.ts(x)`, `tests/e2e/phase4b.spec.ts`. Script: `scripts/dev/e2e_fixtures.py` (read-only `experiment-counts`). Docs: `ADR.md` (ADR-26), `ARCHITECTURE.md` (§5 rows, §5.8 note, §5.9 fix note, new §5.10, §7), `SECURITY.md`, `TEST_MATRIX.md`, `SOURCES_AND_RISKS.md`, `DEMO_SCRIPT.md`, `PHASE_STATUS.md`, this report and `reports/phase-4d-screenshots/`.

## 12. Phase 4 closure prerequisites (not done here)

1. Architectural review of Phase 4C and 4D (ADR-22…26, the bounds and the two additive endpoints).
2. Phase 4 **feature freeze** and a closure report.
3. A CI run on GitHub (Linux) of the closure commit; this needs a push, which was not done.
4. The annotated `phase-4` tag, only on a closure commit whose own CI run passed (not created).
5. Optional, each needing its own approval: a real-provider executor (budget, `LLMI_ALLOW_REAL_LLM`, per-run approval), decision-actor history (D8), a background experiment worker.

## 13. Final checks and verdict

After this report and all documentation were written: `python -I scripts/verify_phase0.py --quiet` → **737 PASS, 0 FAIL**; `git diff --check` → clean (exit 0; line-ending notices only, `core.autocrlf=true`), and no trailing whitespace in the new files. Backend and frontend source did not change after their final runs in §8 (only E2E synchronization and documentation; the full E2E suite was re-run after the last E2E change).

**Verdict: PASS for the Phase 4D scope** on the evidence above. No tag, no push, no real or paid call; Phase 5 not started. Awaiting architectural review.
