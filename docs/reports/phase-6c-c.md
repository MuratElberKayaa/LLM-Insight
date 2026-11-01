# Phase 6C-C — Live observability dashboard: real/synthetic filtering, controlled refresh, provider views — Report

**Date:** 2026-11-01 (UTC). **Branch:** `feature/phase-6c-gemini-free` (from `580d457`; not pushed, not merged, no tag). **Decisions:** ADR-37, ADR-38 (`ADR.md` §11, recorded for the architectural review). **No Gemini or OpenAI request was sent, no provider key was read or used, nothing was spent; the stored real Gemini trace was only read.**

## 1. Recovered baseline

| Item | State found |
|---|---|
| Repository | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (own Git root; the home-directory repository was not touched) |
| Branch / HEAD | `feature/phase-6c-gemini-free` at `580d457` (Phase 6C-A); nothing committed after the real call |
| Uncommitted work | `.env.example` only, modified by the user with live-run settings (no key). Kept as is, **not committed** (`phase-6c-b.md` §3) |
| Runtime | PostgreSQL container stopped (started on its volume, nothing recreated); the user's backend from 2026-10-28 on `127.0.0.1:8010` (pre-6C-C code, left running, not used for tests); an unrelated container (`ppe-split-dashboard-1`) on `127.0.0.1:8000` (left untouched). This session's backend ran on `127.0.0.1:8020`; the limited backends on 8001/8002 |
| Reports read | `phase-6c-a.md`; no Phase 6C-B or integrity-audit report existed (Phase 6C-B evidence is now `phase-6c-b.md`) |

## 2. Gap analysis (before coding)

| Feature | Found | Location | Change | Coverage before |
|---|---|---|---|---|
| Auto-refresh: off by default, 30 s / 1 min / 5 min, no overlap, 30-min bound, off on cursor pages | EXISTS (Phase 3C) | `lib/auto-refresh.ts`, `components/auto-refresh.tsx` | Timers moved into a testable controller; a hidden tab now has **no** timer (it re-armed one per interval); 15 s floor | unit `decideRefresh`, E2E |
| One refresh in flight; superseded render discarded | EXISTS (Next.js action queue + pending check) | `navigation-transition.tsx`; `next/dist/client/components/app-router-instance.js` (a navigation marks the pending action `discarded`) | Documented, controller-tested | — |
| Manual Refresh | EXISTS | `RefreshButton` | Blocked during a 429 cooldown | — |
| 429 handling | PARTIAL: `Retry-After` delay-seconds only, error card | `lib/server/backend.ts` | HTTP-date support (rounded up), cooldown for timer and button | T43 E2E |
| 5xx / network | error card, no retry policy | — | Exponential backoff ≤ 5 min | — |
| Last valid data on failure | MISSING (the error replaced the data) | pages | `LiveRegion`: same view only, labelled stale | — |
| 401 / 404 / invalid project / mismatch | error card; polling continued | — | Polling stops with the reason; never stale data | — |
| Real / Synthetic / Mixed / Unknown | EXISTS server-side on all three endpoints (ADR-21) | `filter-bar`, `trace-filters` | "Real (asserted)" label everywhere; no reclassification | unit + E2E (T48) |
| Model filter | PARTIAL (trace list only, free text) | `trace-filters` | On Overview and Models too; observed-value suggestions | unit, E2E |
| Provider filter | MISSING (no API parameter) | — | Additive read-only API parameter (ADR-37) | — |
| Latest stored trace; time distinctions | MISSING | Overview | `GET /api/v1/traces?limit=1`; event time vs read time | — |
| Project switch | kept every filter (model included) | `project-selector` hidden fields | Provider and model reset (project-specific values) | E2E |

How the backend reports what the new views need: provider (`usage_records.provider`, `unknown` when absent), models (`requested_model`/`response_model`, nullable), provenance (`traces/spans/usage_records.provenance`, `provenance_verified: false`), tokens (provider-reported, nullable), cost (`cost_status`, `cost_usd` NULL when unknown), errors (span `ERROR`, trace `error`), timestamps (event time, ns), project (mandatory parameter, echoed by aggregates). The trace list had no provider filter and no filter echo, which is why ADR-37 adds an API parameter instead of a client-side filter of one page.

## 3. First real Gemini trace compatibility

