# Phase UI-R2 — Trace and agent explorer

**Date:** 2026-11-23 (UTC). **Branch:** `feature/phase-10-medium-term`, uncommitted, on top of `daa6430` and the uncommitted UI-R1 work (kept).

**Scope:** step 2 of the frontend redesign ("FAZ UI-2"). The Traces list and the trace page become a trace and agent explorer, and the E2E run-id flake found in UI-R1 is fixed. Models, Sessions, Datasets and Experiments are not redesigned.

**Fixed for this step:**
- no backend code, API, database or URL contract change;
- no new dependency; no provider request;
- `.env` files and dev-database rows were not read or written;
- no commit, push, PR or merge.

## 1. E2E run-id fix (done first)

**Problem.** `scripts/dev/e2e_fixtures.py` drew the run id with `secrets.token_hex(6)`. About 2 % of ids hold 10+ digits in a row (e.g. `27148722459d`). The backend's label check then refuses labels built from the id, such as dataset names and the reviewer handle, as contact data, and five E2E tests fail.

**Fix.** The id is now built as three 4-character groups, each starting with a letter `a`–`f`.
- At most 3 digits appear in a row, and there is no separator inside the id. So no digit run (10+) and no digit group (≥ 9–10 digits across separators) can reach the backend's limits.
- The id is still 12 lowercase hex characters, so `RUN_RE` and the teardown ownership check are unchanged.
- The backend check is **not** relaxed.

**Test.** `backend/tests/unit/test_e2e_run_id.py` (8 tests) uses the backend's own `redaction.label_is_sensitive` as the oracle. It covers:
- the old failing id is refused;
- the shape of generated ids;
- worst-case ids;
- 2,000 random ids × every label suffix the E2E specs build;
- randomness;
- that `setup()` uses the new generator.

## 2. Explorer layout

**Desktop** (≥ 1360 px viewport), three panels:

| Panel | What it shows |
|---|---|
| **Left: trace rail** (`trace-rail`, new) | The page of the listing the trace was opened from (`list` parameter: window, status, provenance, provider, model, session, cursor). Each row shows status, service, duration, a provenance swatch with its text label and the first provider · model. The open trace has `aria-current="page"` and an accent frame. "Next page →" / "← First page" page the rail without leaving the trace, and "Open list" goes to the full list. If the trace is not on the page, the rail says so. If the read fails, only the rail degrades. It is one extra `GET /api/v1/traces` per trace page |
| **Middle: waterfall** | The existing span tree and timeline (WAI-ARIA tree, keyboard, Timeline/Tree), plus the additions below |
| **Right: span inspector** | The existing span details (sticky), plus the additions below |

**Waterfall additions:**
- a family badge per row (**A**gent, **L**LM, **T**ool, **R**etrieval, **M**CP, `·` application);
- bars colored by family: LLM blue (categorical slot 1), tool/retrieval/MCP aqua (slot 3), agent and application neutral, ERROR red;
- an error row tint;
- "◷ longest #1–3" on the three longest non-root spans (a ranking, not a judgement);
- Expand all / Collapse all;
- columns that follow the panel's width (container queries) instead of the viewport.

**Inspector additions:**
- the step family and step label;
- **time to first chunk** of the attempt, read from the span's own `gen_ai.response.time_to_first_chunk` and `gen_ai.request.stream`. It shows "not recorded" or "not a streaming request" when that is the case, and "unknown" when the stream flag is missing. It is never estimated;
- **Diagnostics on this span:** findings that name the span as evidence, each labelled "Unverified hypothesis" and linking to its finding;
- **Human review:** reviews anchored on the span, the trace total, and a link to the review section.

**1280–1359 px.** The rail becomes a disclosure above the content; the waterfall and the inspector stay side by side.

**Below 1280 px.** The rail is a disclosure. A "Spans / Selected span" switch (`aria-pressed`) shows one panel at a time, and tapping a row opens its details.

**Color validation.** Dataviz validator, all pairs, both themes:
- dark (`#3987e5`, `#199e70` on `#10151f`): all checks pass;
- light (`#2a78d6`, `#1baf7a` on `#ffffff`): aqua is 2.82:1, so the relief rule applies. Every row carries a visible text label, and the span table is the table view.

