# Phase UI-4 — Trace detail: facts strip and waterfall

**Date:** 2026-11-16 (Europe/Istanbul).

**Scope:** step 4 of the user-requested UI refresh. It brings the trace detail page closer to the approved mockup (Trace detail artboard). The data, the span-tree build (`lib/span-tree.ts`) and the WAI-ARIA tree behaviour are unchanged.

**Fixed for this step:**
- no new dependency;
- no backend, API, ADR or contract change;
- no provider request.

## 1. Changes (frontend only)

| File | Change |
|---|---|
| `components/traces/trace-detail-sections.tsx` | `TraceFacts`: the ten separate fact boxes become one strip with hairline dividers. The dividers are 1 px grid gaps over the line color. There are 1, 2 or 5 columns, and each divides ten, so no empty cell shows. Values are `font-medium`. Test ids and texts are unchanged. |
| `components/traces/waterfall.tsx` | **Timeline / Tree switch** (`role="group"`, `aria-pressed`): Tree replaces the bars with the signed start offset. **Per-row columns from `lg`:** Duration, Tokens in / out (`?` = not reported, with screen-reader text) and Cost. Cost reads "unknown" in the warning ink, with the reason in the `title`, and never $0; non-inference rows show "—". **Card header:** the switch and the legend. **Row style:** taller rows and 12 px bars; the selected row gets a series-1 tint and an inset accent; the kind badge is accent-colored on inference rows. **Axis labels:** the middle and quarter labels are hidden while the row columns narrow the axis; gridlines stay. The keyboard hint moved under the card. The detail panel has larger padding and radius. |
| `tests/unit/waterfall-ui4.test.tsx` (new) | 5 tests, listed in §2. |

**Kept unchanged:**
- `role="tree"` / `treeitem` with levels, sizes, positions and one tab stop;
- the arrow / Home / End / Enter keys;
- `?span=` selection;
- the 200-row batches;
- `span-bar` geometry;
- `span-details` and its test ids;
- the escaping of hostile labels;
- the rule that no telemetry string reaches a `style` attribute (the existing test still passes).

## 2. Tests and checks

All commands ran in `node:22.23.4-bookworm-slim`, with `frontend/` mounted and `node_modules` in the `llmi-fe-node-modules` volume.

| Command | Result |
|---|---|
| `npm test` | **384 pass / 0 fail / 0 skip** (UI-3: 379) |
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `node scripts/next.mjs build` | success |
| `npm run check:bundle` | 461 files, 0 findings |

**What the 5 new tests check:**
- Timeline is the default (bars, `aria-pressed`);
- Tree removes the bars and keeps every `treeitem`, with the offset shown exactly as built (no doubled sign);
- the new column headers;
- tokens and cost appear only on inference rows, and unknown cost reads "unknown";
- the facts strip keeps all ten test ids.

**Browser check:**
- **Setup:** the local postgres and backend containers were started, then stopped again. Headless Edge was used.
- **Pages:** trace `2a0463dd…` (dark and light) and the failed Gemini trace `cf71d3ef…` (dark, then switched to Tree; light facts strip). Both at 1440 px; the failed trace also at 390 px.

**Two defects found on the screenshots and fixed in this step:**
1. **Overlapping axis labels.** With the new columns, the five axis labels overlapped at 1440 px. The middle and quarter labels are now hidden from `lg` until `2xl`.
2. **Doubled sign.** The Tree offset showed "++0 ns", because `offsetLabel` is already signed. The extra "+" was removed. The new test now asserts there is no doubled sign; its first version had encoded the defect.

After the fixes:
- Tree mode shows 0 bars and 2 offset cells, with Tree pressed;
- there is no horizontal scroll at 390 px.

## 3. Risks and open items

- **E2E suite:** not run. The selectors it uses are unchanged (`treeitem`, `span-details*`, `fact-*`, "attempt N", `data-placement`), but a full run is owed before a commit.
- **Narrow axis at `xl`:** at 1280–1535 px the side panel and the row columns share the width, so the axis is narrow there. Only the start and end labels show; the bars keep exact geometry.
- **Mode is not shared:** the Timeline/Tree choice is component state, not in the URL, so it resets on reload.
- **Next step (UI-5, experiment diff view):** waits for the user's decision.
