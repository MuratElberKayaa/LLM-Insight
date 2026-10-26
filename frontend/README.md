# LLM Insight dashboard (frontend)

Local-only Next.js App Router dashboard for the LLM Insight backend. Phase 3A delivered the
foundation (shell, design system, server-side API layer) and the **Overview** page; Phase 3B
adds the **Trace Explorer** (`/traces`) and **Trace Detail** (`/traces/{trace_id}`) with the
span tree and waterfall; Phase 3C adds model comparison. Phase 4B adds the **human failure review**
and the unverified machine-observation panel on Trace Detail, **Trace-to-Testset**
(`/traces/{trace_id}/reviews/{review_id}/test-case`) and **Datasets** (`/datasets`). Phase 4D adds
**Experiments**: list (`/experiments`), mock-only setup (`/experiments/new`), results
(`/experiments/{id}`) and per-case detail (`/experiments/{id}/cases/{case_id}`); every result is SYNTHETIC.

## Runtime and versions

| Component | Version | Note |
|---|---|---|
| Node.js | 22.23.4 (project-scoped, `.nvmrc`, `engines`, `engine-strict`) | Installed into `../.tools/` by `python -I ../scripts/dev/install_node.py` (SHA-256 checked); the global Node is not changed |
| npm | 10.9.9 (bundled with Node 22.23.4) | |
| Next.js | 16.4.3 | App Router, Turbopack build |
| React / React DOM / react-is | 19.3.1 | |
| TypeScript | 6.0.3 | strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` |
| Tailwind CSS (+ `@tailwindcss/postcss`) | 4.3.3 | |
| Recharts | 3.10.1 | |
| ESLint / eslint-config-next | 9.39.5 / 16.4.3 | includes jsx-a11y, react, react-hooks, typescript-eslint |
| @playwright/test | 1.64.0 | drives the installed Microsoft Edge (`channel: "msedge"`); no browser download |
| server-only | 0.0.1 | |

All versions are exact (`.npmrc`: `save-exact`), and `package-lock.json` is committed. Some
transitive packages are held by `overrides` at their newest release on or before 2026-10-10
(project two-week rule); see `docs/reports/phase-3a.md` §2 for the exceptions and why.

## Commands (from `frontend/`, with the project Node on PATH)

```sh
export PATH="$(python -I ../scripts/dev/install_node.py --print-bin):$PATH"   # Git Bash
npm ci                     # install exactly the lockfile (PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 is fine)
npm run dev                # http://127.0.0.1:3001 (dev)
npm run build && npm start # http://127.0.0.1:3001 (production)
npm run typecheck          # tsc --noEmit (strict)
npm run lint               # eslint, 0 warnings allowed
npm test                   # unit + component tests (tsc → node:test)
npm run check:bundle       # T42: no token value / secret names in the build output
npm run test:e2e           # Playwright against the REAL backend (must be running on :8000)
```

`npm run dev|build|start` go through `scripts/next.mjs`, which refuses any Node other than
`.nvmrc` and sets `NEXT_TELEMETRY_DISABLED=1` for the process (nothing written to the global
Next.js config).

## Configuration (server-side only)

`frontend/.env.local` (git-ignored), created from the root `.env` by
`python -I scripts/dev/init_frontend_env.py --project-id <id>` (never prints the token):

| Variable | Meaning |
|---|---|
| `LLMI_API_BASE_URL` | Backend origin; must be loopback (`127.0.0.1`, `localhost`, `[::1]`), no credentials/path/query |
| `LLMI_API_TOKEN` | The backend's `LLMI_ADMIN_TOKEN`; server-only |
| `LLMI_DASHBOARD_PROJECT_ID` | Default project, shown when the URL has no `?project=` (Phase 3C: other projects are chosen in the selector) |
| `LLMI_API_TIMEOUT_MS` | Optional request deadline, 100–30000 (default 5000) |

No `NEXT_PUBLIC_*` variable exists or is allowed (ESLint rule + static test).

## Architecture

```
browser ──GET──▶ proxy.ts (loopback Host only, GET/HEAD, CSP nonce; POST only for Server Actions on the form pages, ARCHITECTURE.md §5.8)
                   ├─▶ app/page.tsx (Overview) ─────────▶ lib/server/overview.ts ─┐
                   ├─▶ app/models/page.tsx (Phase 3C) ───▶ lib/server/models.ts ───┤
                   └─▶ app/traces/page.tsx, [traceId] ───▶ lib/server/traces.ts ───┤
                         (validated query + ?project=)      lib/server/context.ts ─┴─▶ lib/server/backend.ts ──Bearer──▶ FastAPI /api/v1/*
                                                           (project scope + listing)   (path allowlist, explicit project scope, timeout,
                                                                                        size cap, no redirects, lossless JSON, schema decode)
```

- `app/` — routes: `layout.tsx` (shell + backend readiness), `page.tsx` (Overview), `traces/page.tsx` (trace list), `traces/[traceId]/page.tsx` (trace detail), `loading.tsx` per route, `error.tsx`, `not-found.tsx`.
- `components/` — design system: `app-shell`, `side-nav`, `page-header`, `metric-card`, `chart-card`, `data-table`, `states` (empty/error/notice/skeleton), `status-indicator`, `provenance-badge`, `filter-bar` + `navigation-transition` (client: transition navigation, refresh), `charts/trend-charts` (client, Recharts), `overview/*` sections.
- `components/traces/` — `trace-filters`, `trace-list` (table ≥ md, cards below), `trace-values` (status, tokens, cost, ids), `trace-detail-sections` (provenance notice, facts, status breakdown, logical calls, attempts, span table) and `waterfall` (client: ARIA tree, keyboard, bounded rows, selected-span panel).
- `lib/server/` — `server-only` modules: `config.ts` (env validation), `backend.ts` (the only backend client), `context.ts` (Phase 3C: the page's project scope, the project listing, the echoed-project check), `overview.ts`, `models.ts` and `traces.ts` (page loaders).
- Phase 3C: `app/models/` (Model comparison), `components/models/model-comparison.tsx`, `components/charts/model-charts.tsx`, `components/project-selector.tsx`, `components/range-form.tsx` (custom UTC range), `components/auto-refresh.tsx`; `lib/project.ts`, `lib/model-query.ts`, `lib/model-groups.ts`, `lib/auto-refresh.ts`.
- Phase 4B: `components/reviews/` (review section, review and decision forms, diagnostics panel), `components/datasets/` (dataset and test-case forms, case list), `app/datasets/`, `app/traces/[traceId]/reviews/[reviewId]/test-case/`; `lib/actions/{reviews,datasets}.ts` (the only `"use server"` modules: four Server Actions, each re-validating its form and calling one allowlisted write), `lib/server/{reviews,datasets,review-page}.ts`, `lib/review-forms.ts` (form parsing), `lib/action-state.ts`, `lib/api/{review-types,review-schema,diagnostics,ids}.ts`. Writes go through `sendJson` in `lib/server/backend.ts` (method + path allowlist).
- Phase 4D: `app/experiments/`, `components/experiments/` (setup form, report sections, value renderers), `components/charts/outcome-chart.tsx`; `lib/actions/experiments.ts` (the fifth Server Action, ADR-26: mode always `mock`), `lib/experiment-form.ts`, `lib/server/experiments.ts`, `lib/api/experiment-{types,schema}.ts` (strict report decoding with the report's own invariants).
- `lib/api/` — response types mirroring `backend/app/services/metrics.py`, decoders, failure kinds and copy.
- `lib/` — pure helpers: `validate.ts`, `decimal.ts` (BigInt money), `format.ts` (UTC, en-US), `time-range.ts`, `trend.ts`, `provenance.ts`, `request-guard.ts`; Phase 3B: `json.ts` (lossless JSON: integers above 2^53 keep their digits), `nanos.ts` (BigInt nanosecond math), `trace-query.ts` (trace-list URL state), `span-tree.ts` (span hierarchy, waterfall geometry, logical calls), `api/trace-types.ts` and `api/trace-schema.ts`.

**Data rules:** the frontend never computes an alternative metric. Counts, rates,
percentiles and costs are the backend's (M01–M14 overview, M15 compare). Money stays a decimal
string and is formatted with BigInt arithmetic; a float is used only for bar heights. `null`
is shown as unknown / not reported / no data, never as 0. Provenance is shown as the
backend's buckets with text labels; "real" is always called an instrumentation claim.

**Time windows:** presets 6 h / 24 h (hourly buckets) and 7 d / 31 d (daily), all UTC. Each
window ends at the next bucket boundary after now, so every trend bucket is full width; the
backend echoes the window and the page shows the echo. Custom ranges (Phase 3C, every page):
the form's `from_utc`/`to_utc` fields are UTC by name; links carry ISO `Z` `from`/`to`; both are
validated (real instants, start before end, at most 31 days at microsecond precision) and a
refused range falls back to the default with the reason shown.

**Project context (Phase 3C):** every page reads exactly one project: `?project=<id>` or the
configured default. An invalid id is refused before any request (never replaced by another
project); an unknown one is the backend's `404`. The selector is a plain GET form (a full page
load, so nothing of the previous project survives the switch) that keeps the filters and window
but never a cursor, page or selected span; from a trace detail it opens the new project's list.
The navigation keeps an explicit project. Responses that echo a different project are rejected.

**Model comparison (Phase 3C):** `GET /api/v1/models/compare` groups in backend order (by
attempts), never ranked; exact Decimal cost with unknown/incomplete states, tokens with unknown
counts and estimated usage flagged, latency with `n` and low-sample flags, provenance per group,
SYNTHETIC banner. Charts: attempts (not failed / failed) and p95 latency per group, each with a
table view; no cost chart (a partial known cost must not look comparable).

**Auto-refresh (optional, off by default):** 30 s / 1 min / 5 min, re-renders the same URL,
never while a request is pending, postponed while the tab is hidden, stops after 30 minutes,
disabled on later cursor pages. Polling, not live data.

**Trace explorer:** only the filters `GET /api/v1/traces` implements (UTC window ≤ 31 days as
a preset or a fixed `from`/`to`, `status`, `provenance`, exact `model`, page size). Phase 3C
(R40): a "Providers · models (calls)" column lists every provider/model combination of a trace
from the additive `usage.models` (up to 3 shown, the rest counted); there is still no provider
filter. Paging follows the opaque
cursor and freezes the backend-echoed window. Detail links carry the list state (`?list=`),
which the back link re-parses so only canonical list parameters survive. Nanosecond values stay
strings/BigInt end to end; the span tree tolerates several roots, orphans, truncated parents and
client-made parent cycles; failures are shown as recorded (`error.type`), never explained.

**Security:** see `docs/SECURITY.md` §5 and `docs/reports/phase-3a.md` §4. Local use only; no
login exists, so the dashboard must not be exposed beyond loopback.

## Tests

- `tests/unit/` (node:test after `tsc -p tsconfig.test.json`): formatting, decimals, time
  ranges, provenance, request guard, config, decoders (incl. responses recorded from the real
  backend in `tests/fixtures/*.live.json`), the backend client with a fake `fetch`
  (statuses, timeout, abort, oversize, malformed, no token in errors or logs), server-rendered
  components (unknown vs zero cost, empty/error states, XSS escaping) and static boundary checks.
- `tests/e2e/` (Playwright): every expectation is computed from a direct backend API call for
  the same window; also the outage instance on :3002, the wrong-token instance on :3003,
  Host/method guard, no token in any response, keyboard navigation, console cleanliness, and
  responsive layouts. Phase 3C (R41): the global setup (`tests/setup/e2e-global-setup.ts` →
  `../scripts/dev/e2e_fixtures.py`) creates this run's own projects, seeds SYNTHETIC traces at
  current UTC times through `/v1/traces` and exposes their ids (`tests/e2e/e2e-env.ts`); every
  page is opened with `?project=<run project>`; the teardown deletes exactly those projects.
  `LLMI_E2E_KEEP=1` keeps them. Needs the backend on :8000 and PostgreSQL; no other data needed.
- `tests/fixtures/overview.ts` and `tests/fixtures/traces.ts` are SYNTHETIC test data; production code never imports them. `*.live.json` were recorded from the real backend (synthetic telemetry).
