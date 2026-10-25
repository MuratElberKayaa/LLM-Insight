# LLM Insight dashboard (frontend)

Local-only Next.js App Router dashboard for the LLM Insight backend. Phase 3A delivers the
foundation (shell, design system, server-side API layer) and the **Overview** page. Trace
explorer, model comparison page, reviews and experiments are not built yet.

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
| `LLMI_DASHBOARD_PROJECT_ID` | Project shown (the API needs one; there is no project listing) |
| `LLMI_API_TIMEOUT_MS` | Optional request deadline, 100–30000 (default 5000) |

No `NEXT_PUBLIC_*` variable exists or is allowed (ESLint rule + static test).

## Architecture

```
browser ──GET──▶ proxy.ts (loopback Host only, GET/HEAD only, CSP nonce)
                   └─▶ app/page.tsx (server component, dynamic)
                         └─▶ lib/server/overview.ts ──▶ lib/server/backend.ts ──Bearer──▶ FastAPI /api/v1/*
                               (validated query)          (fixed paths, timeout, size cap,
                                                           no redirects, schema decode)
```

- `app/` — routes: `layout.tsx` (shell + backend readiness), `page.tsx` (Overview), `loading.tsx`, `error.tsx`, `not-found.tsx`.
- `components/` — design system: `app-shell`, `side-nav`, `page-header`, `metric-card`, `chart-card`, `data-table`, `states` (empty/error/notice/skeleton), `status-indicator`, `provenance-badge`, `filter-bar` + `navigation-transition` (client: transition navigation, refresh), `charts/trend-charts` (client, Recharts), `overview/*` sections.
- `lib/server/` — `server-only` modules: `config.ts` (env validation), `backend.ts` (the only backend client), `overview.ts` (page loader).
- `lib/api/` — response types mirroring `backend/app/services/metrics.py`, decoders, failure kinds and copy.
- `lib/` — pure helpers: `validate.ts`, `decimal.ts` (BigInt money), `format.ts` (UTC, en-US), `time-range.ts`, `trend.ts`, `provenance.ts`, `request-guard.ts`.

**Data rules:** the frontend never computes an alternative metric. Counts, rates,
percentiles and costs are the backend's (M01–M14 overview, M15 compare). Money stays a decimal
string and is formatted with BigInt arithmetic; a float is used only for bar heights. `null`
is shown as unknown / not reported / no data, never as 0. Provenance is shown as the
backend's buckets with text labels; "real" is always called an instrumentation claim.

**Time windows:** presets 6 h / 24 h (hourly buckets) and 7 d / 31 d (daily), all UTC. Each
window ends at the next bucket boundary after now, so every trend bucket is full width; the
backend echoes the window and the page shows the echo.

**Security:** see `docs/SECURITY.md` §5 and `docs/reports/phase-3a.md` §4. Local use only; no
login exists, so the dashboard must not be exposed beyond loopback.

## Tests

- `tests/unit/` (node:test after `tsc -p tsconfig.test.json`): formatting, decimals, time
  ranges, provenance, request guard, config, decoders (incl. responses recorded from the real
  backend in `tests/fixtures/*.live.json`), the backend client with a fake `fetch`
  (statuses, timeout, abort, oversize, malformed, no token in errors or logs), server-rendered
  components (unknown vs zero cost, empty/error states, XSS escaping) and static boundary checks.
- `tests/e2e/` (Playwright): every expectation is computed from a direct backend API call for
  the same window; also the outage instance on :3002, Host/method guard, no token in any
  response, keyboard navigation, console cleanliness, and responsive layouts.
- `tests/fixtures/overview.ts` is SYNTHETIC test data; production code never imports it.
