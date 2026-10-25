# Phase 3C — Model Comparison, Project Selector and Frontend Release Readiness

- **Date:** 2026-10-25 (UTC; the machine's local clock is UTC+3).
- **Scope (user instruction "Phase 3C"):** model comparison page, bounded project listing and project selector, R40 (models/providers on the trace list), filters and optional auto-refresh, R41 (reproducible E2E fixtures), security/UX regression (T41, T42, T45, T46, T48), compose frontend service and CI workflow, full regression, documentation and commit.
- **Not done (by instruction):** `phase-3` tag, Phase 4, real OpenAI calls, public deployment, push, history rewrite. No backend provider/service *filter* was added (R40 asked only for the summary).
- **Untouched:** the home-directory repository, the global Git and Node configuration, the external container on port 3000 (`docker-flowise-1`), the native PostgreSQL on 5432, the `llminsight-postgres-1` container and its volume (same container id and start time before and after the compose test), provider pricing, the database schema (no migration).

## 1. Baseline (Task 1)

| Check | Result |
|---|---|
| Git root / branch / HEAD / tree | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi`, `main`, `1554a93` (Phase 3B), clean |
| Tags | `phase-0`, `phase-1`, `phase-2` (annotated, on `75ff8c3`); no `phase-3` |
| Remote | `origin` = `https://github.com/MuratElberKayaa/LLM-Insight`; `git fetch` → `origin/main` = `1554a93` = local `main`, so `git branch --set-upstream-to=origin/main main` was run (no history change, nothing pushed) |
| Services | `llminsight-postgres-1` `Up (healthy)` on `127.0.0.1:55432`; backend started with `python -m app` → `/health/ready` `{"status":"ready"}`; DB `0002 (head)`; port 3000 = external `docker-flowise-1` (left alone); 3001–3003 free |
| Contracts read | `CLAUDE.md`, `frontend/AGENTS.md`, master plan Phase 3, `CLAUDE_PHASE_PROMPTS.md` §4, `ARCHITECTURE.md` §4–§8, `SECURITY.md`, `METRICS_CATALOG.md`, `TEST_MATRIX.md`, `PHASE_STATUS.md`, reports 2-final, 3a, 3b |

**ADR review.** No conflict: the project listing uses the existing CR-06 admin token (which already reads every project; no new permission, no `403`), adds no column or table (no ADR-21 change), and all API changes are additive response fields. `METRICS_CATALOG.md` M15 gains token fields (M08/M09/M14 blocks reused, no formula changed).

## 2. Backend API changes (all additive, no schema change)

| Change | Files | Contract |
|---|---|---|
| `GET /api/v1/projects` | `app/api/projects.py`, `app/repositories/projects.py`, `app/main.py` | `require_admin` before validation; `limit` 1–200 (default 100), `422` naming `limit`; one read-only snapshot; `SELECT id, name, created_at … ORDER BY id LIMIT limit+1`; `{items[{id,name,created_at}], truncated, limit}`; `no-store`; JSON `405` for other methods; no key material |
| `usage.models` on trace list items and detail `trace` (R40) | `app/repositories/traces.py` (`trace_models_stmt`), `app/api/traces.py` | `{items[{provider, requested_model, response_model, calls}], distinct, truncated}`; every distinct combination, largest first, ≤ 10, `distinct` counts all; `NULL` models stay `null`; one grouped statement per page over the page's trace ids (window functions), same snapshot; list statement, plan, cursor and window unchanged |
| Per-group `tokens` and `usage_source` on `models/compare` | `app/repositories/metrics.py` (`_token_columns`, `_usage_source_columns` shared with the overview), `app/services/metrics.py` | Overview M08/M09 block (unknown sums `null`, unknown counts, subsets with `calls_reporting`) and M14 counts per group |

Documentation: `ARCHITECTURE.md` §5 table, §5.1 note, §5.2 note, new §5.5, §7, §8; `METRICS_CATALOG.md` M15 note.

## 3. Frontend features (Tasks 2–5)

### 3.1 Model comparison — `/models`
- Server component; one `GET /api/v1/models/compare` per render (`lib/server/models.ts`) plus the project listing.
- **Table** (cards below `md`): group (provider, requested → response model, prompt version; `null` shown as "not reported"), provenance badges per group (next to the group so it stays visible), attempts, error rate with failed count (✕ + text), latency p50/p95/p99 with `n` and low-sample flags, tokens in/out with unknown counts, subsets only as reported, estimated usage flagged, known cost (exact Decimal in `data-exact`, `$` formatting by BigInt; "unknown" when nothing priced, never `$0`; "Incomplete" when some calls are unpriced) with priced/unknown counts and reasons.
- **Charts** (Recharts, client; the Phase 3A validated palette: series-1 = not failed, critical = failed with ✕ + text): attempts per group (stacked) and p95 latency per group (single series, ▲ = n < 20); first 15 groups in backend order, each with a table view. **No cost chart**: a partial known cost next to unknown-cost groups would look comparable when it is not.
- **No ranking**: groups keep the backend order (attempts); a notice states that groups differ in sample size, prompt version, provenance and cost completeness, that no group is the best, and that no quality score exists. A SYNTHETIC banner appears when synthetic attempts are present. Nothing named "best", "winner" or "recommended" (unit + E2E).
- Filters (URL state): time range presets and custom UTC range, provenance, attempt status, group limit (10/25/50/100/200). Loading skeleton, empty and error states. The decoder checks group invariants (failed + succeeded = attempts; computed + unknown = attempts; `cost_complete` ⇔ no unknown; known cost ⇔ a priced call; token `n` = attempts).

### 3.2 Project context and selector
- Every page reads **one** project: `?project=<id>` (positive BIGINT) or `LLMI_DASHBOARD_PROJECT_ID` (renamed internally to `defaultProjectId`). Invalid → `invalid_project` state ("not sent to the backend; no other project was substituted"); unknown → the backend's indistinguishable `404` ("Project not found").
- `getJson(config, path, scope, params, …)` now **requires an explicit scope**: project endpoints need a validated id, `/api/v1/projects` must have none, and a `project` key in `params` is refused — all before any fetch. `requireProject` rejects overview/compare responses that echo another project.
- **Selector** (`components/project-selector.tsx`): plain GET form with a labelled `<select>` and a "Switch" button (no auto-submit on change). Switching is a **full page load**, so no render of the previous project survives; hidden fields carry the filters and window only — never a cursor, page or selected span (`restartFields`). From a trace detail it opens `/traces` of the chosen project (a trace id belongs to one project). A failed listing degrades to one line under the selector; the current project stays selectable even if unlisted. Project names render as text only.
- Side navigation (new "Models" link) carries an explicit project; the back link from a trace detail always uses the detail page's own project, whatever `?list=` contains.

### 3.3 R40 in the trace list
New column "Providers · models (calls)" in the table and the mobile cards: up to three combinations with `×calls`, "+N more combinations" from `distinct`, "none" without inference calls, `null` models as "unknown". Nothing picks one model for a trace. The decoder rejects inconsistent summaries (more listed calls than `inference_calls`; a complete listing that does not account for every call; `truncated` disagreeing with `distinct`; zero-call combinations).

### 3.4 Filters and refresh (Task 5)
- **Custom UTC ranges** on Overview, Models and Traces (`components/range-form.tsx`): `datetime-local` fields named `from_utc`/`to_utc` (UTC by name, independent of the browser time zone, tested in Europe/Istanbul); links carry ISO `Z` `from`/`to`. Validation: real calendar instants (no `2026-02-30` roll-over), start < end, ≤ 31 days at **microsecond** precision (a 31 d + 1 µs window was accepted by a first `Date.parse` version — found by a unit test, fixed), naive or offset link values refused. A refused range falls back to the default preset with the reason. The Overview states that custom-range edge buckets may be partial.
- Shareable canonical URLs: `project` first, defaults omitted; repeated or hostile parameters are ignored with a notice and never forwarded.
- **Auto-refresh** (`lib/auto-refresh.ts`, `components/auto-refresh.tsx`): off by default; 30 s / 1 min / 5 min; re-renders the same URL via `router.refresh()`; never while any navigation/refresh is pending; at most once per interval from the last completion; postponed while the tab is hidden; stops itself after 30 minutes; disabled with a reason on later cursor pages; labelled "Polling, not live". No storage.

## 4. Security and UX regression (Task 7)
- Unchanged and re-verified: server-side token only (T42 bundle scan + live response/storage scans on every page incl. `/models`), loopback-only backend URL, `Host` guard `421`, GET/HEAD only `405`, nonce CSP (no CSP violations or console errors on the new pages), path allowlist (now incl. `projects`), no route handler or Server Function, no `dangerouslySetInnerHTML`, no browser storage (static tests), lossless JSON and BigInt nanoseconds, exact Decimal, cursor correctness, keyboard access (skip link, labelled groups/forms/comboboxes, captioned tables), responsive layouts (no page-level horizontal scroll at 1440/390 px on Models, Overview, Traces and Detail).
- Hostile inputs tested live: a project named `e2e-<run>-xss <script>window.__llmiXss=1</script><img src=x onerror=…>` (selector option, current-project label), hostile span/service/resource labels (Phase 3B probes, re-run), hostile URL values for `project`, `range`, `from`/`to`, `from_utc`/`to_utc`, `status`, `provenance`, `limit`, `model`, `cursor`, `span`, `list`: no dialog, `window.__llmiXss` undefined, no injected elements. Hostile provider/model names cannot be stored (ingest drops them, Phase 1); component tests render hostile provider/prompt labels as text in the table and charts.
- UX fix found on review: during a backend outage the page showed two identical "Backend unreachable" alerts (page + selector); the selector now shows a single non-alert line.

## 5. R41: reproducible E2E fixtures (Task 6)
- `scripts/dev/e2e_fixtures.py setup|teardown --state <file>` (backend venv): creates `e2e-<12 hex>-main`, `-foreign` and `-xss <hostile>` (fails if a name exists), mints one key per seeded project **in memory**, runs `seed_trace_fixtures.py` (`--set edge` + new `--set phase3c` → main, `--set isolation` → foreign) with seeds derived from the run (deterministic ids per run, unique across runs) and timestamps 10 minutes before now (UTC), refuses if the seeder echoes the key, revokes the keys, writes a state file (run, ids, names; no secret). A failed setup deletes what it created. `teardown` deletes only state-file projects whose id **and** name match the run pattern (cascade: keys, traces, spans, usage); `LLMI_E2E_KEEP=1` keeps them.
- New seeder set `phase3c`: a retried call (attempt 1 `429` without usage, attempt 2 ok, one `call_id`) — replacing the dependency on `demo_app.web` — and a trace calling `openai`/`gpt-5.4-mini` twice (priced), `llminsight.mock`/`mock-chat-small` (unpriced) and `synthetic.alt`/`alt-model-x` (failed, no usage). Response ids use letters only, so the R08 digit-run rule cannot drop them at random. Nothing claims `real`.
- Playwright: `globalSetup`/`globalTeardown` (`tests/setup/`); because Playwright starts `webServer` before `globalSetup` (checked in the installed runner), ids reach the tests through `process.env` (`tests/e2e/e2e-env.ts`) and every page is opened with `?project=<run project>`. The teardown runs even when tests fail.
- **Evidence:** dev-DB counts before a run and after its teardown are identical (`projects 2, project_api_keys 5, traces 28, spans 846, usage_records 43, model_prices 6, active keys 0, e2e projects 0`); every run printed `"deleted": [3 ids], "remaining": []` (runs used ids 3–26, each deleted). Zero paid API usage (§8).

## 6. Compose and CI (Task 8)
- **Compose** (`docker-compose.yml`): the `postgres` service is byte-identical; the default `docker compose up` still starts PostgreSQL only. New opt-in profile `app`: `backend` (`backend/Dockerfile`, `python:3.12.6-slim-bookworm`, full `requirements.lock`, `pip check`, non-root, `alembic upgrade head && python -m app`, healthcheck on `/health/ready`) and `frontend` (`frontend/Dockerfile`, multi-stage `node:22.23.4-bookworm-slim`, `npm ci`, production build, runtime `npm ci --omit=dev`, user `node`) sharing **one network namespace** (`network_mode: service:backend`). Reason: the backend config refuses any non-loopback bind and the dashboard refuses any non-loopback backend URL; sharing the namespace keeps both guarantees. Only the dashboard is published, as `127.0.0.1:${LLMI_FRONTEND_HOST_PORT:-3001}:3001`; each container receives only its listed variables (dashboard: the three server-side variables; never the pepper or DB URL). `.dockerignore` files keep `.env`, `.env.local`, `node_modules`, builds and tests out of the images. Limitation R43: this profile does not expose ingest.
- **Compose results:** `docker compose config --quiet` exit 0 (default and `--profile app`); `docker compose --profile app build backend frontend` built both images; `docker compose --profile app up -d --no-deps --wait backend frontend` → both healthy; `GET 127.0.0.1:3001/` 200; `/models?project=1&range=31d` rendered the comparison table for project 1 (no error state); foreign `Host` → `421`; `POST` → `405`; `docker port` shows only `3001/tcp -> 127.0.0.1:3001`; backend log `Uvicorn running on http://127.0.0.1:8000`, Alembic "no upgrade" (dev DB already at head); env names in the dashboard container: `LLMI_API_BASE_URL`, `LLMI_API_TOKEN`, `LLMI_DASHBOARD_PROJECT_ID` only. Then `docker compose --profile app stop backend frontend` and `rm -f backend frontend` (never `down`); PostgreSQL container id and start time unchanged.
- **CI** (`.github/workflows/ci.yml`): triggers `push` to `main` and `pull_request`; `permissions: contents: read`; actions pinned to commit SHAs of releases ≥ 2 weeks old (checkout v7.0.1, setup-python v7.0.0, setup-node v7.1.0); jobs `docs` (doc verifier), `python` (throwaway `.env` via `init_env.py`, compose config for both profiles, PostgreSQL from the repo's compose service, backend venv + `pip check` + ruff/format + `alembic upgrade head` + `alembic check` + pytest with `LLMI_TEST_REQUIRE_DB=1`, dev-script lint from the root, SDK venv + ruff/format + strict mypy + pytest with `LLMI_TEST_REQUIRE_DB=1`), `frontend` (`npm ci`, `npm audit`, typecheck, lint, unit tests, build, bundle secret scan). No secrets, no paid services, no provider calls. **Not executed on GitHub** (nothing pushed, R44); every step's command was run locally in this phase. Explicitly outside CI (documented, not silently skipped): Playwright E2E (needs a live seeded backend and Edge) and the compose image build. YAML parsed (3 jobs: 3 / 14 / 10 steps).

## 7. Tests and evidence (Task 9; final run after the last code change, Node 22.23.4, Python 3.12.6)

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `npm run typecheck` (frontend) | strict `tsc --noEmit`, 0 errors | 0 |
| 2 | `npm run lint` (frontend) | ESLint, 0 problems, `--max-warnings 0` | 0 |
| 3 | `npm test` (frontend) | **236 tests, 236 pass, 0 fail, 0 skipped** (194 before + 42 new) | 0 |
| 4 | `npm run build` (frontend) | routes `/`, `/_not-found`, `/icon.svg`, `/models`, `/traces`, `/traces/[traceId]`, Proxy | 0 |
| 5 | `npm run check:bundle` (frontend) | 456 files scanned, 18 in the browser bundle, 1 secret value + 5 names, **0 findings** | 0 |
| 6 | `npm run test:e2e` (frontend; real backend :8000, PostgreSQL; dashboards :3001/:3002/:3003) | **44 passed, 0 failed, 0 skipped** (12 overview + 18 traces + 14 Phase 3C); run `3a37990b1473`, teardown deleted 24, 25, 26 | 0 |
| 7 | `npm audit` / `npm ls --all` (frontend) | 0 vulnerabilities / clean; `package.json` and lockfile unchanged | 0 / 0 |
| 8 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1104 passed, 0 failed, 0 skipped** (1082 + 22 new), 1 warning (R20) | 0 |
| 9 | same (sdk) | **343 passed, 0 failed, 0 skipped** (unit + live backend) | 0 |
| 10 | `ruff check .` / `ruff format --check .` / `pip check` (backend) | `All checks passed!` / `89 files already formatted` / no broken requirements | 0 |
| 11 | `ruff check` / `ruff format --check` on `scripts/dev/{seed_trace_fixtures,e2e_fixtures}.py` (root, backend venv) | passed / 2 files already formatted | 0 |
| 12 | `alembic current` / `alembic check` (backend, dev DB) | `0002 (head)` / `No new upgrade operations detected.` | 0 |
| 13 | `ruff check .`, `ruff format --check .`, `python -m mypy src`, `python -m mypy --config-file pyproject.toml ../demo_app`, `pip check` (sdk) | passed; 28 formatted; strict mypy `no issues` in 8 and 3 files; clean | 0 |
| 14 | `docker compose config --quiet`; `docker compose --profile app config --quiet` (root) | valid | 0 / 0 |
| 15 | `python -I scripts/verify_phase0.py --quiet` (root) | `SUMMARY: 728 PASS, 0 FAIL` | 0 |
| 16 | `git diff --check` (root) | no output | 0 |
| 17 | Credential scan (scratch script; changed/untracked repo files, build output, all session logs; prints counts only) | 576 files, 3 local secret values searched: **0** files with a value, **0** key/`Bearer`-shaped strings; `OPENAI_API_KEY` empty | 0 |

**New backend tests (22):** `tests/integration/test_phase3c_api.py` (21) — projects: 401 before validation (3 header cases), shape/order/no key material/hostile name verbatim, truncation, `limit` validation without echo (5 values), empty DB, read-only (4 methods → 405), `/api/v1` rate limit, SQL-level bound; trace models: several providers/models with counts and a null response model (list = detail), > 10 combinations counted not listed, cross-project isolation with identical trace ids, models across cursor pages, EXPLAIN on 10,000 traces (no seq scan, usage unique key, distinct counts equal an independent SQL count), empty id list, window/cursor unchanged. `test_metrics_api.py` (1): per-group tokens, unknown never 0, estimated usage, groups sum to the overview totals. Changed expectations (contract additions, not loosened): the two exact list-item shape tests now include `usage.models`; the route-inventory test lists `/api/v1/projects`.

**New frontend unit/component tests (42, `tests/unit/phase3c.test.tsx` + `time-range`/`backend` additions):** project id parsing (BIGINT bound, repeated/hostile values), link and back-link project rules, `restartFields` (no cursor/page), model query parsing/params/links, custom ranges (form vs link fields, naive/offset/impossible/reversed/empty/31 d + 1 µs refused with reasons, exactly 31 d accepted), decoders (project list bounds; `usage.models` consistency mutations; compare group invariants), auto-refresh decisions (interval, pending, hidden, 30-min stop), explicit scope enforcement before fetch, listing sent without `project`, explicit project used for all five data calls, invalid project → no request, echoed-project mismatch rejected, failed listing degrades only the selector; components: exact/unknown/incomplete cost, estimated tokens, "not ranked"/synthetic notices without ranking words, backend order in charts, hostile labels escaped (table, charts, models cell, selector), selector form/hidden fields/unlisted project/outage line, range form fields; static: no Server Function, raw HTML or browser storage in the new pages. Re-recorded contract fixtures (`trace-list`, `trace-detail-nested`, `trace-detail-retry`, `models-compare` `.live.json`) from the real backend with the same requests as Phase 3B/3A; `overview.live.json` unchanged.

**New E2E (14, `tests/e2e/phase3c.spec.ts`):** comparison table = API row by row (attempts, exact cost / unknown, tokens), no ranking words, synthetic banner, chart rendered, screenshot; filters in the URL (failed-only groups all failed, `mixed` → empty usage population, limit and 7-day window); custom range via the form in a non-UTC browser (window = UTC values entered) and refused ranges with reasons on three pages; selector lists the run's projects and renders the hostile project name as text; switching project keeps filters, shows only the new project's data (no old trace id anywhere in the HTML), from a cursor page drops cursor/page, navigation keeps the project, Overview equals the foreign project's API; switching from a trace detail opens the new list and the old trace is a 404 there; invalid ids (5 values × 4 pages) → `invalid_project`, no data, no selector; unknown id → `not_found` on three pages; R40 multi-model trace (3 combinations, call counts, every row's `distinct` = API); hostile URL values on the new controls; auto-refresh off by default, no polling while off, one refresh after 31 s (fake clock), stopped after 30 min, disabled on a cursor page; no credential in responses/storage and no console/CSP errors across Models → Traces; keyboard/labels/captions on Models; no page-level horizontal scroll at 1440 and 390 px on Models (incl. the hostile project), Overview and Traces. Existing specs now use the run's projects; the retry test uses `phase3c.retry`.

### 7.1 Mutation checks (defect injected, suite run, file restored byte for byte; tree hash compared)

| Mutation | Caught by |
|---|---|
| F1 backend client sends the default project instead of the scope | unit (1) |
| F2 invalid project falls back to the default | unit (1) |
| F3 echoed-project check disabled | unit (1) |
| F4 trace models column shows only the first combination | unit (1) |
| F5 unknown group cost rendered `$0.00` | unit (1) |
| F6 custom window compared at millisecond precision | unit (1) |
| F7 back link trusts the project inside `list` | unit (1) — first attempt mutated a redundant guard and survived; retargeted at the real guarantee |
| F8 auto-refresh ignores pending requests | unit (1) |
| F9 trace models decoder accepts inconsistent counts | unit (1) |
| F10 project switch keeps the cursor | **survived at first** → `restartFields` extracted, unit test + live E2E step added → unit (1) |
| B1 one model picked per trace | integration (2) |
| B2 trace models not scoped by project | integration (2) |
| B3 truncation never flagged | integration (1) |
| B4 project listing without admin auth | integration (4) |
| B5 project listing unbounded in SQL | **survived at first** (the response was still sliced) → SQL-level test added → integration (1) |
| B6 per-group tokens dropped | integration (1) |

### 7.2 Test matrix (exact definitions in `TEST_MATRIX.md`)

| ID | Definition | Evidence (Phase 3C) | Status |
|---|---|---|---|
| T41 ★ | `<script>` in a model/service name renders as text | Live: hostile service/span/resource labels (list + detail) and a hostile **project name** (selector + label), hostile URL values on every control; components: hostile provider/model/prompt labels in the comparison table, charts and the trace models cell; mutation M13 of 3B still applies | PASS (phase-3b) → **PASS (phase-3c)**; re-verify Phase 5 |
| T42 ★⛔ | No admin token in build output / bundle / HTML | `check:bundle` 0 findings; live response/storage scans on Overview, Traces, Detail and Models; dashboard container env holds only its three variables; credential scan 0 | PASS (phase-3a) → **PASS (phase-3c)** |
| T45 ★ | Seeded DB data visible in the list; screenshot | Run-owned seeded traces = API row by row; `phase-3c-screenshots/traces-list-desktop.png` | **PASS (phase-3c)** |
| T46 ★⛔ | List → detail shows root → child tree | Click from the list, levels = parent + 1 for all spans, back link restores the list (now with the project) | **PASS (phase-3c)**; re-verify Phase 5 |
| T48 ★ | SYNTHETIC/MIXED/UNKNOWN badges; never counted as real | List/detail badges and notices (run-owned mixed/unknown/synthetic traces), per-group provenance on Models, SYNTHETIC banner; real badges = API real count (0) | **PASS (phase-3c)**; re-verify Phases 4–5 |

UI re-verification of earlier rows (statuses unchanged): T25 cursor pagination (now also dropped on project switch), T26 ns timestamps, T27 orphan, T28 project filter (now also via the selector and URL), T18 unknown tokens/cost.

### 7.3 Screenshots (`docs/reports/phase-3c-screenshots/`, SYNTHETIC run-owned data)
`models-desktop.png`, `models-mobile.png` (comparison, cards on mobile), `traces-list-models-desktop.png`, `traces-list-models-mobile.png` (R40 column), `traces-list-desktop.png` (T45), `traces-list-mobile.png`, `trace-detail-desktop.png`, `trace-detail-mobile.png`, `trace-detail-app-error.png`, `trace-detail-xss-escaped.png`, `trace-detail-backend-unreachable.png`, `overview-{desktop,tablet,mobile,backend-unreachable}.png` — all from the final E2E run. Reviewed: at 1440 px the comparison table's cost column lies inside the table's own scroll region (R47); provenance was moved next to the group after the first review so it is always visible; a duplicated "UTC" in the window line was removed.

### 7.4 Defects found and fixed during the phase
31 d + 1 µs custom window accepted (ms precision); double outage alert; provenance column off-screen and "UTC UTC" (screenshot review); two mutation survivors (F10, B5) closed with tests; test-side issues fixed without weakening assertions (a `$0.00` regex that matched `$0.0012345`, attribute-order-sensitive form regex, a substring role-name match, a lost regex escape in a template literal, column-position locator replaced by a test id, Windows console decoding in the mutation harness, a wrong ruff working directory for the dev scripts in the CI file and in one local run).

## 8. No real OpenAI usage
`OPENAI_API_KEY` is empty in `.env` (checked without printing), `LLMI_ALLOW_REAL_LLM=false` (and forced `false` in the compose backend); every trace in this phase came from `seed_trace_fixtures.py`, which only posts to a loopback `/v1/traces`; the SDK suite's OpenAI adapter tests use fake transports with network blocking (Phase 2B); no demo was run in real mode. **No provider was called.**

## 9. Risks and limitations
- **Closed:** R40 (additive `usage.models`), R41 (run-owned fixtures with exact cleanup).
- **New:** R43 compose `app` profile does not publish ingest; dashboard binds `0.0.0.0` inside the shared namespace (host publish is loopback only). R44 CI not yet run on GitHub. R45 the listing shows every project to the single admin token (no new permission; ≤ 200). R46 one more `/api/v1` call per render (no `429` observed). R47 comparison table scrolls inside its region at 1440 px.
- **Unchanged:** R27, R36–R39, R42; public deployment not authorized; provenance is a client claim; unknown cost stays `NULL`.
- Not built (not in the Phase 3C instruction): Overview filters for model, status and prompt version (the API supports them); a provider/service filter on the trace list.
- The Phase 3B data in dev projects 1 and 2 (including the old hostile probes) is left as it was; the E2E suite no longer depends on it.

## 10. Phase 3 final acceptance prerequisites
1. Architectural review of Phase 3C (and the pending user reviews of 3A and 3B in `PHASE_STATUS.md`).
2. First CI run on GitHub after an approved push (R44); fix any Linux-only differences.
3. Decide whether the Overview needs model/status/prompt-version filters before closure.
4. Phase 3 re-verification run (this report's commands) on the closure commit, then the annotated `phase-3` tag (not created).
5. Remaining critical tests not yet PASS, all owned by later phases: T33, T34⛔, T35⛔, T36, T37, T39 (Phase 4, NOT RUN) and T43 (PARTIAL; full rate-limit re-verification in Phase 5). Non-critical NOT RUN: T38, T40, T47 (clean-clone compose, Phase 5).

## 11. Files
Added: `backend/app/api/projects.py`, `backend/app/repositories/projects.py`, `backend/tests/integration/test_phase3c_api.py`, `backend/Dockerfile`, `.dockerignore`, `frontend/Dockerfile`, `frontend/.dockerignore`, `.github/workflows/ci.yml`, `scripts/dev/e2e_fixtures.py`, `frontend/app/models/{page,loading}.tsx`, `frontend/components/{auto-refresh,project-selector,range-form}.tsx`, `frontend/components/models/model-comparison.tsx`, `frontend/components/charts/model-charts.tsx`, `frontend/lib/{project,model-query,model-groups,auto-refresh}.ts`, `frontend/lib/server/{context,models}.ts`, `frontend/tests/e2e/{e2e-env.ts,phase3c.spec.ts}`, `frontend/tests/setup/e2e-global-{setup,teardown}.ts`, `frontend/tests/unit/phase3c.test.tsx`, this report and its screenshots. Changed: `backend/app/{main.py,api/traces.py,repositories/{traces,metrics}.py,services/metrics.py}`, `backend/tests/{integration/test_trace_api.py,integration/test_metrics_api.py,unit/test_health.py}`, `docker-compose.yml` (header + `app` profile; `postgres` unchanged), `.env.example` (comment only), `scripts/dev/seed_trace_fixtures.py` (`--set phase3c`), frontend pages, filters, list, navigation, shell, server modules, decoders, types, config (field rename), tests and fixtures, `frontend/playwright.config.ts`, `frontend/README.md`, `docs/{ARCHITECTURE,METRICS_CATALOG,SECURITY,SOURCES_AND_RISKS,TEST_MATRIX,PHASE_STATUS}.md`. No schema, migration, SDK, pricing or dependency change (`package.json`, lockfiles and Python locks unchanged).
