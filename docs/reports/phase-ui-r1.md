# Phase UI-R1 — Dark-first shell and overview hierarchy

**Date:** 2026-11-23 (UTC). **Branch:** `feature/phase-10-medium-term`, uncommitted on top of `daa6430`.

**Scope:** step 1 of the requested frontend redesign ("FAZ UI-1"). It is named UI-R1 here because `reports/phase-ui1.md` … `phase-ui5.md` already record the earlier UI refresh (2026-11). Step 2 is not started.

**Fixed for this step:**
- no backend, API, database or URL/query-parameter change; only the overview's existing `GET /api/v1/traces` read asks for `limit=5` instead of `1`;
- no new dependency; no provider request; `.env` files and dev-database rows were not read or written;
- no commit, push, PR or merge.

## 1. Result

**Shell.** The sidebar has three titled navigation groups:
- **Observe:** Overview, Traces, Sessions (Sessions has its own icon now);
- **Analyze:** Models;
- **Evaluate:** Datasets, Experiments.

The current page gets an accent tint and, on wide screens, a 3 px accent bar. The backend status, the local-only notice and the theme toggle sit together in one footer panel. Below `lg` the sidebar is still a top strip. The skip link, `aria-current`, focus rings and the System/Light/Dark toggle are unchanged. Content is centred with a 1600 px maximum width.

**Page context.** Every page header can carry an eyebrow naming its area ("Observe" on the overview), one `h1` and a rule below. On the overview, the project selector, window, provenance, provider/model filter and custom range are one "Scope and filters" panel (`data-testid="scope-panel"`). Each control keeps its own form, field names and test ids.

**Overview, top to bottom:**

| # | Section | What changed |
|---|---|---|
| 0 | Firing alerts | When a rule fires, a one-line pointer links down to the Alerts card (`alerts-pointer`). It shows nothing when the alert read failed |
| 1–2 | **Traffic & reliability** | Traces, trace error rate, inference attempts, inference error rate (unchanged cards with blue stripe) |
| 3–4 | **Latency** | Inference and trace latency p95 (p50, p99, n), plus a new **Time to first chunk p95** card (M20, below) |
| 5–6 | **Tokens & cost** | Tokens with reasoning; known cost, which now has a **cost completeness bar** (below) |
| — | Trends | Unchanged charts, without the old top margin |
| 7 | Model and provider activity | Card frame; the link to the model comparison is an accent link |
| 8–9 | Error patterns · Alerts | Side by side on wide screens. Alerts is a titled card (`alerts-card`) around the unchanged strip |
| 10 | **Recent traces** | The newest trace summary as before, plus a table of the five newest traces and an "All traces →" link |
| — | Provenance, error types, cost details | Card frames, after the main hierarchy |

**TTFT card (M20).** The value is the backend's p95; the detail line gives p50 and "n = X of Y streaming attempt(s)". With no streaming attempt in scope the card says "No streaming"; with streaming attempts but nothing recorded it says "Not recorded". Neither case shows a number. The low-sample flag shows below 20 recorded values. The `ttfc-footer` sentence moved here from the latency card and adds "Never estimated from duration or tokens."

**Cost completeness bar.** Two segments, priced vs unknown-cost attempts, each with a text label and count, and an `aria-label` ("4 of 6 attempts priced, 2 with unknown cost"). It shows counts of attempts, never dollars. The "Incomplete: X of N unpriced" flag is unchanged.

**Recent traces.** The newest trace keeps `data-testid="latest-trace"`, `data-trace-id`, `data-latest` and `latest-started`. The table (`recent-traces`) has a caption, a row header and one provenance badge per trace, so real, synthetic and mixed traces stay separate. It appears only when there are at least two traces.

**Palette.** Navy/anthracite neutrals and three accent tokens (`accent`, `accent-teal`, `accent-violet`) for navigation, section stripes and links only. Series and provenance colors are unchanged. No gradient and no new animation. The only transition is `transition-colors` on hover.

