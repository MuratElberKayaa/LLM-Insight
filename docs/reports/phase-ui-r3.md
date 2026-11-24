# Phase UI-R3 — Model analytics, session explorer, collapsible sidebar

**Date:** 2026-11-23 (UTC). **Branch:** `feature/phase-10-medium-term`, uncommitted, on top of `daa6430` with the uncommitted UI-R1 and UI-R2 work (kept, including `docs/reports/assets/ui-r2/`).

**Scope:** step 3 of the frontend redesign ("FAZ UI-3"). Models and Sessions are rebuilt as analysis views, the app sidebar collapses, and the trace explorer gets three panels at 1280 px. Datasets and Experiments are not redesigned.

**Fixed for this step:**
- no backend code, API, database or URL contract change; the new URL parameters are additive and read only by the dashboard;
- no new dependency; no provider request;
- `.env` files and dev-database rows were not read or written;
- no commit, push, PR or merge.

## 1. Models: before and after

**Before (UI-R2):**
- a header, filter rows outside any panel, three notices, two charts (attempts; p95 latency only) and the comparison table;
- the only way to compare two groups was to read two table rows.

**After:**
- **Header and scope:** eyebrow "Analyze", the sticky context bar (project, window, *attempt* provenance, attempt status, provider, model), and one "Scope and filters" panel (project, window, provenance, attempt status, groups, provider/model, custom range).
- **Side by side** (`model-pair`, new). Two groups chosen in a GET form, or with "Compare as A / B" on any table row. The choice travels as `?a=` / `?b=`: each is a group key (`["provider", "requested", "response", "prompt"]`) checked for shape and length before use and never sent to the backend. It is one captioned table with a row per measure:
  - provenance per attempt;
  - physical attempts;
  - error rate with failed of attempts;
  - inference latency p50 · p95 · p99 on a scale shared by both sides (bar to p95, ● p50, | p99), with n and the low-sample flags;
  - time to first chunk (recorded values only);
  - input, cached input, output and reasoning tokens, with cached and reasoning labelled "part of input/output, not added";
  - token source;
  - known cost with a completeness bar (priced vs unknown attempts; unknown is never $0).
- **Differences.** Below the table, a list says what differs: provider, requested and response model, prompt version, provenance mix, sample size, cost completeness, estimated tokens and streaming. Each line carries `=` / `≠` and text. A fixed note says the groups are observed, not a controlled experiment, that no statistical test is run and that no group is named better. A linked group that the current filters no longer return is reported (`pair-missing`).
- **Latency chart:** p50, p95 and p99 per group, one hue in three steps (light → dark) with a legend; the table view gained p99.
- **Group → traces** (`group-traces-link`, table and cards). It carries provider and model (requested, else response, if the trace list accepts its format) and the window, which mean the same on the trace list (one usage row with that provider and model, ARCHITECTURE.md §5.11). Prompt version, attempt status and attempt provenance are **not** carried: the list has no prompt filter, and its status and provenance are trace-level. The link says so ("any prompt version").
- **Unchanged:** the requested → response model display, reasoning never added to output, "Groups are not ranked", the list-price reference "not billed".

## 2. Sessions: before and after

**Before:** a header, range links and the table of sessions (pseudonym link to the trace list).

