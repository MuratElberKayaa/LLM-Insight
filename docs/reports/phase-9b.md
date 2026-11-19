# Phase 9B — Reference list price (F3): implementation

**Date:** 2026-11-19 (UTC). **Branch:** `feature/phase-9b-reference-price`, created from `release/groq-first-observed-request` at `9120e7f`. Nothing is committed or pushed. **Provider requests:** 0. **Migrations:** 0.

**Authorization:** the owner accepted the design of `reports/phase-9a.md`, its decision record (now ADR-52) and decisions D1–D6 as recommended, and asked for 9B on a new branch (2026-11-19).

## 1. Result

`GET /api/v1/models/compare` now returns a `reference_list_price` block in every group. Models Compare shows it in its own column, "List-price reference (USD)", with a "Not billed" flag and a notice.

The cost ledger is untouched:

- no stored row is written or repriced;
- Gemini, Groq and OpenRouter rows stay `unknown` / `price_missing`;
- the `cost` blocks keep their values.

On the same window as the Phase 3C recording, every non-reference field of the real local backend's response is identical to that recording (§4).

## 2. Changes

| Area | File | Change |
|---|---|---|
| Catalogue | `backend/app/services/reference_pricing_catalog.py` (new) | `reference-2026-11-19`: Gemini `gemini-3.1-flash-lite` paid Standard 0.25 / 0.025 / 1.50; Groq `openai/gpt-oss-20b` on-demand 0.075 / 0.037 / 0.30 (USD per 1M tokens); Decimal only; `STALE_AFTER` 30 days |
| Computation | `backend/app/services/reference_pricing.py` (new) | `GroupUsage` with consistency checks; `reference_block`: exact match, eligibility reasons, the ledger's `pricing.compute_cost`, no amount without a priced call, staleness |
| Query | `backend/app/repositories/metrics.py` | `_reference_columns()` added to `model_groups`: usage row count, eligible count, eligible token sums, cache-write count (additive) |
| API | `backend/app/services/metrics.py` | `compare` adds `reference_list_price` per group; nothing else changed |
| Frontend | `frontend/lib/api/{types,schema}.ts`, `frontend/lib/model-groups.ts`, `frontend/components/models/model-comparison.tsx` | Type and strict decoder with 7 group invariants; reference column and card item; notice shown only when a reference is shown |
| Fixtures | `frontend/tests/fixtures/overview.ts`, `frontend/tests/fixtures/models-compare.live.json` | Builders `noReference` and `geminiReference`; contract fixture re-recorded (§4) |
| Docs | `ADR.md` (ADR-52), `PRICING.md` §9, `METRICS_CATALOG.md` (M15), `ARCHITECTURE.md` §5, this report, `PHASE_STATUS.md` | — |

## 3. Evidence re-read (plan step 1)

Both pricing pages were downloaded as raw HTML on 2026-11-19, between 12:51:42Z and 12:51:43Z (HTTP 200). The Gemini URL `ai.google.dev/gemini-api/docs/pricing` redirected to `aistudio.google.com/docs/pricing`.

- **Groq.** The price card reads Input `$0.075`, Cached Input `$0.037`, Output `$0.30`.
- **Gemini.** In section "Gemini 3.1 Flash-Lite", tab "Standard", column "Paid Tier, per 1M tokens in USD", the page lists:
  - input `$0.25 (text / image / video)`;
  - output `$1.50`, under "Output price (including thinking tokens)";
  - context caching `$0.025 (text / image / video)`.

Both match the 9A read. `verified_at` is 2026-11-19T12:51:42Z.

## 4. Real local backend check (read-only)

The new backend ran on `127.0.0.1:8041` against the local development database and was stopped afterwards. It received only `GET /api/v1/models/compare` for project 1, and nothing was written.

- **Same window and limit as the Phase 3C recording** (2026-09-24T11:50:57Z to 2026-10-25T11:50:57Z, limit 50): 7 groups. With `reference_list_price` and the Phase 6C-C `filters.provider` echo removed, the response equals the old fixture exactly. This shows invariant I3 on real stored data. Every group in that window is `not_in_reference_catalogue`: they are mock, OpenAI and unknown providers. This response is the new `models-compare.live.json`.
- **Window with the real traces** (2026-11-11T00:00Z to 2026-11-19T12:00Z), not stored as a fixture. Every known cost stays `null`.

