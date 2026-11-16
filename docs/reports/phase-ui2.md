# Phase UI-2 — Application shell and navigation icons

**Date:** 2026-11-16 (Europe/Istanbul).

**Scope:** step 2 of the user-requested UI refresh. It follows the approved design mockup (a private claude.ai design canvas: Overview and Trace detail). This step changes the application shell only. Page content is unchanged: KPI cards, the waterfall and the experiment views are later steps.

**Fixed for this step:**
- no new dependency;
- no backend, API, ADR or contract change;
- no provider request.

## 1. Changes (frontend only)

| File | Change |
|---|---|
| `components/icons.tsx` (new) | Inline stroke SVG icons (16 px, `currentColor`) for the five pages, a lock and the brand mark. All are `aria-hidden` and `focusable="false"`. There is no icon font or library, so the strict CSP is unaffected. |
| `components/side-nav.tsx` | The navigation is grouped by the product loop: **Observe** (Overview, Traces, Models) and **Evaluate** (Datasets, Experiments). Traces now comes before Models. Each link has an icon; on the active link the icon takes the series-1 accent. Link text is unchanged, so accessible names are unchanged. The `?project=` carry-over is unchanged. Group labels show only at `lg`; below `lg` the links stay a horizontal strip. |
| `components/app-shell.tsx` | The full-width top bar is removed; each page already renders its own header. The sidebar gets a brand mark with a subtitle, and a footer with the backend status, the theme toggle and the local-only notice: "Local only · loopback, no login" with a lock icon. The full sentence ("… not for public deployment") stays in the `title` and in screen-reader text. Below `sm`, the "loopback, no login" suffix is hidden so the footer fits one row. |
| `components/theme-toggle.tsx` | Styling only (`w-fit`, canvas background) for its place in the sidebar. |
| `tests/unit/shell.test.tsx` (new) | 6 tests, listed in §2. |

**Kept unchanged:**
- the "Skip to content" link;
- the `main#main` landmark;
- `nav[aria-label=Primary]` and the link names;
- `data-testid="backend-status"`.

These are what the Playwright suites assert.

## 2. Tests and checks

All commands ran in `node:22.23.4-bookworm-slim`, with `frontend/` mounted and `node_modules` in the `llmi-fe-node-modules` volume.

| Command | Result |
|---|---|
| `npm test` | **371 pass / 0 fail / 0 skip** (UI-1: 365) |
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `node scripts/next.mjs build` | success |
| `npm run check:bundle` | 461 files, 0 findings |

**What the 6 new tests check:**
- the exact navigation groups, order and hrefs;
- every nav icon is decorative and adds no text to the accessible name;
- every icon is `aria-hidden`;
- the shell keeps the skip link, `main#main` and the primary navigation;
- the shell keeps the local-only / not-for-public-deployment statement;
- the status slot and the theme toggle sit in the sidebar.

**Browser check:**
- **Setup:** the local `llminsight-postgres-1` and `llminsight-backend-1` containers were started, then stopped again. The new build ran beside them. Headless Edge was driven over the DevTools protocol.
- **Pages:** Overview (dark and light), Traces (light) and Experiments (dark) at 1440 px; Overview and Traces at 390 px.
- **Result:** in each, the primary links read "Overview, Traces, Models, Datasets, Experiments", with `aria-current` on the right one. Backend health showed `ready`. There was no horizontal page scroll at 390 px (`scrollWidth` 390).

## 3. Risks and open items

- **E2E suite:** not run here; it needs the full stack and its fixture projects. The selectors it uses were kept (§1), but a full run is still owed before a commit.
- **Nav order:** Traces now comes before Models. No test depends on the order.
- **Browser coverage:** as in UI-1, only Chromium (Edge) was available.
- **Next step (UI-3, KPI cards):** waits for the user's decision.