Verified without any provider call (details and screenshots: `phase-6c-b.md` §2): `gemini-live` (#148) → trace `bec4f18a3f9a29375a61290dfc62798d`: 2 spans with the correct parent, `gcp.gemini` / `gemini-3.1-flash-lite`, input 18 / output 1 (`provider_reported`), status OK, cost unknown (`price_missing`, `null`, never 0), provenance `real` with `provenance_verified: false`. Runtime readback through the API: **VERIFIED**. Dashboard (Overview, Models, Traces, Trace detail; 7-day window; GET only): **VERIFIED** — every page labels it "Real (asserted)" and shows the unknown cost as unknown. The default 24-hour window no longer contains it (event time 2026-10-29); that is the window rule, not a defect. No compatibility change was needed and the stored trace was not modified.

## 4. What was implemented

**Backend (ADR-37, additive and read-only).** Optional `provider` on `GET /api/v1/traces`, `/api/v1/overview`, `/api/v1/models/compare`: exact `usage_records.provider` in the stored identifier format (`422` naming `provider` otherwise, auth first, never echoed); usage-level metrics filter the row, trace-level metrics and the list use "a usage row of the trace with that provider"; with `model`, **one row must match both** (`usage_match`, one `EXISTS`, shared by the list and the trace-level metrics). Aggregates echo `filters.provider`. Same plans as before for a present provider (EXPLAIN-tested). No migration, no index, no write path.

**Frontend.**
- `RefreshController` (`lib/auto-refresh.ts`): one refresh timer at most plus a display-only countdown, both cleared on unmount and safe under Strict Mode (attach/detach); paused while hidden; resumes on `visibilitychange`; 30-minute bound; cooldown from `Retry-After` (timer and button); backoff `interval × 2^n` ≤ 5 min; stop on `config`, `invalid_project`, `unauthorized`, `not_found`, `invalid_request`, `malformed` (which includes "response for another project").
- `renderOutcome`: each server render reports the kind and class of its reads' failures (no data) to the controller through `NavigationTransition`'s new `outcome` prop.
- `LiveRegion`: last good content of the same view (project + filters + window), shown below the error, dimmed and labelled with its load time, only after a transient or 429 failure.
- Filters: `ScopeFilterForm` (provider `select`, model field with a `datalist`, GET form, works without JavaScript) on Overview, Models and Traces; values from one unfiltered `models/compare?limit=200` of the same project and window; attempts without a reported model are counted and stated; unobserved URL values are kept and marked; a project switch resets provider and model. Echo checks: an aggregate that does not echo the requested filters, or a trace list with an item lacking a matching call, is refused (`malformed`).
- Overview: "Latest stored trace" (event time reported by the instrumentation, duration, status, provenance, providers and models, link) next to the read time; mixed-provenance notice on Overview and Models.
- Labels: "Real (asserted)" for every `real` value (badges, filters, banners).
- Security fix found by the E2E suite while implementing: a client component imported `describeFailure`, which carries operator hints naming `LLMI_API_TOKEN`/`LLMI_ADMIN_TOKEN` (names, no values) into the browser bundle; fixed with a fixed client-side copy (`STOP_REASON`) and a static test that no client component imports `describeFailure`.

**Fixtures (test-owned, SYNTHETIC).** `seed_trace_fixtures.py --set phase6c` (openai ok/failed, gcp.gemini synthetic, one asserted-real `gcp.gemini` span with a response id, a real claim without a response id that the server stores as unknown, a span with no provider/model/claim) in a fourth run-owned project `e2e-<run>-providers`; `e2e_fixtures.py add-trace` sends one more SYNTHETIC trace during a test with a key minted and revoked for that call. The teardown deletes the four run projects by id and name.

## 5. Refresh lifecycle and rate-limit design

```
server render ── reads (/api/v1, server-side only) ── renderOutcome{renderId, fetchedAt, failure kind/class, cooldownUntil}
      │                                                        │
      ▼                                                        ▼
LiveRegion (retain on success; stale on transient/429)   NavigationTransition → RefreshController.observe()
                                                               │  setPending(transition pending)
                                                               │  visibilityChanged()
                                                               ▼
                         decideRefresh: stop (30 min) | pause (hidden) | wait (pending, interval×backoff, cooldown) | refresh
                                                               │
                                                     router.refresh() in a transition
```

| Situation | Timer | Refresh button | Display |
|---|---|---|---|
| Off (default) | none | enabled | "Off. Data changes only when you refresh." |
| On, visible | one timer to `max(last completed + interval·2^failures, cooldown end)` | enabled | interval, "Re-reads stored telemetry; not live", last successful load |
| Request in flight | re-check after 1 s | disabled ("Refreshing…") | region dimmed |
| Tab hidden | **none** | — | "Paused while this tab is hidden" |
| `429` | waits for `Retry-After` (seconds or HTTP-date, rounded up; 60 s if absent); a later shorter value never shortens it | disabled, "Retry in N s" | error first, then the last good data labelled stale; absolute end time in the polite live region (no per-second announcements) |
| Outage / timeout / 5xx | backoff 60 s, 120 s, … ≤ 5 min | enabled | error, stale data of the same view |
| `401`, `404`, invalid project, config, `422`, contract mismatch | stopped, chosen interval cleared | enabled (deliberate manual retry) | "Stopped: … fix the cause, then refresh manually"; no stale data |
| Navigation during a refresh | Next.js discards the unfinished refresh's result | — | the new view only |

## 6. Provenance filter behaviour

The filter values are the backend's (`real`, `synthetic`, `mixed`, `unknown`) and apply server-side to the whole stored dataset at each metric's own level (ADR-21, METRICS_CATALOG.md). The UI never classifies a record: provider, model, timestamps, token usage and response ids are never used to decide provenance. `real` is labelled "Real (asserted)" with the tooltip "asserted, not verified" and `provenance_verified` stays `false`. A `real` claim without a response id is stored as `unknown` by the server's downgrade rule and appears only under Unknown (E2E). `mixed` exists only for traces; the Overview and Models pages say that attempt-level views are empty for it.

## 7. Provider and model filtering

Exact backend filters (ARCHITECTURE.md §5.11), combinable with project, window, provenance and (Traces) status; one call must match provider and model together. `unknown` selects spans that named no provider. Missing models cannot be selected by a model filter; the control states how many attempts that affects. Suggestions are the observed values of the project and window (top 200 groups; truncation stated, R100); a URL value not observed is kept and marked; malformed values are ignored with a notice and never forwarded. Filters live in the URL, so they survive refreshes and paging restarts on a filter change; a project switch resets provider and model and never shows the previous project's rows (E2E).

## 8. Tests and quality gates (exact commands, this session)

| Command | Result |
|---|---|
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend, real PostgreSQL) | **PASS** — 1765 passed, 0 failed, 0 skipped (289.6 s; 1 warning: R20), exit 0 = 1730 + 35 new (30 in `test_phase6c_provider_filter.py`, 5 plan variants) |
| `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk, live backend) | **PASS** — 817 passed, 0 failed, 0 skipped (270.9 s), exit 0 (OpenAI and Gemini suites unchanged) |
| `npm test` (frontend, Node 22.23.4) | **PASS** — 349 passed, 0 failed, 0 skipped, 0 cancelled = 311 + 38 (`phase6c.test.tsx`) |
| `npx playwright test` (Edge; `LLMI_API_BASE_URL`/`LLMI_SEED_ENDPOINT` → backend on `127.0.0.1:8020`, port 8000 occupied by an unrelated container) | **PASS** — 62 passed, 0 failed, 0 skipped (2.2 min) = 55 + 7 (`phase6c.spec.ts`); teardown `remaining: []` |
| `npm run typecheck` / `npm run lint` | **PASS** — clean / 0 warnings |
| `npm run build` / `npm run check:bundle` | **PASS** — built / 634 files, 23 in the browser bundle, 0 findings |
| `npm audit` | **WARN** — exit 1: 16 moderate, one advisory (GHSA-g3hv-75wg-m249, `minimatch`) in dev-only ESLint tooling; `npm audit --omit=dev`: 0 (R102; package files unchanged) |
| `ruff check .`, `ruff format --check .` (backend; sdk with `../demo_app`; the two changed `scripts/dev` files) | **PASS** — all passed; 141 / 50 / 2 files formatted |
| `python -m mypy src`; `python -m mypy --config-file pyproject.toml ../demo_app` (strict) | **PASS** — no issues (12 / 6 files) |
| `pip check` (backend; sdk) | **PASS** — no broken requirements |
| `alembic current` / `alembic check` | **PASS** — `0005 (head)` / no new upgrade operations |
| `docker compose config --quiet`; `docker compose --profile app config --quiet` | **PASS** — exit 0 / 0 |
| `python -I scripts/verify_phase0.py` | **PASS** on the staged tree (temporary worktree = `HEAD` + `git diff --cached`, the committed `.env.example` template): 797 PASS, 0 FAIL, exit 0. The working tree gives 797 PASS, **1 FAIL**: the user's uncommitted `.env.example` (`LLMI_LIVE_MAX_*_TOKENS` not placeholders), which is not part of this commit |
| `pip-audit -r backend/requirements.lock --no-deps --disable-pip -s pypi\|osv` | **PASS** — no known vulnerabilities |
| `pip-audit -r sdk/requirements-dev.lock --no-deps --disable-pip -s pypi\|osv` | **FAIL** — exit 1: `pyasn1` 0.6.4, four advisories published after the 6C-A audit (R101; no dependency changed in this phase) |
| `pip-audit --path <venv>/Lib/site-packages` (both venvs) | **WARN** — backend: `pip` 24.2 only (R75); sdk: `pip` 24.2 (R75) + `pyasn1` (R101) |
| `bandit` 1.9.4 on the changed Python files (`api/{traces,overview}.py`, `repositories/{traces,metrics}.py`, `services/metrics.py`, `scripts/dev/{e2e_fixtures,seed_trace_fixtures}.py`) | **PASS** — 0 High, 0 Medium; 2 Low (B404/B603: the fixture script's fixed `subprocess` call of the seeder, unchanged pattern) |
| `python -I scripts/qa/secret_scan.py --repo . --env .env --env frontend/.env.local` (history, messages, staged and working files) | **PASS on secret values / WARN on patterns** — exit 1: 883 blobs, 33 messages, 497 working files, 9 local secret values (names only): **0 secret-value hits**; 40 pattern hits, all `url_userinfo` at the pre-existing Phase 5B F9 fixture locations (`docs/SECURITY.md` blobs 16, backend/SDK redaction test vectors 22, `phase-2c.md` 2); no hit is in a file changed by Phase 6C-C |
| `git diff --check` | **PASS** — exit 0 |

**Mutation checks** (`mutate.py`, each mutation applied, the suite run, the file restored byte for byte): frontend 11/11 killed (cooldown ignored by the button / by the timer, fatal failure keeps polling, no backoff, hidden tab keeps a timer, stale data across views, stale data after a fatal failure, trace-list scope unchecked, echo unchecked, HTTP-date rounded down, `real` not labelled asserted); backend 5/5 killed (provider dropped from the list, provider and model matched on different rows, provider not applied to usage-level metrics, provider not echoed, no format check).

**Unit coverage of the instruction's list:** disabled by default, enable/disable, timer lifecycle, no duplicate timers (repeated enable, Strict-Mode attach/detach), hidden tab, unmount, no overlap, stale-response suppression (same-view rule; framework discard documented), Retry-After (seconds, HTTP-date, past, bound, garbage), 429 cooldown for timer and button, backend failures (backoff, stop), labels, unknown provenance, provider/model parsing and suggestions, echo checks, loader parameters (project-scoped, same window), unknown cost never $0 for a Gemini group, UTC event-time rendering, empty/unavailable latest trace, no provider endpoint or key in any frontend source.

**Browser E2E (`phase6c.spec.ts`, live stack):** (1) provenance: Real (asserted) / Unknown (incl. the downgraded claim) / Synthetic match the backend; (2) provider/model: observed options incl. `unknown`, exact filtering, combination with provenance, one-call rule, Overview and Models filtered by the backend, Gemini cost unknown not $0, malformed value refused; (3) project switch resets provider/model and shows only the other project; (4) manual refresh re-reads; auto-refresh off sends nothing for 5 minutes; enabled at 30 s, nothing at 29 s, the trace added by `add-trace` appears after exactly one refresh; off again sends nothing; (5) 429 after a successful load: error first, stale data labelled, no empty state, button disabled "Retry in N s", no request for 5 minutes (Retry-After ≈ 1000 s); (6) backend down: error, no data, first retry at 60 s not 30 s, then 120 s; (7) wrong token: one attempt, then "Stopped", no further request in 30 minutes. Existing flows re-run green: Trace Explorer, human review → decision → dataset → approved case, mock A/B experiment against PostgreSQL, T41/T42/T43/T48, CSP/console, accessibility, mobile layout.

## 9. Security and privacy verification

- No new route handler, Server Action, write path or client-side backend call; the backend is still reached only by `lib/server/backend.ts` (static test updated with `filter-options.ts`). The dashboard has no provider code, endpoint or key (static test); no provider was called by any test.
- New query values are validated before forwarding (provider and model formats, already-validated windows); the backend validates again (`422` without echo, auth first). Every read carries one explicit project; aggregates are checked for the echoed project and filters; the stale display is keyed by project + filters + window and never crosses views.
- T42: the bundle leak of setting **names** introduced during development was caught by the existing E2E tests and fixed before the final run; `check:bundle` 0 findings; E2E bodies contain no token. Prompts and completions are never captured; the real trace read-back shows metadata only.
- Rate-limit consumption: Overview 5, Traces and Models 3 `/api/v1` reads per render (R99); cooldown and backoff prevent bursts; the T43 dashboard tests still pass.

## 10. Screenshots (`phase-6c-c-screenshots/`)

`01`–`07` from the E2E run (run-owned SYNTHETIC data): Real (asserted) filter, provider `unknown`, Overview and Models filtered to `gcp.gemini`, the newly added trace after an automatic refresh, the stale view after a 429, backend unavailable. `08`–`11`: read-only views of the first real Gemini trace (Overview, Models, Traces, Trace detail; project #148, 7-day window).

## 11. Files

Backend: `app/api/{traces,overview}.py`, `app/repositories/{traces,metrics}.py`, `app/services/metrics.py`; tests `tests/integration/test_phase6c_provider_filter.py` (new), `test_metrics_plan.py`, `test_trace_api.py`. Frontend: `app/{page,models/page,traces/page}.tsx`; `components/{auto-refresh,navigation-transition}.tsx`, `components/traces/trace-filters.tsx`, new `components/{live-region,scope-filter-form}.tsx`, `components/overview/latest-trace.tsx`; `lib/{auto-refresh,model-query,provenance,time-range,trace-query}.ts`, new `lib/scope-filters.ts`, `lib/api/{schema,types}.ts`, `lib/server/{backend,models,overview,traces}.ts`, new `lib/server/filter-options.ts`; `playwright.config.ts`; tests `tests/unit/phase6c.test.tsx`, `tests/e2e/phase6c.spec.ts` (new), updated `tests/unit/{backend,phase3c,security-static,time-range,trace-components,trace-query}.test.*`, `tests/e2e/{overview,traces}.spec.ts`, `tests/e2e/e2e-env.ts`, `tests/setup/e2e-global-setup.ts`, `tests/fixtures/overview.ts`. Scripts: `scripts/dev/{e2e_fixtures,seed_trace_fixtures}.py`. Docs: `ADR.md` §11, `ARCHITECTURE.md` §5 table and §5.11, `SOURCES_AND_RISKS.md` R97–R102, `PHASE6_LIVE_INTEGRATION_PLAN.md` Part III, `PHASE_STATUS.md`, `README.md`, `frontend/README.md`, `reports/phase-6c-b.md`, this report and its screenshots.

Existing tests changed only where the intended behaviour changed: new fields in deep-equality checks, the hidden-tab decision (`pause` instead of a re-armed wait), the request count of the loaders (5 → 9), the reader allowlist (`filter-options.ts`), the trace filter now offering a provider, the "Real (asserted) only" link name, the model field's role (`combobox`, because of its suggestions) and the search form's name. The hostile-value test now feeds malformed `model` and `provider` values (both refused) instead of a valid one.

## 12. Known limitations and review items

- **Not live:** controlled re-reads of stored telemetry; no push, no token streaming. Freshness is bounded by the chosen interval, the exporter's batching and ingest.
- **Stale display** may still be misread (R97); **cooldown** binds the timer and button, not a browser reload (R98); **read budget** per render increased (R99); **suggestions** limited to 200 groups (R100).
- **Open dependency advisories found by this phase's audit** (not introduced by it): R101 (`pyasn1`, SDK Gemini extra, Low, needs a pin decision) and R102 (`minimatch`, dev-only ESLint, Low).
- Provenance remains client-asserted (R16, ADR-21); Gemini cost remains unknown by design (ADR-35); a provider literally named `unknown` cannot be told apart from a missing one.
- The user's modified `.env.example` is outside this commit; the doc verifier fails on it in the working tree (`phase-6c-b.md` §3).
- Linux CI was not run (branch not pushed). The dashboard is **not production-ready**: local-only, no authentication (SECURITY.md).

## 13. Commit and verification of the committed tree

The commit contains only the files of §11 (66 paths; the user's `.env.example` stays modified and unstaged). Before committing, the staged tree was reproduced in a temporary worktree (`git worktree add --detach … HEAD` + `git diff --cached --binary | git apply`) and the doc verifier passed there (797 PASS, 0 FAIL); the secret scan ran on the repository with the change staged (§8). The commit id cannot be written into its own report; it is given in the completion report.

## 14. Verdict

Phase 6C-C delivers controlled refresh, server-side Real (asserted) / Synthetic / Mixed / Unknown and exact provider/model filtering, honest stale and failure states and the latest-trace summary, verified on the live stack with SYNTHETIC data and read-only against the first real Gemini trace. No provider was called. **Stopped for architectural review** (ADR-37, ADR-38, R101, R102).
