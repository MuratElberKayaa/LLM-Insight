# Phase UI-3 — KPI tiles

**Date:** 2026-11-16 (Europe/Istanbul).

**Scope:** step 3 of the user-requested UI refresh. It brings the eight Overview KPI tiles closer to the approved mockup. The tiles only re-draw values the backend already returns; no new metric is derived and nothing is re-summed.

**Fixed for this step:**
- no new dependency;
- no backend, API, ADR or contract change;
- no provider request.

## 1. Changes (frontend only)

| File | Change |
|---|---|
| `components/metric-card.tsx` | New optional `visual` slot. Restyled: 18 px padding, `rounded-xl`, a 28 px headline value, an 11 px footer. Order: label → value → detail → flags → visual → footer. The value `<p>` keeps the same `data-testid`, `data-state` and text. |
| `components/overview/kpi-visuals.tsx` (new) | Server components, no JavaScript. **`Sparkline`:** one bar per backend trend bucket, on a 0..max scale. An empty bucket is a 2 px stub in the grid color, so it never reads as a small value. `role="img"` with a summary label; each bar has a `title`. **`RateSplit`:** error rate per provenance on an absolute 0–100 % scale, never rescaled. Rows with n = 0 are omitted, and the split is omitted when only one provenance has data. **`ErrorTypeChips`:** the first three error types in backend order, then "+N more"; a null type reads "not reported". |
| `components/overview/kpi-grid.tsx` | Traces and Inference attempts get sparklines from `data.trend`, the same series as the trend charts. Trace error rate gets the per-provenance split. Inference error rate gets the error-type chips. All other content, flags and footers are unchanged: "Incomplete: X of Y unpriced", "Unknown is never counted as $0", "Low sample (p95)" and the rest. |
| `tests/unit/kpi-visuals.test.tsx` (new) | 8 tests, listed in §2. |

**Kept from the current UI, unlike the mockup:** the explanatory footers, such as "Unknown is never counted as $0" and "Trace-level; separate from the inference error rate". They carry the project's honesty rules (ADR: unknown cost is not zero; failure cause is a hypothesis). They are smaller now, but still visible.

## 2. Tests and checks

All commands ran in `node:22.23.4-bookworm-slim`, with `frontend/` mounted and `node_modules` in the `llmi-fe-node-modules` volume.

| Command | Result |
|---|---|
| `npm test` | **379 pass / 0 fail / 0 skip** (UI-2: 371) |
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `node scripts/next.mjs build` | success |
| `npm run check:bundle` | 463 files, 0 findings |

**What the 8 new tests check:**
- the sparkline bucket count, maximum, bar heights and accessible summary;
- an empty bucket gets a grid-colored stub;
- an all-empty window and an empty list;
- `RateSplit` uses an absolute scale and omits empty provenances;
- `RateSplit` is omitted when only one provenance has data;
- the chip order and "+N more";
- `KpiGrid` wiring on the fixture;
- an empty window draws no visual.

**One failure during the run:** the first run had 1 failing test. The cause was in the test: its height regex also matched the sparkline container. The regex now matches only the bars; the component did not change.

**Browser check:**
- **Setup:** the local postgres and backend containers were started, then stopped again. Headless Edge, project 1, 31-day range, 1440 px in both themes and 390 px.
- **Sparkline label:** "Traces per bucket: 5 of 31 buckets with data; peak 84 in 2026-11-12 (UTC day)".
- **Split and chips:** the provenance split is present; the chips read 429 ×5, 500 ×5, 504 ×3.
- **Phone width:** no horizontal scroll at 390 px.

## 3. Risks and open items

- **E2E suite:** not run. The selectors it uses are unchanged (value test ids and texts, `data-metric`, "Incomplete: …"), but a full run is owed before a commit.
- **Sparkline at fine resolution:** a custom range with many hourly buckets draws bars without gaps; there is no rendering problem. The exact series stays in the trend charts and tables.
- **Small samples:** the provenance split shows small-n rows (e.g. Mixed n = 1) as they are. The `title` on each row gives n.
- **Next steps (UI-4 waterfall, UI-5 experiment diff):** wait for the user's decision.
