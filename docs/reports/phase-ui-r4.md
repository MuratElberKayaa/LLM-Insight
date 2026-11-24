# Phase UI-R4 — Evaluation workspace and frontend finalization

**Date:** 2026-11-24 (UTC). **Branch:** `feature/phase-10-medium-term`, uncommitted, on top of `daa6430` with UI-R1…UI-R3 (kept, with their assets and the 12 UI-R3 Windows baselines).

**Scope:** step 4 of the frontend redesign ("FAZ UI-4"). Datasets and Experiments become one evaluation workspace, the whole frontend gets a final pass, and Linux visual regression is set up as far as this machine allows (§7).

**Fixed for this step:**
- no backend, API, database or URL contract change; the new URL parameters are additive and dashboard-only;
- no new dependency; no provider request;
- `.env` files and dev-database rows were not read or written;
- no commit, push, PR or merge.

## 1. Datasets: before and after

**Before:**
- the list was a plain three-column table and a create form;
- the dataset page had a heading, a count and full case cards.

**After, list page (`/datasets`):**
- breadcrumb (Evaluate / Datasets), eyebrow, and a context bar (project);
- the **evaluation workflow strip** (§3);
- **facts:** datasets, approved cases (sum over the listed datasets, labelled so), datasets without a case, newest dataset;
- the table (same `dataset-table` / `dataset-row` ids) with a **Next step** column: "Set up a mock experiment →" when the dataset has cases (the existing setup route; nothing runs), else "Add a case from a reviewed trace";
- cards below `md`; the create form in a card that says cases come later, one approval at a time.