| Token (dark / light) | Value | Lowest WCAG contrast on canvas/surface/raised |
|---|---|---|
| canvas · surface · raised | `#0a0e15` `#10151f` `#171e2b` / `#f4f6f9` `#ffffff` `#eaeef3` | — |
| ink · ink-2 · ink-3 | `#f0f3f8` `#b3bdcc` `#8a96a8` / `#0f1722` `#3d4a5c` `#566274` | dark 15.0 · 8.8 · 5.6; light 15.5 · 7.7 · 5.3 |
| accent · teal · violet | `#6aa5ff` `#3cc7b8` `#ad8cff` / `#1f5fd1` `#0b766d` `#6d3fd6` | ≥ 4.7 in both themes |

## 2. Changed files (frontend only)

| File | Change |
|---|---|
| `app/globals.css` | New neutral values in the dark `@theme` and in both light blocks (kept identical); three accent tokens; contrast values in the header comment |
| `app/layout.tsx` | `themeColor` follows the new canvas colors |
| `components/icons.tsx` | `SessionsIcon`; brand mark uses `fill-accent` |
| `components/side-nav.tsx` | Observe / Analyze / Evaluate groups; accent current-page marker |
| `components/app-shell.tsx` | 16 rem sidebar, footer panel, bounded main column |
| `components/page-header.tsx` | `eyebrow` prop; new `MetricGroup` (titled `h2` section) |
| `components/metric-card.tsx` | `accent` stripe and `noDataText` props; hover border |
| `components/overview/kpi-grid.tsx` | Three `MetricGroup` sections; TTFT card; completeness bar on known cost |
| `components/overview/kpi-visuals.tsx` | `CompletenessBar` |
| `components/overview/latest-trace.tsx` | Recent traces: newest summary + table; `RECENT_TRACES_LIMIT = 5` |
| `components/overview/alert-strip.tsx` | `AlertsCard`, `firingCount`; the strip lost its bottom margin |
| `components/overview/{breakdowns,error-patterns,model-groups,trend-section}.tsx` | Outer class only (card frame or no margin) |
| `lib/server/overview.ts` | `RECENT_TRACES_LIMIT = "5"` for the existing traces read |
| `app/page.tsx` | Eyebrow, scope panel, new section order |
| `tests/unit/shell.test.tsx` | Expected groups (Models in Analyze), with a note |
| `tests/unit/phase6c.test.tsx` | Expected traces `limit` 5 (was 1), with a note |
| `tests/unit/ui-r1.test.tsx` (new) | 10 tests, §3 |

**Kept:** every `data-testid`, `data-metric`, `data-state` and `data-chart` the e2e and unit tests use; one `h1`; the `Time range` and `Provenance` groups; table captions.

## 3. Tests and checks

All commands ran from `frontend/` with Node 22.23.4 (`.tools/`), unless noted.

| Command | Result |
|---|---|
| `npm run typecheck` | pass |
| `npm run lint` | pass (0 warnings) |
| `npm test` | **513 passed**, 0 failed (503 before + 10 new) |
| `npm run build` | pass, 15 routes |
| `npm run check:bundle` | 494 files scanned, 23 in the browser bundle, **0 findings** |
| Mutants (scratchpad script; each file restored) | **5/5 killed:** TTFT always "known"; "$0" for the unknown count; one provenance for every recent trace; limit back to 1; firing count of all rules |
| Playwright E2E (`npx playwright test`, msedge) against a disposable database | **72 passed**, 0 failed (2.7 min), on the third run; see below |

**How the E2E suite ran.** A scratchpad script (run with the backend venv) did the following:
1. created `llminsight_uir1_e2e_test` on the local PostgreSQL, from the backend's own settings (as the integration tests do);
2. migrated it and seeded the prices;
3. started the backend on 127.0.0.1:8000;
4. ran Playwright with `LLMI_DATABASE_URL` and `LLMI_API_BASE_URL` in the process environment only;
5. dropped the database at the end ("remaining: 0" after every run).

The dev database and the `.env` files were not read or changed.

The three runs:

| Run | Result | Cause |
|---|---|---|
| 1 | Did not start | The local `.env` puts the backend on port 8010. The script waited on 8000; it now sets `LLMI_PORT=8000` for its own backend |
| 2 | 67 passed, 5 failed | Unrelated to UI-R1 (see below) |
| 3 | **72 passed** | — |

