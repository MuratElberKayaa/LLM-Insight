# Phase 8E — Independent Gemini execution switch

**Date:** 2026-11-12 UTC. **Branch:** `feature/live-comparison-development`, on `e7ab7e9` (the same tree as `origin/main` `008d58b`, the PR #13 merge).

**Result:**
- `gemini_live.REAL_EXECUTION_ENABLED = False` (ADR-50), closing R140, for review.
- 0 provider requests made; no `--execute` used.
- No change to `.env`, the evidence, the database, migrations, Groq or OpenRouter.

## 1. Change

| File | Change |
|---|---|
| `sdk/src/llm_insight_sdk/gemini_live.py` | `REAL_EXECUTION_ENABLED: bool = False`, a code constant with no environment override. `evaluate_free_tier_policy` refuses `real_execution_disabled` while it is false. `FreeTierSession.reserve` refuses with the same code (second layer for `run_single_request`) |
| `demo_app/live_compare.py` | Gemini constraints report `real_execution_enabled` |
| `sdk/tests/conftest.py` | test-only `gemini_released` fixture (monkeypatch, this test process only) |
| 5 Gemini unit and 3 integration test modules | `usefixtures("gemini_released")`, as the Groq and OpenRouter tests release theirs |
| `sdk/tests/unit/test_gemini_execution_gate.py` (new) | 30 tests, run as shipped |
| `docs/ADR.md` (ADR-50), `SOURCES_AND_RISKS.md` (R140), `SECURITY.md` §6, `README.md`, this report, `PHASE_STATUS.md` | documentation |

**Paths blocked by the switch:**
- `demo_app.live_gemini`, through the policy, and through the session if its shared path is called directly;
- `demo_app.live_gemini_content`, through the same policy;
- `demo_app.live_compare --provider gemini`: the policy at planning, again immediately before the send, and the session.

**Unchanged:**
- the Free Tier attestation, the evidence window (`FREE_TIER_VERIFIED_AT` is still 2026-10-28 13:00 UTC), the model allowlist and the R123 hardening;
- one request, zero retries, no paid fallback or substitution, the token and timeout ceilings, metadata-only telemetry.

## 2. Tests (local; nothing here is GitHub CI)

| Check | Result |
|---|---|
| `test_gemini_execution_gate.py` (as shipped) | **30 passed**. They show that: the default is false; it is the only refusal of an otherwise valid request; valid evidence alone, or valid consent alone, cannot enable it; no environment variable (five names × five values, also via `os.environ`) can enable it; the switch is never read from the environment; the session refuses a hand-built policy; the smoke, content and comparison runners exit 2 with no client built, no transport call and no span, and the comparison runner never takes the shared slot; the comparison send path re-checks the switch; switched on, ten Free Tier and R123 safeguards and expired evidence still refuse; the evidence and allowlist are unchanged; the Groq and OpenRouter locks stay closed |
| Existing Gemini unit suites under the test-only release | **370 passed** |
| Full SDK suite, unit and integration (non-editable install byte-identical to this checkout) | **2614 passed, 0 failed, 0 skipped**; this includes the 112 database-backed integration tests, among them the three-provider comparison test (see §2.1) |
| Ruff check and format | clean |
| Docs (`verify_phase0.py`) | **799 PASS, 0 FAIL** |
| Secrets | the 11 raw detectors: 0 matches in the 18 changed files; history-secret gate in a throwaway clone (18 blobs on `e7ab7e9`, PR-style merge onto `008d58b`): **PASS, 40 = 40, 0 problems, 0 findings in the new blobs** |

### 2.1 Incident: the database-backed tests used the developer PostgreSQL server

**What happened.**
- The full run was made inside the owner's checkout. The SDK integration `conftest.py` takes `LLMI_DATABASE_URL` from the repository `.env` when the variable is unset, and starts backends with `backend/.venv`.
- Unsetting the variable in the test command did not prevent that fallback.
- The harness, not the operator session, read `.env`; no value was printed.
- It created the separate disposable database `llminsight_sdk_it_test` on the developer PostgreSQL server, ran throwaway backends against it with SYNTHETIC data, and dropped it at session end. The run exited 0, so the teardown ran.

**Not involved:**
- the owner's `llminsight` database and project 1;
- any provider request: those tests block every non-loopback connection.

This breaks the instruction not to change the database. The owner may want to confirm that `llminsight_sdk_it_test` no longer exists. Earlier phases ran these tests in scratch worktrees without `.env`, where they skipped.

**Prevention.** Run SDK integration tests in this checkout only with an explicit, disposable `LLMI_DATABASE_URL`, or only in CI.

## 3. Dry-run blockers (readiness probe, never `--execute`)

The probe uses the operator settings of `reports/phase-8d.md` §6, SYNTHETIC keys, project 1, the real clock and the local OTLP stub.

| Provider | Refusals | Change from 8D |
|---|---|---|
| Groq | `real_execution_disabled`, `free_plan_evidence_missing`, `model_not_free_plan_verified` | none |
| **Gemini** | **`real_execution_disabled`**, `free_tier_evidence_stale` | the switch added |
| OpenRouter | `real_execution_disabled`, `free_model_evidence_missing`, `free_routing_evidence_missing`, `model_not_free_verified` | none |

All three: exit 0, nothing sent, `execution_ready: true`, `sdk_checkout_match: true`, 0 network attempts.

## 4. Remaining GO / NO-GO conditions

| Provider | Verdict | Before the first request |
|---|---|---|
| Groq | **NO-GO** | account Limits evidence; reviewed release (allowlist, evidence date, `groq_adapter.REAL_EXECUTION_ENABLED`); authorization |
| Gemini | **NO-GO** | AI Studio Billing Tier and limits check; **two** separate reviewed changes (re-release `FREE_TIER_VERIFIED_AT`; then set `gemini_live.REAL_EXECUTION_ENABLED = True`); a current attestation; authorization; confirmation that PR #12 counts as the R123 review |
| OpenRouter | **NO-GO** | R135 (zero-cost guarantee undocumented) and free-endpoint routability under `data_collection: deny`, both unresolved (`reports/phase-8d.md` §3) |