**After:**
- eyebrow "Observe", the context bar and one scope panel;
- the table, plus cards below `md`, with a "Timeline" column: "Inspect →", or "Selected" for the open session (`aria-current`);
- **Selected session** (`session-detail`, `?session=` deep link, the backend's hash format only; anything else is ignored and never sent):
  - the backend's own aggregates for that session (traces with failed, attempts, tokens in/out, known cost with "lower bound", first → last trace start);
  - its traces **in time order on one axis**. They come from the existing `GET /api/v1/traces?session=` (Phase 10C) in the same window, and each bar is the trace's stored start and end;
  - each row links into the trace explorer with the session as the listing (the rail and context bar then say "one session");
  - "Open in trace list →" and "Close the session".
- **What is not shown or made up:**
  - no session-level metric the API does not return (no session duration, rate or score);
  - no raw conversation id, user id, prompt or reply;
  - the pseudonym is called "a keyed pseudonym, not an anonymous id".
- **Empty state:** says where sessions come from (`gen_ai.conversation.id`, pseudonymised at ingest) and that none is invented.

## 3. Collapsible sidebar and the 1280 px trace explorer

**Sidebar.** From `lg`, a button in the sidebar header collapses it to a 4.5 rem icon rail (`aria-expanded`, `aria-controls="app-sidebar"`, name "Collapse sidebar" / "Expand sidebar").
- Link labels stay in the accessibility tree as visually hidden text.
- A tooltip (`aria-hidden`) shows on hover **and on keyboard focus**.
- Group titles become hairlines.
- The footer keeps the status dot, the lock (with its title) and a vertical theme toggle.

**Where the state lives.** In `ShellFrame`, the root layout's client frame: it survives client-side navigation between pages. A full reload returns to "expanded", because the dashboard stores nothing (no browser storage or cookies; `tests/unit/security-static.test.ts` forbids them, and the theme toggle follows the same rule). This was evaluated and kept on purpose.

**Explorer.** The three-panel switch is now a container query on the explorer (`@[64rem]/explorer`), not the viewport (UI-R2 used 1360 px). So collapsing the sidebar gives the explorer room:
- **1280 px, sidebar open:** the explorer is about 944 px wide. The list is a disclosure above the spans, and the spans and inspector sit side by side.
- **1280 px, sidebar collapsed:** three panels.
- **Hide list panel:** gives the spans the rail's width; "Show list panel" brings it back. It is shell state, so it holds while moving between traces.

**Fix found while doing this:** the UI-R2 rail never actually stuck while scrolling (its `sticky` box was its own height). The sticky box is now the full-height grid cell's child.

**Measured in the browser** (`ui-r3.spec.ts`, Playwright msedge, 1280 × 800):

| State at 1280 × 800 | Explorer layout | Rail | Spans panel | Inspector |
|---|---|---|---|---|
| Sidebar open (16 rem) | rail as disclosure; spans and inspector side by side | — | **584 px** | **336 px** |
| Sidebar collapsed (4.5 rem) | **three panels** | **240 px** | **504 px** | **336 px** |
| Collapsed, list panel hidden | spans and inspector | "Show list panel" | **768 px** | 336 px |

The test asserts the minimum readable widths in three-panel mode (rail ≥ 224, spans ≥ 400, inspector ≥ 320 px), no horizontal page scroll, and that hiding the list widens the spans. The same numbers came out in runs C, D and E. At 504 px the waterfall's own columns (duration, tokens, cost) are left out by its container query; the inspector shows them for the selected span.

**Phone (390 px):** model groups and sessions are cards, the session timeline stacks, and nothing scrolls horizontally (asserted).

## 4. Chart and table language

- **One tooltip surface** for every chart (`components/charts/chart-tooltip.tsx`): raised surface, hairline, shadow, muted title, value rows in ink with a small swatch for the series. The model, trend and outcome charts now share it instead of three copies.
- **Chart cards** match the other cards (rounded-xl, 20 px padding).
- **Numeric table columns** right-aligned with tabular figures (sessions tokens and cost added).
- **States:** the same hover (`raised` wash), selected (accent tint or frame, `aria-current`), empty and unavailable wording ("Unavailable: … No trace is assumed.").
- **Color is never the only carrier:** percentile bars have a legend, a table and tooltips; differences use `=` / `≠` plus text; errors use ✕ plus text.
- The three-step p50/p95/p99 bars are one hue at 40 / 70 / 100 % opacity. The two lighter steps sit below 3:1 against the surface, so they rely on the legend, the table view and the tooltip, as the dataviz relief rule requires.

## 5. Accessibility findings

- **Keyboard:** the skip link is the first stop (asserted after reload). The sidebar toggle works with Enter, and Tab then reaches the first nav link with its tooltip visible (asserted). The span tree keeps one tab stop and arrow navigation in three-panel mode (asserted).
- **Names:** nav links keep their text as the accessible name when collapsed (`getByRole("link", { name: "Traces" })` passes). The toggle names the action. The pair table has a caption and row headers; session timeline bars have screen-reader text ("starts +40.000 s after the session's first trace, lasts 1.400 s").
- **Found and fixed:** the rail's sticky behaviour (§3), and a race in the tests when two pair links were clicked before the first navigation finished (tests only).
- **Open:** the collapsed tooltip is not announced, because the link text already is (deliberate). Focus is not moved into the session detail after "Inspect" (it is a navigation; the detail sits above the table).

## 6. Changed files

| File | Change |
|---|---|
| `frontend/components/shell-frame.tsx` (new, client) | Shell state (sidebar, rail), `SidebarToggle`, skip link and `<main>` |
| `frontend/components/app-shell.tsx` | Uses `ShellFrame`; `sb-*` hooks |
| `frontend/components/side-nav.tsx` | Label span, tooltip span, group hairline |
| `frontend/components/status-indicator.tsx` | `status-label` class (hidden visually when collapsed) |
| `frontend/app/globals.css` | Collapsed-sidebar rules (`.sb-*`) |
| `frontend/components/traces/rail-toggle.tsx` | Container-query panel, hide/show, sticky fix |
| `frontend/components/traces/trace-rail.tsx`, `frontend/app/traces/[traceId]/page.tsx` | `@container/explorer` grid |
| `frontend/lib/model-compare.ts` (new) | Pair key parsing, selection, pair links, differences, group → traces |
| `frontend/components/models/compare-panel.tsx` (new) | Side-by-side panel, `LatencyRange` |
| `frontend/components/models/model-comparison.tsx` | Row actions (traces, A/B), p50/p95/p99 chart card, p99 in the table view |
| `frontend/lib/model-groups.ts` | `p99`, `lowSampleP99` on chart points |
| `frontend/components/charts/chart-tooltip.tsx` (new); `model-charts.tsx`, `trend-charts.tsx`, `outcome-chart.tsx` | Shared tooltip; percentile bars |
| `frontend/components/chart-card.tsx` | Card style |
| `frontend/app/models/page.tsx` | Context bar, scope panel, pair, row links |
| `frontend/lib/session-timeline.ts` (new), `frontend/components/sessions/session-timeline.tsx` (new) | Time-ordered session traces |
| `frontend/components/sessions/session-table.tsx` | Inspect column, cards, alignment |
| `frontend/lib/server/sessions.ts`, `frontend/app/sessions/page.tsx` | `?session=` read and the page layout |
| `frontend/components/auto-refresh.tsx` | The interval select is disabled until hydrated (found by an intermittent E2E failure, §7) |
| `frontend/components/models/compare-panel.tsx` | (also) no doubled screen-reader text for unreported subsets |
| `frontend/playwright.config.ts` | Visual spec not discovered under `LLMI_E2E_CI=1`; screenshot tolerances |
| `frontend/tests/unit/ui-r3.test.tsx` (new) | 22 tests (§7) |
| `frontend/tests/unit/shell.test.tsx` | Reads `shell-frame.tsx` too (skip link and `<main>` moved), with a note |
| `frontend/tests/e2e/ui-r3.spec.ts`, `ui-r3-fixtures.ts`, `ui-r3-visual.spec.ts` (new) + `ui-r3-visual.spec.ts-snapshots/` (12 baselines) | §7 |
| `scripts/dev/seed_trace_fixtures.py`, `scripts/dev/e2e_fixtures.py` | `--set ui-r3-sessions` (SYNTHETIC, three conversations) for `add-trace` |
| `backend/tests/unit/test_uir3_sessions_fixture.py` (new) | 3 tests: three pseudonymous sessions of 3, 2 and 1 traces; raw id not kept; one failed synthetic attempt |

## 7. Tests and checks

Commands ran from `frontend/` with Node 22.23.4 unless noted. Final code state:

| Command | Result |
|---|---|
| `npm run typecheck` · `npm run lint` | pass · pass (0 warnings) |
| `npm test` | **552 passed**, 0 failed (530 before + 22 in `ui-r3.test.tsx`) |
| `npm run build` · `npm run check:bundle` | pass · 497 files, 24 in the browser bundle, **0 findings** |
| Mutants (scratchpad, each file restored) | **10/10 killed** (list below) |
| backend `pytest tests/unit` · `ruff check .` · `ruff format --check .` | 1394 passed (3 new) · pass · pass |
| `python -I -m unittest discover -s scripts/qa/tests` (root) | 256 tests OK (15 skipped) |
| Playwright E2E, full suite, disposable `_test` database (run E) | **91 passed**, 0 failed (78 before + 7 `ui-r3.spec.ts` + 6 `ui-r3-visual.spec.ts`) |
| Playwright, after the last two cosmetic fixes (run F: `ui-r3-visual`, `ui-r3`, `traces` specs) | **31 passed** |

The 10 mutants:
1. a pair key of 3 parts accepted;
2. prompt versions always "same";
3. a missing linked group not reported;
4. an unvalidated model carried to the trace list;
5. `status=error` carried to the trace list;
6. "$0.00" for unknown cost;
7. the p95 bar drawn at p99;
8. the session timeline reversed;
9. every session marked selected;
10. the sidebar tooltip on mouse focus only.

**E2E runs.** All ran on a disposable database created, migrated and dropped by the scratchpad runner ("remaining: 0" every time); the dev database and `.env` were not touched.

| Run | Scope | Result | Notes |
|---|---|---|---|
| A (×2) | visual spec, `--update-snapshots` | 6 passed (second try) | The first try failed on a test race: the B link was clicked before the A navigation finished. The test now waits for the URL |
| B | full suite against A's baselines | 87 passed, 1 failed, 3 not run | Visual: passed on new data. The failure was my test assumption (the fixture groups report no reasoning subset). Fixing it also removed a doubled screen-reader text ("not reported (not reported)") |
| C | full suite | 90 passed, 1 failed | `phase6c` "rejected credentials stop polling…": intermittent. Alone ×6 it passed. Root cause in the product: the auto-refresh select was enabled before hydration, so an early change reached no handler. The select is now disabled until hydrated (`useSyncExternalStore`), with a unit test |
| D | full suite | **91 passed** | — |
| E | full suite after the pseudonym-column fix | **91 passed** | Visual baselines matched for the third time on a new database |
| F | UI-R3 specs + traces after the date-cell fix | **31 passed** | — |

**Visual regression (`ui-r3-visual.spec.ts`, 12 baselines).**
- **Covers:** model pair and latency chart (dark and light), session detail (dark and light), model card and session timeline at 390 px, sidebar expanded and collapsed, and the spans panel at 1280 px with the sidebar open and collapsed.
- **How it stays stable:** fixed viewports and schemes, SYNTHETIC fixtures, masks on everything that changes per run (times, windows, context bar, project names, pseudonyms, ids, span details), `maxDiffPixelRatio` 1.5 % and a color threshold of 0.2.
- **Where it runs:** every local run. Under `LLMI_E2E_CI=1` it is not discovered, because the baselines are msedge/Windows renders, CI uses Linux Chromium, and the CI gate fails on skipped tests.
- **Determinism:** baselines made in run A matched in runs B, D and E, each on a fresh database with new run ids, trace ids and times.

**Unit tests (`ui-r3.test.tsx`):**
- pair key validation (9 malformed inputs);
- pair selection and the missing report;
- pair links keep the filters;
- the fixture pair's 7 differences, and that a group with itself differs only in sample size;
- streaming compared only when a side streams;
- the panel: caption, requested → response kept, error counts, subsets not added, unknown cost has no `$0`, differences, "not a controlled experiment" and "no statistical test", never "winner", form hidden fields, empty and missing states;
- the latency range on a shared scale;
- group → trace link: provider and model, no prompt, an invalid model left out;
- table and card actions;
- chart points with p99;
- session timeline order and placement, its rendering and explorer links, not-listed and unavailable states;
- the session table's selection and cards;
- sidebar SSR state, names, focus tooltip and no storage;
- auto-refresh disabled before hydration.

## 8. Screenshots

**Where they are and what they contain.** Stored in `docs/reports/assets/ui-r3/` (9 files; the UI-R2 set in `assets/ui-r2/` is untouched). They are taken by `ui-r3.spec.ts` in run F on the disposable database, from SYNTHETIC fixtures only: the run-owned `e2e-<run>-*` projects, the comparison set and the session set. They show no real data and nothing from Project 1.

**Secret check.** Before every capture the test asserts that the page HTML holds:
- no server-side token;
- no `Bearer …` value;
- no database URL with a password;
- no raw conversation id (`uir3-conv…`).

The PNG files (and the 12 baselines) carry no text chunks (checked). The session pseudonyms in the images are hashes of synthetic test ids.

| File | Shows |
|---|---|
| `ui-r3-models-dark-1440.png` / `ui-r3-models-light-1440.png` | Models with the side-by-side pair, differences, p50/p95/p99 chart and the table with row actions |
| `ui-r3-models-mobile-390.png` | Scope panel and group cards with actions at 390 px |
| `ui-r3-sessions-dark-1440.png` | Session table: wrapped pseudonym, aligned numbers, "Inspect" |
| `ui-r3-session-detail-dark-1440.png` / `ui-r3-session-detail-light-1440.png` | Selected session: backend aggregates and the time-ordered traces (failed one marked ✕) |
| `ui-r3-sessions-mobile-390.png` | Cards and the stacked timeline at 390 px |
| `ui-r3-explorer-1280-sidebar-open.png` / `ui-r3-explorer-1280-sidebar-collapsed.png` | Trace explorer at 1280 px: list as disclosure vs three panels with the icon sidebar (the hostile service name is shown escaped) |

**Bundle** (sum of `.next/static`):

| | UI-R2 | UI-R3 | Δ |
|---|---|---|---|
| JS chunks | 1 153 160 B (19 files) | 1 160 605 B (19 files) | +7 445 B (+0.6 %): `shell-frame` (client) and chart tooltip; no new dependency |
| CSS | 45 005 B | 49 091 B | +4 086 B (collapsed-sidebar rules and new utilities) |

## 9. Left for UI-4 (not started)

1. Datasets and Experiments on the shell, context-bar and scope-panel pattern (as asked, not started).
2. Visual regression baselines for Linux Chromium, so the visual spec can also run in CI.
3. A per-span-name duration baseline, so "slow" can be stated against history (UI-R2 follow-up).
4. Keyboard shortcuts between explorer panels and a resizable rail.
5. Model pair: a stated minimum sample before a side is shown without a warning band, and an export of the side-by-side table as CSV.
