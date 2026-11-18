# Phase UI-1 — Design tokens and light/dark theme

**Date:** 2026-11-15 (Europe/Istanbul).

**Scope:** the first step of the UI refresh the user requested, after the market research (Langfuse, Sentry, LangSmith, Arize Phoenix, Braintrust, Datadog, Helicone, Logfire, Linear). This step covers color tokens and the theme only. Layout, navigation icons, KPI sparklines, the waterfall and the experiment diff view are later steps and still need a decision.

**Fixed for this step:**
- no new dependency;
- no backend, API, database, ADR or contract change;
- no provider request.

## 1. Changes (frontend only)

| File | Change |
|---|---|
| `app/globals.css` | The `@theme` values stay the dark defaults. Two new tokens: `--color-warning-ink` and `--color-cursor`. A light theme overrides the same variables. It applies when the OS prefers light (unless the toggle forced dark), or when the toggle forces light. The two light blocks are identical. |
| `components/theme-toggle.tsx` (new) | Header control with three buttons: System / Light / Dark. It is a labeled `role="group"`; each button carries `aria-pressed` and a text name. It only sets or removes `data-theme` on `<html>`. |
| `components/app-shell.tsx` | The toggle sits next to the backend status. |
| `app/layout.tsx` | `colorScheme: "light dark"`; one `themeColor` per scheme. |
| `components/charts/*.tsx` | `CHART` references the CSS tokens (`var(--color-…)`) instead of hex copies, so charts follow the theme with no JavaScript. |
| `metric-card`, `model-comparison`, `model-groups`, `diagnostics-panel` | Status text moved from `text-warning` to `text-warning-ink`. The warning hue is 1.8:1 on the light surface, so it is unreadable as text. |
| `tests/unit/theme.test.tsx` (new) | 9 tests, listed in §3. |

**Not persisted.** The existing static security test forbids browser storage and `document.cookie` in the dashboard. The toggle choice therefore lasts until the next full page load; client-side navigation keeps it. Persisting it needs a server-set cookie (a `proxy.ts` change). That is left for a decision.

## 2. Palette validation (light surface `#fcfcfb`)

Run with the dataviz `validate_palette.js`:

| Set | Result |
|---|---|
| Provenance real/synthetic/mixed (`#2a78d6,#eb6834,#1baf7a`), all pairs | PASS. Worst CVD ΔE 9.2; worst normal-vision ΔE 24.0. Mixed is 2.74:1, below 3:1, so the relief rule applies: it is always next to its text label. |
| Succeeded/failed (`#2a78d6,#d03b3b`) | PASS. CVD ΔE 23.8; both colors ≥ 3:1. |
| Passed/failed/not evaluated (`+#8a8882`) | Chroma floor FAIL on the gray, by design: "not evaluated" is meant to read as an absent result. The dark theme has the same result (`#7d7b75`). CVD ΔE 9.3. |

**Text contrast (WCAG), lowest across canvas/surface/raised:**

| Token | Light | Dark (unchanged) |
|---|---|---|
| ink | 14.8 | 13.7 |
| ink-2 | 7.5 | 8.6 |
| ink-3 | 4.8 | 5.0 |
| critical-ink | 5.2 | 6.8 |
| warning-ink | 5.0 | 9.0 |

## 3. Tests and checks

The local host had no `node_modules`, and its Node (22.11) is not the pinned 22.23.4. Every command below therefore ran in `node:22.23.4-bookworm-slim`, with `frontend/` mounted and `node_modules` in a Docker volume (`llmi-fe-node-modules`). Before the first run: `npm ci --no-audit --no-fund` from the lockfile.

| Command | Before | After |
|---|---|---|
| `npm test` | 356 pass / 0 fail | **365 pass / 0 fail / 0 skip** |
| `npm run typecheck` | clean | clean |
| `npm run lint` | clean | clean |
| `node scripts/next.mjs build` | — | success, 14 routes |
| `npm run check:bundle` | — | 463 files, 0 findings |

**What the 9 new tests check:**
- the two light blocks are identical;
- every themed token has a light value;
- the fixed status hues have none;
- a forced dark theme opts out of the OS-light block;
- the chart sources contain no hex or `rgba()`;
- no `text-warning` without `-ink` remains;
- the toggle markup and `applyThemeMode`.

**Built CSS:** Tailwind keeps all token variables, including the new ones. `color-scheme: light` is present in both light blocks; Lightning CSS adds its own `--lightningcss-*` helpers.

**Browser check:** existing local containers `llminsight-postgres-1` and `llminsight-backend-1` were started, then stopped again afterwards. The new build ran beside them on 127.0.0.1:3001. Headless Edge was driven over the DevTools protocol with project 1 and a 31-day range. Pages: overview, models, traces and one trace detail, in each scheme.

| Check | Result |
|---|---|
| Body background, dark scheme | `rgb(12,13,16)` |
| Body background, light scheme | `rgb(244,244,241)` |
| Chart grid stroke | follows the theme: `rgb(36,39,47)` dark → `rgb(230,228,222)` light. Confirms that `var()` in SVG presentation attributes works in Chromium. |
| OS light + toggle Dark | dark |
| Then client navigation to /models | still dark |
| Then System | light |
| OS dark + toggle Light | light |

Only Chromium (Edge) was available. Firefox and WebKit were not tested.

## 4. Risks and open items

- **Toggle persistence:** decision needed (see §1).
- **Browser coverage:** `var()` in SVG presentation attributes was verified only in Chromium. The same pattern is widely used with Recharts (for example shadcn/ui charts), but it was not run here in Firefox or Safari.
- **Light tints:** the light theme reuses the existing tinted backgrounds (`bg-synthetic/10` and others). They were reviewed on the screenshots; no separate contrast numbers are recorded for them.
- **E2E suite:** Playwright e2e was not run. It needs the full local stack with its own fixture projects.