The new token is `--color-series-3`.

## 3. Agent analysis

The "Agent steps" section is now a card above the waterfall, shown only when agent, tool, retrieval or MCP steps exist. It adds:
- **Spans by step type:** Agent · LLM · Tool · Retrieval · MCP with counts (zeros stay visible);
- **Repeats** (`×N`) and **Signal** columns in the tool table;
- **Possible loop (hypothesis, not verified):** shown when a *named* tool ran 5 or more times. The threshold is the backend diagnostics rule `repeated_tool_calls` (`REPEATED_TOOL_CALLS = 5`), and unnamed tools are not grouped, as in the backend. The text says the repetitions may be intended and that tool arguments and results are not stored. It links the diagnostics finding when the rule fired, and the diagnostics section otherwise.

No tool argument, result or prompt is shown, and none exists to show. No example agent flow is generated: everything comes from the stored spans of the trace.

## 4. Navigation and context

- **Sticky context bar** (`context-bar`) on Overview, Traces and the trace page. It shows project (`#id · name`), window (backend-echoed UTC range and preset), provenance and active filters, plus "Local only · metadata only · not live". It is text only; every control stays on the page.
- **Overview → Traces** (`lib/context-links.ts`) carries project, window, provenance, provider and model. They mean the same on both pages (ARCHITECTURE.md §5.11: provenance is the trace's own value; provider/model mean "a usage row of the trace with that provider and model").
  - New metric links: "Open these traces →" (Traces card) and "Open failed traces →" (trace error rate card, adds `status=error`, which is what that rate counts).
  - Recent traces and "All traces →" now open with the overview's listing as `list`.
  - Error patterns keep their own links, because they ignore every filter but the window.
- **Overview → Models** already carried project, window, provenance, provider and model (`to-models`, unchanged).
- **Traces list:** eyebrow "Observe · Trace explorer" and one "Scope and filters" panel, as on the overview.

## 5. Changed files

| File | Change |
|---|---|
| `scripts/dev/e2e_fixtures.py` | `new_run_id()` (§1); `add-trace --set` limited to `phase6c-new` (default) and `ui-r2-agent` |
| `scripts/dev/seed_trace_fixtures.py` | `--set ui-r2-agent`: one SYNTHETIC agent trace (agent, streaming LLM with TTFT 0.25 s, retrieval, MCP `tools/list`, `lookup_order` ×5 with the last failing, `search_kb` ×2); metadata only |
| `backend/tests/unit/test_e2e_run_id.py` (new) | §1 |
| `backend/tests/unit/test_uir2_agent_fixture.py` (new) | 4 tests: the set ingests without violations; the Phase 10A step classification and tool summary; `lookup_order` exactly at `REPEATED_TOOL_CALLS`; the TTFT attribute; no content keys |
| `frontend/lib/span-tree.ts` | `SpanView.streaming` and `slowRank` |
| `frontend/lib/agent-analysis.ts` (new) | Step families, tool repeats, possible-loop flag, finding anchors, findings per span |
| `frontend/lib/context-links.ts` (new) | Overview → trace-list state; context-bar entries |
| `frontend/lib/server/traces.ts` | `loadTraceRail` (one read) |
| `frontend/components/traces/waterfall.tsx` | §2 |
| `frontend/components/traces/trace-detail-sections.tsx` | `AgentSteps` with the analysis (§3) |
| `frontend/components/traces/trace-rail.tsx`, `rail-toggle.tsx` (client), `step-badge.tsx` (new) | Rail, its disclosure, the family badge |
| `frontend/components/context-bar.tsx` (new) | Sticky context bar |
| `frontend/components/reviews/diagnostics-panel.tsx` | An `id` anchor per finding |
| `frontend/components/metric-card.tsx`, `overview/kpi-grid.tsx`, `overview/latest-trace.tsx` | `action` link slot; trace KPI links; `listState` |
| `frontend/app/traces/[traceId]/page.tsx`, `app/traces/page.tsx`, `app/page.tsx` | Explorer layout, context bar, scope panel, context links |
| `frontend/app/globals.css` | `--color-series-3` (dark `#199e70`, light `#1baf7a`, both light blocks) |
| `frontend/tests/unit/ui-r2.test.tsx` (new) | 17 tests, §6 |
| `frontend/tests/e2e/ui-r2.spec.ts` (new) | 6 browser tests, §6 |

**Kept:**
- every existing `data-testid`, the tree's ARIA attributes and their order;
- `?span=` deep links and the `list` back link;
- the "Selected span" complementary landmark;
- one `h1` per page;
- table captions;
- the review, decision and diagnostics flows.

## 6. Tests and checks

Commands ran from `frontend/` with Node 22.23.4, unless noted.

| Command | Result |
|---|---|
| `npm run typecheck` · `npm run lint` | pass · pass (0 warnings) |
| `npm test` | **530 passed**, 0 failed (513 before + 17 in `ui-r2.test.tsx`) |
| `npm run build` · `npm run check:bundle` | pass · 495 files, 24 in the browser bundle, **0 findings** |
| backend `pytest tests/unit/test_e2e_run_id.py tests/unit/test_uir2_agent_fixture.py tests/unit/test_e2e_comparison_fixture.py` | 20 passed (8 + 4 new, 8 existing) |
| backend `ruff check` / `ruff format --check` (new tests); scripts with the repository's 88-column format | pass |
| Mutants (scratchpad, each file restored) | **10/10 killed** (list below) |
| Playwright E2E (`npx playwright test`, msedge) on a disposable `_test` database | **78 passed**, 0 failed (72 existing + 6 in `ui-r2.spec.ts`); the run below |
| backend `pytest tests/unit` · `ruff check .` · `ruff format --check .` | 1391 passed · pass · pass |
| `python -I -m unittest discover -s scripts/qa/tests` | 256 tests OK (15 skipped) |

The 10 mutants:
1. loop at `>` instead of `>=`;
2. loop for unnamed tools;
3. usage row not making an LLM step;
4. TTFT seconds not converted;
5. root ranked as longest;
6. every rail row marked current;
7. rail paging that leaves the trace;
8. provenance dropped from the overview link;
9. TTFT always shown;
10. tool loop flag for unnamed tools.

The first run left mutant 3 alive, so a test for usage rows without `gen_ai.operation.name` was added.

**How the E2E suite ran.** The same scratchpad runner as in UI-R1 was used:
1. created `llminsight_uir1_e2e_test`;
2. migrated it and seeded prices;
3. ran a backend on 127.0.0.1:8000 with `LLMI_DATABASE_URL` in the process environment only;
4. ran Playwright;
5. dropped the database ("remaining: 0" after every run).

The dev database and `.env` files were not touched. The runs:

| Run | Result | Cause and fix |
|---|---|---|
| 1 | 74 passed, 1 failed, 3 not run | My test: `[data-family="agent"]` matched the count chip and the badge inside it (strict mode). Now scoped to `li[...]` |
| 2 | 74 passed, 1 failed, 3 not run | My test: a page-wide "root cause" check also found the existing review-form disclaimers ("…not that the root cause is proven"). Now scoped to the agent section |
| 3 | **78 passed** | — |
| 4 | **78 passed** | After the waterfall row fix (§7) |
| 5 | `ui-r2.spec.ts` only: 6 passed | After adding the secret check to the screenshot step |

No product behaviour had to change for a test.

**Unit tests (`ui-r2.test.tsx`):**
- streaming attributes as stored, including a string value that is not a number;
- longest-span ranks, including ties, the root and zero length;
- families, including usage-only LLM spans;
- loop exactly at 5, unnamed tools, the finding id, and finding anchors with hostile ids;
- the agent section's hypothesis wording and links;
- waterfall badges, bars and ranks;
- inspector TTFT (recorded, not recorded, unknown), findings and reviews;
- the rail: current trace, `list` kept, next page staying on the trace, open list, not listed, unavailable, empty;
- overview → list state;
- KPI links;
- recent-trace links;
- context bar: escaped project name and the local-only text.

**E2E (`ui-r2.spec.ts`):**
- rail selection and `list` from a filtered list, a span deep link across a reload, and rail navigation;
- overview → traces keeps range and provenance;
- the agent trace: families, loop notice, the diagnostics finding, family rows, longest marks, LLM TTFT 250.0 ms, the evidence link on a tool span, no content and no "root cause";
- the light theme;
- phone width: rail disclosure, Spans/Selected span switch, no horizontal scroll.

**Bundle** (sum of `.next/static`):

| | UI-R1 | UI-R2 | Δ |
|---|---|---|---|
| JS chunks | 1 140 563 B (18 files) | 1 153 160 B (19 files) | +12 597 B (+1.1 %); `rail-toggle` is the only new client module, and the waterfall grew |
| CSS | 39 694 B | 45 005 B | +5 311 B (container-query and new utility classes) |

## 7. Screenshots

**What they show and how they were taken.** Stored in `docs/reports/assets/ui-r2/`. They come from the E2E run on the disposable database, with SYNTHETIC fixtures only (run-owned `e2e-<run>-*` projects and the `uir2.agent` trace), so no real data is shown.

**Secret check.** Before each capture, the test asserts that the page HTML holds neither the server-side token nor a `Bearer …` value nor a database URL with a password (`ui-r2.spec.ts`, `shot()`). The PNG files carry no text chunks (`tEXt`, `iTXt`, `zTXt`, `eXIf`: none, checked).

| File | Shows |
|---|---|
| `ui-r2-explorer-dark-1440.png` | Three panels, dark: rail (open trace marked), waterfall with family badges and longest marks, inspector |
| `ui-r2-agent-dark-1440.png` | Agent trace, dark: step-family counts, possible-loop notice (hypothesis), tool table with Repeats/Signal, tool/MCP/retrieval rows, the failed `lookup_order` row |
| `ui-r2-agent-light-1440.png` | The same in the light theme |
| `ui-r2-list-dark-1440.png` / `ui-r2-list-light-1440.png` | Trace list with context bar and one scope panel |
| `ui-r2-agent-spans-390.png` / `ui-r2-agent-details-390.png` | Phone width: rail disclosure, Spans / Selected span switch, the LLM attempt's TTFT 250.0 ms |

**Waterfall row fix found on the screenshots (run 3).** At three-panel width the span names were cut short and the step labels wrapped word by word. The fix:
- the name column takes 46 % of a narrow panel;
- the SRV/INT/CLI kind tag shows only when the panel is wide (the inspector always shows the kind);
- step labels stay on one line, cut with an ellipsis and a title.

**Before and after.** Before UI-R2, the trace page was one long column, and the waterfall plus inspector were the only side-by-side part. There was no list of other traces and no way back to the listing except the back link. Agent steps were a table below the waterfall, and the colors told only LLM, other and error apart. After UI-R2, the page is a three-panel explorer with the listing beside the trace; agent analysis sits above the tree; there are five step families; and the inspector shows TTFT, diagnostics and reviews for the selected span.

## 8. Risks and notes

- The trace page makes one more backend read (the rail). The rate-limit E2E tests do not load trace pages; their reads are unchanged.
- The three-panel layout needs about 1360 px of viewport because the 16 rem app sidebar stays. On a 1280 px laptop the rail is a disclosure.
- "Longest" is a ranking by stored duration and does not mean "slow" against any baseline. Baselines (p95 per span name) are a later step.
- The run-id construction lowers the id space from 16¹² to 6³·16⁹ (about 1.5·10¹³). Ids stay unique per run in practice (200 of 200 distinct in the test).
- Screenshots come from the E2E run's SYNTHETIC fixtures on a disposable database. They show e2e project names and synthetic ids only, and no token, key, URL credential or `.env` value (checked, §7).

## 9. Left for UI-3 (not started)

1. Bring Sessions (session → its traces in the explorer rail), Models, Datasets and Experiments onto the same shell, context bar and scope-panel pattern.
2. Per-span-name duration baselines, so "slow" can be stated against history instead of a ranking.
3. A collapsible app sidebar, so three panels fit on 1280 px screens.
4. Keyboard shortcuts between panels (e.g. `[` / `]` for the previous or next trace in the rail) with a visible shortcut list.
5. Charts restyled on the new surfaces (grid, axis, tooltip), and the visual regression snapshots proposed in UI-R1.