The five failures in run 2 were review and dataset creation: `422` for `created_by` and the dataset `name`. **Root cause:** the random run id `27148722459d` contains 11 consecutive digits. The backend's label check therefore rejects `e2e-27148722459d-…` as contact data (`redaction.label_is_sensitive` returns True; checked directly). An id with letters passes. This is a **pre-existing E2E fixture flake** in `scripts/dev/e2e_fixtures.py`: 12 random hex characters can contain a long digit run. It was not changed in this UI step. Proposed fix: draw the run id so it never contains a run of 7 or more digits (e.g. start every 4 characters with a letter).

**New tests (`ui-r1.test.tsx`):**
- the KPI headings and card order;
- TTFT without streaming, not recorded, and recorded with the low-sample flag;
- the completeness bar (labels, no `$`, nothing when empty) and its place in the known-cost card;
- the loader and component limit;
- the recent-traces table (caption, links, one badge per provenance, "All traces" link) and no table for one trace;
- the Alerts card and `firingCount` (including a failed read counts 0).

**Bundle size** (sum of `.next/static`; the baseline is `daa6430` built in a throwaway worktree):

| | HEAD `daa6430` | UI-R1 | Δ |
|---|---|---|---|
| JS chunks (18 files) | 1 135 745 B | 1 140 563 B | +4 818 B (+0.4 %) |
| CSS | 35 481 B | 39 694 B | +4 213 B (new utility classes) |

The baseline build used a wider `turbopack.root` in the throwaway copy only, so that it could resolve `node_modules` through a junction. Treat the JS delta as approximate. No new client component was added; `side-nav.tsx` was already a client component.

## 4. Evidence (screenshots)

**How they were taken.** The dashboard ran on `127.0.0.1:3001` (production build) against the local dev backend (its own `.env`), and both were stopped afterwards. The window was a custom range, 2026-11-12 → 2026-11-14 UTC (project #1, real asserted Groq/Gemini comparison traffic). Every request the browser made was a `GET`. The images are in the session scratchpad (`shots/`), not committed:

| Image | Shows |
|---|---|
| `overview-dark-1440.png` / `overview-light-1440.png` | Full overview in both themes |
| `overview-dark-viewport.png` | First screen |
| `overview-focus.png` | Keyboard focus after skip link and two tabs (lands on "Traces") |
| `overview-mobile-390.png` | Top-strip navigation and one-column cards |
| `traces-dark-1440.png`, `sessions-dark-1440.png` | Other pages inside the new shell |

**What the real data shows:**
- 96 traces and 96 attempts, error rate 3.13 %;
- latency p95 8.15 s with "Low sample (p99)";
- the TTFT card says "No streaming" (no streaming attempt in the window), with no number;
- known cost "Unknown: no call priced", with the completeness bar at Priced 0 / Unknown cost 96 and no $0;
- one error pattern (Gemini 504 ×3);
- Alerts: none firing, 2 checked, 2 without enough data;
- the five newest traces, each "Real (asserted)".

## 5. Not changed / risks

- The alert pointer and the Alerts card read the same `GET /api/v1/alerts` result; no new request.
- Error patterns still render nothing when the window has none (Phase 10D behaviour). In that case Alerts is alone in its row.
- The "All traces" link opens the trace list with its default window, not the overview's custom range. Carrying the window over needs a mapping between the two window models (UI-2).
- Recharts colors are unchanged, so the charts keep their earlier look inside the new frames.

## 6. Left for UI-2 (not started)

1. Carry the overview window and filters into the trace and model links.
2. Bring the trace list, trace detail, sessions, models, datasets and experiments pages onto the same card, eyebrow and scope-panel pattern.
3. A compact top context bar (project · window · provenance) that stays visible on scroll.
4. Chart restyle on the new surfaces (grid, axis, tooltip) and a stated empty-bucket legend.
5. A loading skeleton for the KPI groups during navigation (today the pending region dims the content).
6. Visual regression snapshots for both themes in the e2e suite.
