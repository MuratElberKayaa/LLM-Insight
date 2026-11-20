# Phase 9C — OpenAI price catalogue re-verification

**Date:** 2026-11-19 (UTC). **Branch:** `feature/phase-9b-reference-price`. This phase is documentation only: no code, catalogue, database or provider-request change.

**Authorization:** the owner asked for the remaining short-term phases to run in one batch, with the recommended option taken at each decision and recorded (2026-11-19).

## 1. Result

The verified catalogue `openai-2026-10-24` is **unchanged** on the official pages. No new version was released (§3).

## 2. Evidence

Five official pages were downloaded raw between 2026-11-19T13:17:43Z and 13:17:46Z. Each returned HTTP 200 with no redirect.

| Page | Finding |
|---|---|
| https://developers.openai.com/api/docs/pricing | The first (Standard) row per model is `gpt-5.4-mini` 0.75 / 0.075 / 4.5, `gpt-5-mini` 0.25 / 0.025 / 2 and `gpt-4.1-mini` 0.4 / 0.1 / 1.6, identical to `PRICING.md` §2. The tier names are Standard, Batch, Flex, Fast and Ultrafast. |
| …/models/gpt-5.4-mini | One snapshot, `gpt-5.4-mini-2026-03-17`; $0.75 / $0.075 / $4.50; the 10 % regional-processing uplift is repeated |
| …/models/gpt-5-mini | One snapshot, `gpt-5-mini-2025-08-07`; $0.25 / $0.025 / $2.00 |
| …/models/gpt-4.1-mini | One snapshot, `gpt-4.1-mini-2025-04-14`; $0.40 / $0.10 / $1.60 |
| …/deprecations | Shutdown dates: `gpt-5-mini` and its snapshot Dec 11, 2026; `gpt-4.1-mini-2025-04-14` Jan 14, 2027. `gpt-5.4-mini` is not listed. |

## 3. Decision: re-verify, do not release (recommended option, recorded)

The SDK live guard (`sdk/src/llm_insight_sdk/live.py`, ADR-29, R83) refuses real OpenAI execution once `PRICE_VERIFIED_AT` is older than 14 days. Since 2026-11-07, that refusal has been in force.

- **Released:** a new, identical catalogue version, for example `openai-2026-11-19`, would mean six new append-only rows and a seeding step. It would also reopen the price check of a paid path for 14 days, and no approved, budgeted smoke test exists to use that window.
- **Recorded only (chosen):** the evidence is written down in `PRICING.md` §2, and the guard stays closed. The release belongs to the smoke-test phase: re-read, release, seed and run inside one approved window.

Consequence: OpenAI live execution stays refused by the price check, which is unchanged behaviour. Ledger pricing of OpenAI spans is unaffected, because the stored rows still apply from 2026-10-24T21:40Z.

## 4. Upcoming catalogue events

- **2026-12-11:** `gpt-5-mini` shutdown. Its rows stay valid for historical events (`PRICING.md` §2, "Lifecycle").
- **2027-01-14:** `gpt-4.1-mini` shutdown, as above.
- **2026-12-19T12:51:42Z:** the Phase 9B reference catalogue becomes `stale` (`PRICING.md` §9).

## 5. Checks

| Command | Result |
|---|---|
| `python -I scripts/verify_phase0.py` | 865 PASS, 0 FAIL |

No test suite is affected: no code changed.
