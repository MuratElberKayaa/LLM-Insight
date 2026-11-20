# Phase 9F — PHASE_STATUS cleanup

**Date:** 2026-11-19 (UTC). **Branch:** `feature/phase-9b-reference-price`. This phase is documentation only.

**Authorization:** the owner asked for the remaining short-term phases to run in one batch, taking the recommended option at each decision and recording it (2026-11-19).

## 1. Problem

The top of `docs/PHASE_STATUS.md` had grown into a free-form running log: 161 lines and about 57 KB above the first phase section. Several "current" paragraphs contradicted each other by date. The actual open items were spread across them; the R102/R144 deadline, for example, sat in the middle of the Phase 8H-CI paragraph.

## 2. Change (recommended option: move verbatim, do not rewrite history)

- **Archive.** The log, lines 2–162 of the old file, was moved **verbatim** to `docs/reports/phase-status-log-2026-11.md`, under a one-paragraph note that says so.
- **New header.** It is about 20 lines:
  - the current state and branch;
  - the provider execution switches as they stand on this branch;
  - a dated table of open items;
  - the latest verified local results;
  - how the file is organised.
- **Unchanged.** Every phase section (Phase 0 onward) and `## Commands` are byte-identical to before.

Rejected:

- **Rewriting or summarising the old paragraphs:** that would edit recorded evidence.
- **Deleting them:** that would lose history the reports refer to.

## 3. Checks

| Check | Result |
|---|---|
| Old header text contained verbatim in the archive | true (scripted comparison) |
| Phase sections after the header byte-identical | true |
| File size | 157 444 → 102 266 characters |
| `python -I scripts/verify_phase0.py` | 865 PASS, 0 FAIL |