| Provider / model | Prompt version | Attempts | Reference (USD) | Note |
|---|---|---|---|---|
| gcp.gemini / gemini-3.1-flash-lite | cmp-v1.ticket-triage | 24 | 0.0021915000 | all 24 priced |
| groq / openai/gpt-oss-20b | cmp-v1.ticket-triage | 22 | 0.0015048000 | all 22 priced |
| groq / qwen/qwen3.8-27b | cmp-v1.ticket-triage | 21 | none | `not_in_reference_catalogue` (no verified price) |
| gcp.gemini / gemini-3.1-flash-lite | live-ticket-v1 / v2 | 6 / 6 | 0.0003255000 / 0.0003510000 | — |
| groq / openai/gpt-oss-20b | live-ticket-v1 / v2 | 6 / 6 | 0.0003447750 / 0.0002766750 | — |
| gcp.gemini / (no response model) | cmp-v1.ticket-triage, cmp-v1.release-summary | 2, 1 | none | `no_priced_calls`: usage missing (failed calls) |
| gcp.gemini / gemini-3.1-flash-lite | cmp-v1.json-aggregate | 1 | 0.0001222500 | (189 · 0.25 + 50 · 1.50) / 10^6, checked by hand |
| groq / openai/gpt-oss-20b | cmp-v1.json-aggregate | 1 | 0.0001272000 | (244 · 0.075 + 363 · 0.30) / 10^6, checked by hand |

These amounts are references at list price, not spend, and they come from very small samples.

## 5. Tests

Each command was run from the stated directory with PostgreSQL 17 on the compose service.

| Command | Result |
|---|---|
| `backend: .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/unit/test_reference_pricing.py` | 24 passed |
| `backend: LLMI_TEST_REQUIRE_DB=1 … tests/integration/test_phase9b_reference_price.py` | 8 passed |
| `backend: LLMI_TEST_REQUIRE_DB=1 .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (full) | **1986 passed, 0 failed, 0 skipped** |
| `backend: ruff check .`, `ruff format --check .` | pass, 156 files formatted |
| `sdk: LLMI_TEST_REQUIRE_DB=1 .venv/Scripts/python.exe -m pytest -q -rs -p no:cacheprovider` (full) | **2724 passed, 0 failed** (16 min 15 s; the live-backend integration tests read `models/compare`) |
| `frontend: npm run typecheck`, `npm run lint` | pass |
| `frontend: npm test` | **412 passed, 0 failed, 0 skipped** (19 new in `phase9b.test.tsx`) |
| `frontend: npm run build` (pinned Node 22.23.4), `npm run check:bundle` | pass; 465 files, 23 in the browser bundle, 0 findings |
| `python -I scripts/verify_phase0.py` | 865 PASS, 0 FAIL |

**Mutation check.** Seven mutants of the new backend code were each run against the 32 new tests and then restored. **7 of 7 were caught:**

| Mutant | Tests failed |
|---|---|
| Gemini output price 1.50 → 1.60 | 3 |
| Cache-write filter removed | 7 |
| Eligibility widened to every row | 7 |
| "No priced call" shown as an amount | 1 |
| Cached tokens priced at 0 | 2 |
| Fuzzy, case-folded model match | 2 |
| Staleness boundary moved | 1 |

The restored code passed 32 of 32.

**Not run locally:**

- **Playwright E2E.** The gate `scripts/qa/e2e_gate.sh` refuses to run outside GitHub Actions. The existing compare specs count rows, not columns, so the new column is not expected to affect them. This is unverified until CI runs.
- **Bandit and pip-audit.** No dependency was added.
- **GitHub CI.** The branch is not pushed.

## 6. Invariants (from 9A §3)

| # | Verified by |
|---|---|
| I1, I2 | `test_reading_the_reference_never_writes_or_reprices_the_ledger`: a full ledger snapshot before and after compare and overview reads, the `model_prices` count, the stored outcomes; no migration file |
| I3 | `test_cost_blocks_keep_the_ledger_values`; the real-backend equality of §4 |
| I4 | `test_the_overview_never_carries_a_reference`; frontend tests that the known-cost cell never shows the amount |
| I5 | the `usage_missing` and cache-write rows of the integration test; the eligibility mutants |
| I6 | the worked examples and the equality between per-row and summed costs (unit and integration) |
| I7 | `test_the_model_must_match_exactly`; the fuzzy-match mutant |
| I8 | the metadata assertions; the decoder refuses missing catalogue details and non-`https` sources |

## 7. Risks and open items

- **Misreading.** The label, flag and notice are mitigations, not guarantees.
- **Coverage.** `qwen/qwen3.8-27b` (21 real Groq attempts) has no reference. Adding it needs its official price and a new catalogue version.
- **Modality (D3).** Future audio input to Gemini would be under-referenced.
- **Catalogue age.** The catalogue becomes `stale` after 2026-12-19T12:51:42Z.
- **Still open from before:** R102 and R144 need re-review by 2026-11-30. The Groq Free Plan evidence window ends 2026-11-26.
- **Next step:** owner review, then a commit on this branch and CI, once the owner asks. Pushing is the owner's step.
