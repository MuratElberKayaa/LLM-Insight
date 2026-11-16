# Phase UI-5 — Experiment diff view

**Date:** 2026-11-16 (Europe/Istanbul).

**Scope:** step 5, the last step of the user-requested UI refresh. It adds a Braintrust-style comparison to the experiment report page: version A is the baseline and B is compared. Naming a better version stays the backend's decision (`ComparisonVerdict`, unchanged).

**Fixed for this step:**
- no new dependency;
- no backend, API, ADR or contract change;
- no provider request.

**Files left untouched:** the user's uncommitted experiment work (`app/experiments/page.tsx`, `app/experiments/live/`, `components/experiments/imported-report.tsx`).

## 1. Changes (frontend only)

| File | Change |
|---|---|
| `components/experiments/experiment-diff.tsx` (new) | **`caseChange`:** a plain reading of the two stored outcomes of one case. The result is Pass → Fail, Fail → Pass, Same outcome, or Not comparable; a case is Not comparable when either result is missing or not evaluated, and never counts as a pass or a fail. **`changeCounts`:** counts the rows per change. **`ChangeBadge`:** glyph plus text, never color alone. **`VersionCards`:** A · baseline and B · compared, each with the stored pass rate (passed / evaluated; `n/a` when nothing was evaluated), an outcome bar (passed / failed / not evaluated), duration p50, tokens, and cost through the existing `VariantCost`, so an incomplete total reads "Total unknown" and never shows $0. |
| `components/experiments/case-diff-table.tsx` (new, client) | The per-case table gains a "Change (B vs A)" column. Filter chips (`aria-pressed`, with counts) cover All / Pass → Fail / Fail → Pass / Same / Not comparable; a chip with no cases is disabled. The caption says the change is "not a significance test" and, when filtered, "Showing N of M". Pass → Fail rows get a faint critical tint. Only `outcome` and `detail_code` per result cross to the client. |
| `components/experiments/experiment-report.tsx` | `CaseResultsTable` keeps its name and signature and renders `CaseDiffTable`. The local `ResultCell` and the now-unused imports moved out. |
| `app/experiments/[experimentId]/page.tsx` | `VersionCards` sits after the warnings, before "What can be compared". The case-section heading gets a one-line description. |
| `tests/unit/experiment-diff.test.tsx` (new) | 9 tests, listed in §2. |

**Kept unchanged:**
- `case-results-table`, `case-result-row[data-case-id]`, `case-result-link`, `td[data-slot]` with `[data-outcome]`;
- `comparison-table` rows, `quality-verdict` / `cost-verdict` and the outcome chart.

The "All" filter is the default, so the e2e row counts hold.

## 2. Tests and checks

All commands ran in `node:22.23.4-bookworm-slim`, with `frontend/` mounted and `node_modules` in the `llmi-fe-node-modules` volume.

| Command | Result |
|---|---|
| `npm test` | **393 pass / 0 fail / 0 skip** (UI-4: 384) |
| `npm run typecheck` | clean |
| `npm run lint` | clean, after removing one unused import that lint flagged |
| `node scripts/next.mjs build` | success |
| `npm run check:bundle` | 467 files, 0 findings |

**What the 9 new tests check:**
- the four change readings;
- missing or not-evaluated results are never treated as pass or fail;
- each row is counted once;
- the e2e selectors and the default "All" filter;
- the labels and the filter counts;
- a filter shows "Showing 1 of 4";
- an empty filter is disabled;
- the version cards (baseline/compared, rates, counts, "Total unknown", no $0);
- an undefined pass rate reads "n/a".

**Browser check:**
- **Setup:** the local postgres and backend containers were started, then stopped again. Headless Edge was used on experiments #58 (dark) and #57 (light) at 1440 px, and #58 at 390 px.
- **Experiment #58:** 12 cases: Pass → Fail 2, Fail → Pass 5, Same 3, Not comparable 2. Clicking Pass → Fail showed 2 rows with "Showing 2 of 12". There was no horizontal scroll at 390 px.

**Cross-check against the backend verdict on the same page:**

| Backend says | Diff view gives |
|---|---|
| 10 paired | Same 3 + Pass → Fail 2 + Fail → Pass 5 = 10 |
| 1 excluded by design + 1 evaluated under one version only | Not comparable 2 (#196 and #197) |
| A passed 4 on paired cases | 2 (Same, passed) + 2 (Pass → Fail) = 4 |
| B passed 7 on paired cases | 2 + 5 = 7 |

## 3. Risks and open items

- **Reading the counts:** on #58, Fail → Pass (5) outnumbers Pass → Fail (2), while the backend names no better version (quality "partial"). The view states "not a significance test", and the verdict card stays directly above it.
- **E2E suite:** not run. The selectors are kept (§1), but a full run is owed before a commit.
- **Imported live experiments:** `/experiments/live/[id]` is the user's uncommitted work and was intentionally not changed.
- **Filter is not shared:** the filter is component state, not in the URL, so it resets on reload.
