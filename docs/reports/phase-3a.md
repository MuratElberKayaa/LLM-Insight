# Phase 3A — Frontend Foundation and Overview Dashboard

- **Date:** 2026-10-25 (UTC; the machine's local clock is UTC+3).
- **Scope (user instruction "Phase 3A"):** lock the frontend toolchain, create `frontend/` (Next.js App Router, strict TypeScript), a dark-first design system, a secure server-side backend access layer, and the **Overview** page on the real `/api/v1/overview` and `/api/v1/models/compare`; verify with a real local round trip.
- **Not done (by instruction):** Phase 3B/3C (trace explorer, span tree, model comparison page), any backend contract change, real OpenAI calls, public deployment, push, `phase-3` tag.
- **Untouched:** the home-directory repository, the global Git config, the global Node.js installation (22.11.0), the service on port 3000, the Windows PostgreSQL on 5432, other containers.

## 1. Baseline (Step 1)

| Check | Result |
|---|---|
| Git root | `C:/Users/PC/Desktop/LLM_Insight_Claude_Code_MVP_Paketi` (independent repository) |
| Branch / HEAD / tree | `main`, `75ff8c3`, clean |
| Tags | `phase-0`, `phase-1`, annotated `phase-2` → `75ff8c33f579…` |
| PostgreSQL | `llminsight-postgres-1` `Up (healthy)`, `127.0.0.1:55432`; dev DB `0002 (head)`; 0 projects, 6 price rows (Phase 2 closure) |
| Backend | `python -m app` → `GET /health/ready` `{"status":"ready"}` on `127.0.0.1:8000` |
| Ports | `3000` taken (two listeners, left alone, R19); `3001`, `3002`, `8000`, `8100` free before use |
| Contracts read | `CLAUDE.md`, `MVP_MASTER_PLAN.md`, `ADR.md`, `ARCHITECTURE.md` (§1–3, §5–8), `PRD.md`, `METRICS_CATALOG.md`, `SECURITY.md`, `TEST_MATRIX.md`, `PHASE_STATUS.md`, `CLAUDE_PHASE_PROMPTS.md`, `reports/phase-2-final.md` |
| Implementation inspected | `backend/app/api/{overview,traces,admin,health}.py`, `backend/app/services/metrics.py`, `backend/app/repositories/metrics.py` (queries and nullability), `backend/app/models/*` |
| Backend contract change needed | **None.** One gap was solved on the frontend side: there is no project listing endpoint, so the project id is server configuration (`LLMI_DASHBOARD_PROJECT_ID`) |

## 2. Toolchain and exact versions (Step 2)

**Node.js.** Installed 22.11.0 globally. Node **22.23.4** (2026-10-20, LTS "Jod", security release, npm 10.9.9, verified in `https://nodejs.org/dist/index.json`) was installed **project-scoped** by the new stdlib script `scripts/dev/install_node.py`: official `node-v22.23.4-win-x64.zip`, SHA-256 `cbc74b5e0982c9dbb41525fd8fb5dcd32fbee237863f7829072f4639a1ce5f20` matched `SHASUMS256.txt` before extraction into the git-ignored `.tools/`. Commands prepend its bin directory to `PATH` for the frontend only; `node --version` outside that PATH still prints `v22.11.0`. `frontend/.nvmrc`, `"engines": {"node": "22.23.4"}` with `engine-strict=true`, and `frontend/scripts/next.mjs` refuse any other version. The SHASUMS file's GPG signature was not verified (no release keys on the machine).

| Package | Pinned | Published | Reason |
|---|---|---|---|
| next | 16.4.3 | 2026-10-23 | User target. 16.4.0 (the two-week candidate) has critical unauthenticated RCE advisories (GHSA-673q-mjm6-r5xr, GHSA-2464-fhr4-w9xw) and a high DoS (GHSA-p3m5-59mp-jv5r), fixed in 16.4.1; 16.4.2/16.4.3 are backport releases |
| react, react-dom, react-is | 19.3.1 | 2026-10-14 | Compatible with Next (`^19.0.0`); RSC transport hardening released with the Next security fix |
| typescript | 6.0.3 | 2026-04-16 | User target; resolves R07: `typescript-eslint` 8.x requires `<6.1.0` |
| recharts | 3.10.1 | 2026-07-25 | Peer `react ^19`, needs `react-is` |
| tailwindcss, @tailwindcss/postcss | 4.3.3 | 2026-07-16 | Locked architecture |
| eslint | 9.39.5 | 2026-07-10 | ESLint 10 fails strict peer resolution (`eslint-plugin-react`, `-import`, `-jsx-a11y` declare ≤ 9). R36 |
| eslint-config-next | 16.4.3 | 2026-10-23 | Matches Next |
| @playwright/test | 1.64.0 | 2026-10-07 | Uses installed Microsoft Edge (`channel: "msedge"`, Edge 156); no browser download |
| @types/node / @types/react / @types/react-dom | 22.20.5 / 19.3.0 / 19.3.0 | ≤ 2026-10-01 | Node 22 types |
| server-only | 0.0.1 | 2022-09-03 | Build-time server/client boundary |

- **Lockfile:** `frontend/package-lock.json` (lockfileVersion 3, 406 packages installed). `npm ci` reproduces it. `npm ls --all` exit 0. `npm audit`: **0 vulnerabilities**.
- **Two-week rule:** 40 locked packages were initially newer than 2026-10-10. Besides the security-driven exceptions above (Next and its `@next/*` platform packages, React), every other one is held by `overrides` at its newest earlier release, after checking each dependent's range: the `typescript-eslint` family 8.71.1, `browserslist` 4.29.3 with `caniuse-lite`, `node-releases`, `electron-to-chromium`, `baseline-browser-mapping`, `@babel/{parser,types,helper-string-parser}`, `immer` 11.1.21, `es-toolkit` 1.52.0, `ignore` 7.0.12, `napi-postinstall` 0.3.4, and `postcss` 8.5.29 under `@tailwindcss/postcss`. Holding `zod` at 4.6.5 made `npm audit` report GHSA-mrhx-c7xc-m3w5 (high), so `zod` is pinned to **4.6.6**, the first fixed release (2026-10-11; dev-only, via `eslint-plugin-react-hooks`). Remaining newer-than-rule packages: Next.js 16.4.3 (+ `@next/*`, `eslint-config-next`), React 19.3.1 trio, `zod` 4.6.6 (R37).
- **Test tooling decision:** Vitest 5.0.3 + Vite 8 crashed npm 10.9.9 (`Cannot read properties of null (reading 'edgesOut')`, an optional peer cycle `vite → @vitejs/devtools-vitest → vitest@*`). Instead of a larger stack, unit and component tests are compiled with the pinned TypeScript and run on Node's built-in `node:test` (no extra dependency). Playwright is the master plan's E2E runner and uses the installed Edge.
- `npm install` printed an EPERM cleanup warning for an optional wasm package; afterwards `npm ls` lists 6 such optional packages as "extraneous" (they are in the lockfile; an npm reporting quirk for hoisted optional dependencies of a skipped platform package). Not a lockfile drift.

## 3. Frontend architecture (Steps 3–4)

Full description: `frontend/README.md`; contract: `ARCHITECTURE.md` §5.3.

- `frontend/` is an independent package; the backend, SDK and PostgreSQL build nothing from it, and it imports nothing from them.
- **App Router:** `app/layout.tsx` (shell, backend readiness in a Suspense boundary), `app/page.tsx` (Overview, dynamic), `loading.tsx` (first-load skeleton), `error.tsx` (no error text shown), `not-found.tsx`, `icon.svg`.
- **Design system (`components/`):** application shell with skip link, left navigation (horizontal strip below `lg`; unbuilt pages listed as "Not built yet", not links), header with a "Local only" badge and backend status, page and section headers, metric cards with three explicit value states (`known` / `unknown` / `no_data`), flags (low sample, incomplete cost), chart container with legend and a table-view twin, filter bar (time range + provenance), status indicator (dot + text), provenance badges (swatch + text), empty / error / notice / skeleton states, accessible table (caption, `scope`, row headers, own horizontal scroll region).
- **Visual system:** dark-first tokens in `app/globals.css` (`@theme`), system font stack (no external font request), tabular numbers in tables and axes only. Series/provenance colours are the dark steps of the dataviz reference palette and were **validated** against the chart surface `#15171c` with `validate_palette.js`: provenance real/synthetic/mixed (blue/orange/aqua) and succeeded/failed (blue/status red) pass lightness, chroma, CVD (adjacent and all-pairs; worst ΔE 9.4) and 3:1 contrast. Charts: thin columns (≤ 24 px, 4 px rounded data end), 2 px surface gap between stacked segments, hairline solid grid, no dual axis, hover tooltip, legend for ≥ 2 series, and a table view for every chart.
- **Interaction:** filter links are real anchors (work without JavaScript); with JavaScript they navigate inside a React transition, so the previous render stays visible, dimmed and `aria-busy`, instead of a skeleton flash. A Refresh button re-runs the server render. "Fetched hh:mm:ss UTC · not live" is shown; there is no polling and no live claim.

## 4. Security implementation (Step 5)

- Token server-only: `frontend/.env.local` (git-ignored, `git check-ignore` → `.gitignore:3:.env.*`), created by the new `scripts/dev/init_frontend_env.py` (copies only the admin token, a loopback base URL and the project id; never prints the token; the Next.js process never receives the pepper or the database URL). Read in exactly one module, `lib/server/config.ts`; used in exactly one, `lib/server/backend.ts`; both `server-only`.
- Config validation: loopback-only base URL (http/https on `127.0.0.1`, `localhost`, `[::1]`), bare origin only, token of 32–512 visible ASCII characters (no header injection), placeholder refused, positive int64 project id, bounded timeout. Problem messages never contain the token (tested).
- Requests: closed set of paths, validated parameters only (unsupported URL values fall back to defaults with a notice), `cache: "no-store"`, `redirect: "error"`, `AbortSignal.timeout` (5 s default), 2 MiB response cap (declared and streamed), JSON content type required, full schema decode.
- No proxy surface: no route handlers, no Server Functions (static test). `proxy.ts`: loopback `Host` only (`421` otherwise, DNS-rebinding defence), GET/HEAD only (`405`), per-request nonce CSP, plus static security headers in `next.config.ts`. `poweredByHeader: false`. Next.js telemetry disabled per process.
- Logging: one server-side line per failed backend read with kind, status and schema path only.
- Not done: no login (local trust model, R39); `style-src 'unsafe-inline'` (R38).

## 5. Overview features, tests and evidence (Steps 6–8)

**Overview (only what the contract supports):** KPI cards M01 traces, M05 trace error rate, M02/M03 inference attempts and logical calls, M04 inference error rate, M06 inference latency p95 (p50/p99, `n`, low-sample flags; time to first chunk shown as not recorded), M07 trace latency p95 (root spans, excluded traces), M08/M09 tokens (unknown sums stay "unknown", subsets never added), M10–M12 known cost with the unknown-cost count, "Incomplete" flag and per-priced-attempt cost; provenance banner and breakdown table (backend totals, mixed shown as n/a at usage level); trend charts per backend bucket (attempts split by status, traces split by status, known cost with gaps for buckets without a priced call) with exact table views; failed attempts by `error.type`; M15 model/provider activity (top 10 from `models/compare`, its own error panel if that call fails); M11 unknown cost by reason, M13 price versions, M14 usage source.

### 5.1 Commands and results (final run, Node 22.23.4, after the last code change)

| # | Command (directory) | Result | Exit |
|---|---|---|---|
| 1 | `npm run typecheck` (frontend) | `tsc --noEmit`, strict, no output | 0 |
| 2 | `npm run lint` (frontend) | ESLint 9.39.5, 0 problems, `--max-warnings 0` | 0 |
| 3 | `npm test` (frontend) | **107 tests, 107 pass, 0 fail, 0 skipped** (node:test) | 0 |
| 4 | `npm run build` (frontend) | Next.js 16.4.3 build; `/` dynamic, `/icon.svg` static, Proxy active | 0 |
| 5 | `npm run check:bundle` (frontend) | 376 files scanned (15 in the browser bundle), 1 secret value and 5 secret variable names searched, **0 findings** | 0 |
| 5a | Mutation check of #5: the real token planted in `.next/static/mutation-probe.js` (via script, never printed) | 2 findings, exit **1**; probe removed, rerun exit 0 | 1 / 0 |
| 6 | `npm run test:e2e` (frontend; real backend on :8000; dashboards on :3001 and outage instance on :3002 started by Playwright) | **12 passed, 0 failed, 0 skipped** | 0 |
| 7 | `npm audit` / `npm ls --all` (frontend) | 0 vulnerabilities / clean | 0 / 0 |
| 8 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (backend) | **1082 passed**, 0 failed, 0 skipped, 1 warning (R20) | 0 |
| 9 | `LLMI_TEST_REQUIRE_DB=1 python -m pytest -q -rs -p no:cacheprovider` (sdk, unit + live backend) | **343 passed**, 0 failed, 0 skipped | 0 |
| 10 | `ruff check .` / `ruff format --check .` / `pip check` / `alembic current` (backend) | `All checks passed!` / `86 files already formatted` / no broken requirements / `0002 (head)` | 0 |
| 11 | `docker compose config --quiet`; `docker compose ps` | exit 0; `Up (healthy)` | 0 |
| 12 | `python -I scripts/verify_phase0.py --quiet`; `git diff --check` (root) | `SUMMARY: 726 PASS, 0 FAIL`; no output | 0 / 0 |

**Unit/component coverage (107):** formatting (percent edge cases `<0.01%`/`>99.99%`, ms units, UTC labels independent of the machine zone), decimal money (exact small values such as `$0.0001875`, computed zero `$0.00`, half-even rounding, values beyond float precision), time ranges (UTC bucket alignment, ≤ 31 days, DST day, invalid and repeated parameters), provenance notices (all six cases), request guard (loopback hosts vs `evil.example`, `127.0.0.1.nip.io`, `0.0.0.0`, user-info; methods; CSP), config validation, decoders (valid, empty, forward-compatible extra fields, 15 malformed mutations each rejected at the exact JSON path, plus the two responses **recorded from the real backend** in `tests/fixtures/*.live.json`), the backend client with a fake `fetch` (request shape and bearer header; 401/404/422/429/503/500/418; HTML, invalid JSON, schema mismatch; connection refused; a hanging backend → `timeout` after the deadline; abort; declared and streamed oversize; no token in any error object or log line), server-rendered components (values equal to the fixture, "No data" on empty, unknown cost vs `$0.00`, unknown token sums, SYNTHETIC banner, provenance totals from the backend, **T41** escaping of `<script>`/`<img onerror>` in error types, model/provider/prompt-version labels and price versions, degraded compare section, every failure kind), and static boundary checks (every `lib/server` module is `server-only`; no client component imports server code or reads `process.env`; the token variable is read in exactly one module; no `NEXT_PUBLIC_`, browser storage, cookies or raw HTML; no route handlers or `"use server"`; production code never imports test fixtures).

**E2E (12, real stack, Edge 156, time zone Europe/Istanbul):** (1) KPIs, cost, trend table rows (attempts, failed, exact known cost or "none computed") and model rows equal a direct API call for the window the page echoes; (2) T48 synthetic labelling (`synthetic_only` banner, per-provenance counts, "all provenance", "not verified"); (3) time range and provenance controls change the URL and the backend window (7 d → 168 h ending at UTC midnight, `trend.bucket = day`; `provenance=synthetic` → "synthetic only"; `real` → empty state); (4) no console errors, CSP violations or hydration warnings while navigating, refreshing and hovering a chart; (5) unsupported query values → defaults + notice; (6) **T42 live half:** no response body (HTML, RSC, JS, CSS) contains the token, `LLMI_API_TOKEN`/`LLMI_ADMIN_TOKEN` or a bearer header, and storage/cookies do not hold the token; (7) `Host: evil.example` → 421, `Host: 127.0.0.1.nip.io` → 421, POST → 405, loopback → 200; (8) backend outage (dashboard pointed at closed port 59999): "Backend unreachable" panel and header status, no KPI grid; (9) keyboard skip link, `aria-current`, labelled control groups, one `h1`, every rendered table captioned; (10–12) desktop 1440, tablet 834 and mobile 390 viewports with no horizontal page scroll at any animation frame during load.

### 5.2 Live round trip (Step 7)

1. **Empty database:** `python -m app.cli create-key --project phase3a-demo` → `project_id=1` (the key was captured to a scratch file and never displayed). Dashboard on `127.0.0.1:3001` showed "No telemetry in this window" for project 1 with the backend-echoed window; the E2E run against the empty DB passed 10 and correctly skipped the provenance test (no telemetry). Screenshots: `phase-3a-screenshots/empty/`.
2. **Synthetic data, no OpenAI:** `sdk/.venv/Scripts/python -m demo_app` (mock scenarios A–E; 12 spans exported; scenario E loses its spans by design) and `python -m demo_app.web --drive` (mock and offline fake-OpenAI through `httpx2.MockTransport`; 13 spans exported). `OPENAI_API_KEY` unset; `LLMI_PROVIDER_MODE=mock`.
3. **Backend result for the 24 h window:** 9 traces (all synthetic), 16 inference attempts, 4 failed (`error.type` `429` ×2, `500` ×2), known cost `0.0031500000` USD from 2 priced attempts (offline fake-OpenAI `gpt-5.4-mini-2026-03-17`, price version `openai-2026-10-24`), **14 attempts with unknown cost** (10 `price_missing`, 4 `usage_missing`), low-sample flags on p95/p99.
4. **Dashboard result:** matches (E2E 1–3 and the populated screenshots): "$0.00315" plus "Incomplete: 14 of 16 unpriced"; mock model groups show "unknown" cost, never `$0`; SYNTHETIC banner and badges; trend tables equal the API buckets.
5. **Logs:** backend log (428 lines: 167 overview and 141 compare reads, all `200`; 2 ingest `200`; 1 ingest `401`, the scenario-E rejected key), dashboard logs and demo outputs: **0** occurrences of the admin token, pepper, PostgreSQL password or ingest key, 0 `Authorization` strings, 0 tracebacks.

**Screenshots** (`docs/reports/phase-3a-screenshots/`): `empty/overview-{empty,desktop,tablet,mobile,backend-unreachable}.png` (empty DB) and `populated/overview-{desktop,tablet,mobile,backend-unreachable}.png` (synthetic data). They show SYNTHETIC data only.

### 5.3 Defects found and fixed during verification

- `formatUsd` first rounded small costs (e.g. `$0.0001875` → `≈ $0.000188`); now exact up to 5 significant digits after leading zeros (unit test).
- Mobile/tablet pages overflowed horizontally by up to 547 px: absolutely positioned screen-reader labels inside horizontally scrolling tables escaped the scroll container. The table region is now the containing block (`relative`); the E2E samples every frame from first paint. A wide chart first render was also clipped.
- The model table cut its last column at 1440 px; requested/response models now share one two-line column.
- A missing icon produced a console 404 (found by the console-cleanliness test); `app/icon.svg` added.
- Test harness issues fixed without weakening assertions: `AbortSignal.timeout` timers are unref'd (the timeout test holds a ref'd interval), Next's route announcer also has `role="alert"`, React's empty `<table hidden>` streaming placeholders are excluded from the caption check, and Playwright now always starts the dashboard from the current build (a reused server had served a stale build).

