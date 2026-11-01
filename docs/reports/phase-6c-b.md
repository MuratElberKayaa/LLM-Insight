# Phase 6C-B — First real Gemini Free Tier call: acceptance evidence

**Recorded:** 2026-11-01 (UTC), in the Phase 6C-C session, from evidence that already existed. **No provider request was sent while writing this report**; nothing was re-run, regenerated or changed in the stored trace. The call itself was executed by the user (operator guide `reports/phase-6c-a.md` §10) on branch `feature/phase-6c-gemini-free` at `580d457`; no commit was made between the call and this report.

Two kinds of evidence are kept apart below: what the **operator reported** from the runner output and the dashboard, and what was **read back** in this session through the read-only API and the dashboard (GET requests only).

## 1. Operator-reported evidence (not re-observed here)

As stated by the user in the Phase 6C-C instruction:

| Item | Reported value |
|---|---|
| Provider / model | `gcp.gemini` / `gemini-3.1-flash-lite` |
| Response status | `completed` |
| Tokens | input 18, output 1, total 19 |
| Duration | approximately 6751 ms |
| Exported spans | 2 |
| Cost | unknown |
| Usage consistency | consistent |
| Trace ID | `bec4f18a3f9a29375a61290dfc62798d` |
| Dashboard | shown in the Trace Explorer under project `gemini-live` (#148), provenance labelled as asserted by the instrumentation, not independently verified |

**Not verified in this session** (operator-side only): the runner's exit code and full output, the number of requests Google received, the AI Studio usage page, the project's Billing Tier after the call, and the removal of `GEMINI_API_KEY` from the shell (§10 steps 6–7 of the operator guide).

## 2. Read-back in this session (2026-11-01, GET only)

PostgreSQL (`llminsight-postgres-1`) had been stopped since the previous day; it was started on its existing volume (`docker compose up -d --wait postgres`, nothing recreated or wiped). The trace was then read through `/api/v1` with the admin token (never printed) and through the dashboard (a read-only browser script; its log shows **no non-GET request**).

| Check | Expected | Read back | Result |
|---|---|---|---|
| Project | `gemini-live` | id 148, created 2026-10-28T18:18:00.919388Z (the only project of that name) | PASS |
| Trace readable in its project | yes | `GET /api/v1/traces/bec4f18a…798d?project=148` → 200 | PASS |
| Spans and parent | 2, correct parent | root `demo.live-gemini` (INTERNAL, `UNSET`, provenance unknown: not an inference span) → child `generate_content gemini-3.1-flash-lite` (CLIENT, `UNSET`, `real`), `parent_span_id` = the root | PASS |
| Provider / models | `gcp.gemini`, `gemini-3.1-flash-lite` | usage row: provider `gcp.gemini`, requested and response model `gemini-3.1-flash-lite` | PASS |
| Tokens | input 18, output 1 | 18 / 1, `usage_source = provider_reported` (the stored row has no total column; the total 19 is the runner's printout) | PASS |
| Trace status | OK | `status = ok`, span count 2 | PASS |
| Duration | ≈ 6751 ms | `duration_ns = 6751514200` | PASS |
| Start (event time) | — | `2026-10-29T10:48:49.757174600Z` | recorded |
| Cost | unknown, never 0 | `cost_status = unknown`, `cost_unknown_reason = price_missing`, `cost_usd = null`; overview `known_cost_usd = null`, `cost_complete = false` | PASS |
| Provenance | asserted real | trace `real`, usage row `real`, `provenance_verified = false` everywhere | PASS |
| Aggregates | one attempt, one trace | overview (31-day default window): traces 1 (real 1), attempts 1 (real 1); `models/compare`: one group `gcp.gemini / gemini-3.1-flash-lite / gemini-3.1-flash-lite`, 1 attempt | PASS |
| Metadata only | no content | no prompt, output, key or provider message in any response read (allowlisted metadata only) | PASS |

**Dashboard read-back (Phase 6C-C build, 7-day window):** Overview "Latest stored trace" = this trace (event time 2026-10-29 10:48:49.757 UTC, 6.751 s, OK, Real (asserted), `gcp.gemini · gemini-3.1-flash-lite ×1`), provenance banner "The instrumentation labeled this data real. That label is an assertion and is not verified by the server"; with `provenance=real&provider=gcp.gemini` the same trace; with `provenance=synthetic` the empty state. Models: one `gcp.gemini` group, tokens 18 / 1, cost "unknown (no priced attempt) · 1 no price" (never $0). Traces (`provider=gcp.gemini`): exactly this trace. Trace detail: 2 spans, 18 / 1, unknown cost, asserted-real notice. Screenshots `phase-6c-c-screenshots/08…11`.

**Time-window note:** the dashboard's default window is the last 24 hours; the trace (2026-10-29) is outside it since 2026-10-30T10:48Z, so the default Overview of project 148 shows the empty state. That is correct behaviour (event-time windows, `METRICS_CATALOG.md`), not a missing trace: use 7 days or 31 days. No compatibility defect was found, and nothing in the stored trace was modified.

## 3. Observations for the review

- **`.env.example` in the working tree** is modified (uncommitted, not by this session): it carries live-run values (`LLMI_LIVE_CONSENT`, `LLMI_GEMINI_MODE`, tier attestation dated 2026-10-28, limits, and `LLMI_LIVE_PROVIDERS="real"`, which is not a provider id; the operator guide uses `gcp.gemini`). The committed template is deliberately empty and fail-closed (Phase 6C-A), and `scripts/verify_phase0.py` fails on the modified copy (`LLMI_LIVE_MAX_*_TOKENS` are not placeholders). It holds no key. It was **not** committed or changed in Phase 6C-C; the user decides whether to revert it.
- The evidence window of the Free Tier allowlist ends 2026-11-11T13:00Z (`FREE_TIER_VERIFIED_AT` + 14 days); a later call is refused until the official pages are re-read.

## 4. Verdict

The first real Gemini Free Tier trace is stored, readable in its own project, metadata-only, with the expected two-span structure, provider-reported tokens 18/1, unknown (never zero) cost and asserted (never verified) real provenance. Operator-side items in §1 remain the operator's evidence. **Do not run another real provider request** without a new explicit authorization.