**After, dataset page (`/datasets/{id}`):**
- breadcrumb (Evaluate / Datasets / *name*); the context bar shows project, dataset and case count;
- the workflow strip with Dataset current;
- **facts for the loaded page:** cases in the dataset, with an expected output (cases without one are "never evaluated"), from a reviewed trace vs standalone, and rules by type. Each card says whether it counts the whole dataset or this page;
- **filters** by rule, source and expected output (`?rule=` / `?origin=` / `?expected=`; links with `aria-current`, "Clear filters"). The cases API has no filter and pages 50 at a time (`after`), so the filters apply to the cases loaded, and the panel says so;
- a **case index** table (`case-index`): one row per case with rule, source (trace … · review #n), expected output and approval. It jumps to the card; it repeats no content and no `test-case` id;
- **case cards** (same ids), each with:
  - an anchor (`#case-{id}`) and previous / next links;
  - a lineage line, trace → review #n → "approved as this test case", linking to the trace and to its review section (`#reviews-heading`). A removed review and a standalone case are stated;
  - the input labelled "human-entered, as stored after redaction; not a captured user conversation and not rebuilt from telemetry".
- **Not added:** delete, rename, duplicate or import of datasets or cases. The API has none, so no control suggests one (a unit test checks the page sources). No case is created from a review without its explicit approval: that flow stays on the trace page, unchanged.

## 2. Experiments: before and after

**Before:**
- "Completed live comparisons" came first, then a plain mock table;
- the mode column said "Mock · synthetic".

**After, list page (`/experiments`):**
- breadcrumb, context bar, eyebrow and the workflow strip (Mock experiment current);
- **facts:** mock experiments, finished of listed, datasets used, imported live reports (read-only);
- **Mock experiments** card, first. It carries the **Synthetic / Offline Mock** badge and says a scripted mock produces the outputs, no provider is called, nothing is spent and no winner is named. Each row has:
  - the run and its dataset (linked);
  - versions A and B stacked;
  - the model (its title says it sets identity and price only);
  - a **Synthetic / Offline Mock** badge;
  - the state as glyph and word ("Finished" / "Not finished (running or interrupted)");
  - results and start time.
- **Completed live comparisons** card, second, with an **Imported report · read-only** badge. It says these are real provider calls on synthetic inputs, run outside this dashboard and imported with verified trace links. They are not part of the mock runner, cannot be started or re-run here, and name no general winner. Its data comes from `GET /api/v1/imported-experiments`, a separate read-only source; nothing links it to the mock Experiments API.

**After, experiment page (`/experiments/{id}`):**
- breadcrumb (… / #id); the context bar shows project, dataset, A vs B and mode;
- mode and state badges in the header meta;
- the workflow strip, where Dataset links to **this run's dataset**;
- the existing report is kept: the SYNTHETIC banner, summary, warnings, version cards with the B-vs-A case changes, the backend verdicts, the comparison table, outcomes, results per case and limitations. Its Wilson intervals and exact McNemar test are the backend's (Phase 9D) and stay worded as "not a significance claim".

**Also:**
- **Setup page and case-result page:** breadcrumbs and eyebrows; the setup page is labelled "Synthetic / Offline Mock".
- **Imported report page:** breadcrumb, an "Imported live comparison" eyebrow, the read-only badge and a description that it is not a mock experiment and cannot be re-run.

**Not added:** no new runner, no real-model execution path, no quality score or winner, and no success rate where the backend reports none.

## 3. The evaluation workspace

- **Workflow strip** (`evaluation-flow`), five steps:
  1. Trace (observed metadata, no content);
  2. Human review (recorded on the trace page);
  3. Test case (converted after an explicit approval);
  4. Dataset;
  5. Mock experiment (started explicitly, synthetic).

  The current step is marked (`aria-current="step"`). Only steps with a page are links, and the conversions happen on those pages after a person approves them.
- **Breadcrumbs** (`nav` "Breadcrumb", current page marked) on every evaluation page.
- **Shared status vocabulary** (`components/evaluation/badges.tsx`): mode, state and imported. Each is a glyph plus words; color never carries meaning alone.
- **Same building blocks as UI-R1…UI-R3:** context bar, eyebrow, metric cards with the violet stripe of the "tokens & cost / evaluation" family, card sections and the shared table.

## 4. Whole-frontend pass (UI-R1…UI-R4)

`ui-r4.spec.ts` ("whole-frontend pass") opens every page at 1280 px dark, 1280 px light and 390 px dark. The pages are Overview, Traces, a trace, Sessions, Models, Datasets, a dataset, Experiments and an experiment. On each it asserts:
- one `h1`;
- no horizontal page scroll;
- the skip link as the first Tab stop;
- no secret in the HTML.

With the **sidebar collapsed** at 1280 it walks every primary link and asserts the collapsed state survives, the current page is marked and there is no horizontal scroll.

**Fixed during the pass (small, as asked):**
- At 1280 px the experiment table pushed its Mode, State, Results and Started columns out of view. Versions now stack, the mode badge wraps, and the model header is short with a title.
- The dataset table got right-aligned case counts and a row header.

**Seen and kept:**
- The workflow strip wraps its fifth step to a second line at 1280 px with the sidebar open. It stays readable, so it was kept rather than dropping the step descriptions.
- The trace and model pages keep their UI-R2/UI-R3 layouts unchanged.

## 5. Changed files

| File | Change |
|---|---|
| `frontend/components/breadcrumbs.tsx` (new) | Breadcrumb trail |
| `frontend/components/evaluation/flow-strip.tsx` (new) | Workflow strip |
| `frontend/components/evaluation/badges.tsx` (new) | Mode, state and imported badges |
| `frontend/lib/case-filters.ts` (new) | Case filters, facts, dataset links |
| `frontend/components/datasets/case-table.tsx` (new) | Case index |
| `frontend/components/datasets/case-list.tsx` | Anchors, previous/next, lineage, input label |
| `frontend/app/datasets/page.tsx`, `frontend/app/datasets/[datasetId]/page.tsx` | Rebuilt as above |
| `frontend/app/experiments/page.tsx`, `frontend/app/experiments/[experimentId]/page.tsx` | Rebuilt as above |
| `frontend/app/experiments/new/page.tsx`, `frontend/app/experiments/[experimentId]/cases/[caseId]/page.tsx`, `frontend/app/experiments/live/[id]/page.tsx` | Breadcrumbs, eyebrows, labels |
| `frontend/playwright.config.ts` | Per-spec Linux visual selection with an explicit NOT RUN log line; `updateSnapshots: "none"` in CI (§7) |
| `frontend/tests/unit/ui-r4.test.tsx` (new) | 13 tests |
| `frontend/tests/e2e/ui-r4.spec.ts` (new) | 8 tests (datasets, experiments, light, phone, whole-frontend ×3, sidebar) |
| `frontend/tests/e2e/ui-r4-visual.spec.ts` (new) + `ui-r4-visual.spec.ts-snapshots/` (9 `-win32` baselines) | 5 tests: dataset facts, filters and first case; experiment version cards (dark and light); facts at 390 px |

## 6. Tests and checks

Commands ran from `frontend/` with Node 22.23.4 unless noted. Final code state:

| Command | Result |
|---|---|
| `npm run typecheck` · `npm run lint` | pass · pass (0 warnings) |
| `npm test` | **565 passed**, 0 failed (552 before + 13 in `ui-r4.test.tsx`) |
| `npm run build` · `npm run check:bundle` | pass · 511 files, 24 in the browser bundle, **0 findings** |
| `npm run test:audit-policy` | 116 passed |
| Mutants (scratchpad, each file restored) | **10/10 killed** (list below) |
| `python -I -m unittest discover -s scripts/qa/tests` (root) | OK (15 skipped, as before) |
| `python -I scripts/verify_phase0.py --quiet` (root) | 865 PASS, 0 FAIL |
| Changed text files scanned for control or bidi characters (scratchpad script) | 83 files, 0 found (after the fix below) |
| Playwright E2E, full suite, disposable `_test` database, **after the last cosmetic fixes** (run C) | **104 passed**, 0 failed |
| Backend | No backend file changed in UI-R4; the backend suite was not re-run (UI-R3: 1394 unit passed) |

Run C's 104 tests are the 91 of the UI-R3 baseline plus 8 in `ui-r4.spec.ts` and 5 in `ui-r4-visual.spec.ts`. Every visual spec ran against its Windows baselines, the 12 of UI-R3 included.

The 10 mutants:
1. the source filter ignoring "standalone";
2. an unchecked rule value accepted;
3. paging lost in filter links;
4. every case counted as having an expected output;
5. the review link without its section;
6. the human-entered label weakened;
7. no "previous case" link;
8. the mock badge reduced to "Mock";
9. every workflow step made a link;
10. "never evaluated" dropped from the index.

**E2E runs.** All ran on a disposable database created, migrated and dropped by the scratchpad runner ("remaining: 0" every time); dev data and `.env` were not touched.

| Run | Scope | Result | Notes |
|---|---|---|---|
| A (×2) | `phase4d` + `ui-r4-visual`, `--update-snapshots` | 10 passed (second try) | The first try failed in my test helper: `getByRole` cannot see the dataset table hidden at 390 px. It now reads the link directly |
| B | full suite | **104 passed** | New baselines matched on a new database |
| C | full suite after the experiment-table fix | **104 passed** | — |

**Found by the checks:**
- An edit of mine had turned two `\b` regex escapes in `ui-r4.test.tsx` into real backspace bytes. The project's Trojan Source test (`security-static.test.ts`) caught it. The bytes were replaced, and all 83 changed text files were scanned again: clean.
- The experiment table was too wide at 1280 px (§4).

## 7. Linux visual regression: status

**What exists now:**
- **Two baseline sets, kept apart.** Playwright names baselines per platform, so the 12 UI-R3 and 9 UI-R4 Windows baselines are `*-win32.png`, and Linux baselines would be `*-linux.png` beside them. Neither replaces the other. No Windows baseline was changed in UI-R4.
- **CI runs a visual spec only when its Linux baselines exist** (`playwright.config.ts`). Otherwise that spec is not discovered, because the CI gate (`scripts/qa/e2e_check.py`) counts a skipped test as a failure. The run prints, for each such spec:
  `[visual-regression] NOT RUN on this platform: <spec> has no Linux baselines (*-linux.png). See docs/reports/phase-ui-r4.md (Linux visual regression).`
  Checked with `LLMI_E2E_CI=1 … playwright test --list`: 85 tests listed, both visual specs reported NOT RUN. Locally all visual specs are listed.
- **CI never writes a missing baseline** (`updateSnapshots: "none"` under `LLMI_E2E_CI=1`), so a missing image can never turn into a pass.
- **Deterministic inputs:** fixed viewports and color schemes, SYNTHETIC fixtures, a fixed browser clock (`page.clock.setFixedTime`, UI-R4 spec), animations off, caret hidden, masks for run-specific values, and tolerances of 1.5 % pixels and 0.2 color.

**What was not possible here, stated plainly:**
- **No Linux baselines were produced, and the visual tests have not run on Linux.** The local WSL Ubuntu did not answer: `wsl.exe` failed twice with `Wsl/Service/WSAETIMEDOUT`.
- The available Docker images are Debian-based, with other fonts than the CI runner (`ubuntu-24.04`, Playwright's bundled Chromium). Baselines made there would not match CI, so making them would only move the problem.
- The E2E stack is also loopback-bound (request guard), which a container would have to work around.

**How to add them (owner step):**
1. On an `ubuntu-24.04` runner with the same Playwright 1.64.0 Chromium as CI, run the suite with `LLMI_E2E_CI=1 LLMI_VISUAL_LINUX=1 npx playwright test tests/e2e/ui-r3-visual.spec.ts tests/e2e/ui-r4-visual.spec.ts --update-snapshots`.
2. Review the `*-linux.png` files.
3. Commit them.

From then on the normal CI gate runs both visual specs on Linux.

## 8. Screenshots

**Where they are and what they contain.** Stored in `docs/reports/assets/ui-r4/` (9 files; the UI-R2 and UI-R3 sets are untouched). They are taken by `ui-r4.spec.ts` in run C on the disposable database, from SYNTHETIC fixtures only: run-owned `e2e-<run>-*` projects, the Phase 4D golden fixture and its mock experiments. Nothing comes from Project 1 or real conversations.

**Secret check.** Before every capture the test asserts that the page HTML holds no server-side token, no `Bearer …` value and no database URL with a password. The PNG files and the 9 new baselines carry no text chunks (checked).

| File | Shows |
|---|---|
| `ui-r4-datasets-dark-1280.png` | Datasets: trail, workflow strip, facts, table with next step, create form |
| `ui-r4-dataset-detail-dark-1280.png` / `ui-r4-dataset-detail-light-1280.png` | Golden fixture dataset: facts, filters, case index, cards with lineage and previous/next |
| `ui-r4-experiments-dark-1280.png` | Mock experiments with Synthetic / Offline Mock badges, the imported section labelled read-only |
| `ui-r4-experiment-detail-dark-1280.png` / `ui-r4-experiment-detail-light-1280.png` | One mock run: trail, mode and state, workflow with its dataset, the existing report |
| `ui-r4-datasets-mobile-390.png` / `ui-r4-experiment-mobile-390.png` | Phone width: dataset cards; the experiment report stacked |
| `ui-r4-datasets-sidebar-collapsed-1280.png` | The icon sidebar on an evaluation page (viewport) |

**Bundle** (sum of `.next/static`):

| | UI-R3 | UI-R4 | Δ |
|---|---|---|---|
| JS chunks | 1 160 605 B (19 files) | 1 162 051 B (19 files) | +1 446 B (+0.1 %): no new client module; the new parts are server components |
| CSS | 49 091 B | 50 537 B | +1 446 B (new utility classes) |

## 9. Open technical and UX gaps

1. **Linux visual baselines** (§7): owner step on a CI-equivalent runner.
2. **Case filters** cover one page of 50; a server-side case filter would need an API change (not done).
3. **No dataset or case editing.** Delete, rename, move and edit-after-approval do not exist in the API; adding them needs a backend design and review.
4. **Mock experiments compare prompt versions with a scripted provider only.** Real-model runs remain disabled by design. The imported live reports are fixed-format (six cases, two prompts) and cannot be filtered or compared with the mock runs.
5. **Experiment list:** no filters (dataset, state); the API returns the newest 200.
6. **Flow strip:** it could collapse to a compact form on narrow laptop widths instead of wrapping.
7. **Experiment table at 1280 px with the sidebar open:** the last column (Started) is partly cut inside the table's own scroll region (the page itself does not scroll sideways). Collapsing the sidebar shows it.
8. **Focus management:** after in-page anchor jumps (case index → card) focus stays on the link; moving focus to the card would help keyboard users.