## 6. Test matrix changes

| ID | Before | After | Evidence |
|---|---|---|---|
| T42 Server-only API secret (blocker) | NOT RUN | **PASS (phase-3a)** | Build-output scan with mutation check (§5.1 #5/#5a), live response/storage scan (E2E 6), static boundary tests; re-verify in Phase 5 |
| T41 XSS escape on labels | NOT RUN | **PARTIAL (phase-3a)** | Escaping proven on every telemetry label the Overview renders (component tests, server-rendered HTML). Not yet done: a live E2E with a hostile label ingested through `/v1/traces` and shown in the trace list (Phase 3B) |
| T48 Real vs synthetic provenance on dashboard | PARTIAL (phase-2c) | **PARTIAL (phase-3a)** | Overview banner, badges and breakdown verified live with synthetic data (E2E 2) and for mixed/unknown/real cases in unit tests. Trace-list and span-level badges belong to the trace explorer (Phase 3B) |

T24, T29 and T32 are unchanged (`PASS (phase-2c)`); the dashboard now renders their outputs without recomputation. T43, T45, T46 are unchanged.

## 7. Risks and limitations

- R36 ESLint 9 marked unsupported upstream (dev-only); R37 security-driven exceptions to the two-week rule; R38 `style-src 'unsafe-inline'`; R39 no login (loopback trust model). R07 resolved, R12 mitigated (global Node unchanged by design), R19 handled with port 3001.
- The Overview shows one configured project; there is no project switcher (no listing endpoint; no backend change in this phase).
- Data is a snapshot per render ("not live"); refresh re-queries. No auto-refresh.
- Time ranges are presets only (6 h, 24 h, 7 d, 31 d); no custom range; model/status/prompt-version filters exist in the API but are not exposed yet.
- Dark theme only (dark-first requirement); no light theme.
- All live verification used SYNTHETIC telemetry. No real provider data has been displayed; "real" rendering is covered by unit tests only.
- E2E runs on the developer's dev database and compares against the live API, so it is data-independent but not hermetic; it needs the backend and PostgreSQL running and ports 3001/3002 free.
- The Node archive's checksum was verified; its GPG signature was not.
- Leftover local state (git-ignored, intentional): `.tools/node-v22.23.4-win-x64/`, `frontend/node_modules/`, `frontend/.next/`, `frontend/.env.local`, `frontend/test-results/`; dev DB now holds project 1 (`phase3a-demo`), one ingest key and the synthetic telemetry above.

## 8. Deferred to Phase 3B / 3C (not started)

Trace list with filters and pagination (T45), trace detail span tree and timeline (T46), trace-level provenance badges (rest of T48), live hostile-label E2E (rest of T41), a model comparison page, model/status/prompt-version filters and custom time ranges in the UI, a project selector, auto-refresh, compose service for the frontend, CI workflow. The `phase-3` tag waits for the end of Phase 3.

## 9. Documentation and commit

Changed or added: `frontend/` (new package), `scripts/dev/install_node.py`, `scripts/dev/init_frontend_env.py`, `scripts/verify_phase0.py` (its secret-pattern scan now also skips the git-ignored build/tool output `.next`, `.tools`, `.test-build`, `test-results`, `playwright-report` — it had matched `sk-async` inside file paths in Next.js trace manifests — and a new check that `frontend/.env.local` is git-ignored and untracked), `.gitignore` (`.tools/`), `.env.example` (dashboard variables, placeholders only), `docs/ARCHITECTURE.md` (§5.3, §6 pins and Phase 3A install, §7, §8), `docs/SECURITY.md` (§5, threat table), `docs/SOURCES_AND_RISKS.md` (Phase 3A sources; R07, R12, R19, R36–R39), `docs/TEST_MATRIX.md`, `docs/PHASE_STATUS.md`, this report and its screenshots. `frontend/AGENTS.md` was generated by `next dev` (Next.js 16.4.3 `server/lib/generate-agent-files.js`, verified in the package source) and is committed so dev runs do not dirty the tree. No backend, SDK, schema or migration change.
