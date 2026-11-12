# Phase 8G-A — Groq Free Plan evidence (evidence-only change)

**Date:** 2026-11-12 UTC. **Branch:** `feature/live-comparison-development`, on `8618f41` (same tree as `origin/main` `9556ee7`).

**Result:**
- Groq's model allowlist and evidence date are released.
- **Real execution stays disabled:** `groq_adapter.REAL_EXECUTION_ENABLED = False`.
- 0 provider requests; no `--execute`; `.env`, Gemini and OpenRouter unchanged.

## 1. Account evidence (operator-provided, 2026-11-12 UTC)

| Item | Value as provided |
|---|---|
| Model | `openai/gpt-oss-20b` |
| Account plan | Free (**operator attestation**, not verified with Groq's billing) |
| Requests per minute (RPM) | 30 |
| Requests per day (RPD) | 1000 |
| Tokens per minute (TPM) | 8000 |
| Tokens per day (TPD) | 200000 |
| Source | the account's Limits page in the Groq console, checked by the operator on 2026-11-12 UTC; time of day not stated |

These values are recorded as provided and are not derived from public documentation.

They happen to equal the `openai/gpt-oss-20b` row of the public rate-limits table. That page labels its table "the base limits for the Developer plan", and the models page shows other Developer-plan figures (1K RPM, 250K TPM). Only the account page is authoritative for this account.

**What one comparison request needs:** 1 request; at most 685 input-bound plus 1024 output tokens, about 1.7K. That is well inside 30 RPM, 1000 RPD, 8000 TPM and 200000 TPD.

## 2. Official pages (read 2026-11-12, `reports/phase-8d.md` §2)

- https://console.groq.com/docs/models: `openai/gpt-oss-20b` under "Production Models"; 131,072 context, 65,536 max completion.
- https://console.groq.com/docs/deprecations: not deprecated; named as a recommended replacement.
- https://console.groq.com/docs/rate-limits: "You can view the current, exact rate limits for your organization on the limits page in your account settings."

## 3. Change

| File | Change |
|---|---|
| `sdk/src/llm_insight_sdk/groq_live.py` | `FREE_PLAN_MODELS` lists exactly `openai/gpt-oss-20b`, with the models and deprecations pages and the account Limits page (this report) as sources. `FREE_PLAN_EVIDENCE_VERIFIED_AT = 2026-11-12 06:00 UTC`; the 14-day window ends **2026-11-26 06:00 UTC** |
| `sdk/tests/unit/test_groq_live_guard.py` | the shipped-state tests now assert the released evidence; inside its window the only refusal is `real_execution_disabled` |
| `sdk/tests/unit/test_compare_runner.py` | shipped refusals are now `{real_execution_disabled}`; one released model |
| `sdk/tests/unit/test_gemini_execution_gate.py` | its Groq assertion now expects the released allowlist. No Gemini code or behaviour changes |

**Not changed:**
- `groq_adapter.REAL_EXECUTION_ENABLED` stays `False`; releasing it is a separate reviewed change (Phase 8G patch B), after a written authorization;
- the per-run attestation `LLMI_GROQ_PLAN_CHECKED_ON` (at most 1 day old) is still required at every send.

## 4. Verification (local, offline)

| Check | Result |
|---|---|
| Groq guard and adapter, all three comparison runners, Gemini gate, OpenRouter guard (unit) | **1108 passed, 0 failed** |
| Dry-run probe (never `--execute`; fake key, project 1, real clock, local OTLP stub) | Groq: only `real_execution_disabled`. Gemini unchanged: `free_tier_evidence_stale`, `real_execution_disabled`. OpenRouter unchanged: four refusals. Nothing sent, 0 network attempts |
| Ruff, docs, secrets | Ruff check and format clean; `verify_phase0.py` 799 PASS; raw detectors 0 matches; history-secret gate in a throwaway clone PASS (40 = 40, 0 findings in the new blobs) |

No database-backed test was run against the developer PostgreSQL instance.

## 5. Groq GO / NO-GO

**NO-GO.** Still needed:
1. this commit pushed, with CI green;
2. your written authorization for one request;
3. the release commit (patch B), with CI green;
4. a current attestation and the consent phrase in your shell.

Evidence expires 2026-11-26 06:00 UTC.
