# Phase 10E — CI regression gate on the golden set (F4)

**Date:** 2026-11-20 (UTC). **Branch:** `feature/phase-10-medium-term`. No migration, no secret, no provider request. The decision is recorded as ADR-58 (batch authorization, recommended options).

## 1. Result

`python -m app.eval_gate --config evals/gate.json` (from `backend/`) runs the SYNTHETIC golden set through the scripted mock and the deterministic rule engine. It needs no database, settings, network or model. It compares a candidate prompt version with a baseline on paired cases:

- **Fails** when `regressions + lost` exceeds `max_regressions`, or when the candidate pass rate is below `min_pass_rate`.
- **Writes** a JSON report (`llminsight.eval_gate.v1`) with the counts, Wilson intervals and the exact McNemar test.
- **Exits** 0 on pass, 1 on fail, 2 on a configuration error.

The CI `python` job runs it after the backend tests with the shipped config. That config uses `golden-prompt-v1` as both baseline and candidate, `max_regressions` 0 and `min_pass_rate` 0.6, so the gate passes.

## 2. Evidence that it gates

With `candidate = golden-prompt-v2`, the gate fails on exactly the cases that `golden.py`'s table predicts:

- regressions G04, G07, G08, G09 and G10;
- improvements G02 and G03;
- pass counts of 7 (v1) and 4 (v2);
- exact McNemar p = 2 · (1 + 7 + 21) / 128 = 0.453125;
- the reason: `FAIL (regressions, pass_rate)`.

## 3. Decisions (recommended options)

| Question | Chosen | Rejected |
|---|---|---|
| What fails | Counts: any pass lost to a fail or to "not evaluated", plus a pass-rate floor | Significance, which 12 cases can never reach for one regression |
| Where it runs | An offline module, `app.eval_gate`, apart from the admin CLI (which needs a database) | Real models in CI (secrets, spend) |
| Configuration | `backend/evals/gate.json`, validated strictly (decimal-string rate, integer allowance, known versions only) | Environment variables |
| Self-comparison | Allowed as "no change", by comparing each case with itself | Refusing it |

## 4. Changes

- **Code:** `app/services/experiments/gate.py` (new, pure), `app/eval_gate.py` (new, entry point) and `backend/evals/gate.json` (new).
- **CI:** `.github/workflows/ci.yml` gains one step in the `python` job, with the report written to `$RUNNER_TEMP`.
- **Docs:** `ADR.md` (ADR-58) and `ARCHITECTURE.md` §5.

## 5. Tests

`backend: … pytest tests/unit/test_eval_gate.py` gives **20 passed**. The tests cover:

- regressions, lost passes and improvements;
- a lost pass counting against the allowance;
- the exact rate floor (2/3 passes, 0.667 fails);
- no evaluated case;
- malformed inputs;
- the shipped config passing;
- v1 → v2 failing on the documented cases;
- configuration errors (dataset, version, negative or boolean allowance, float or out-of-range rate, an extra key, an unreadable or missing file) exiting 2.

**A defect caught by the tests.** `run(argv, out=sys.stdout)` bound `sys.stdout` when the module was imported, so redirected output was lost. It is now resolved on each call.

## 6. Limits

The gate guards the evaluation mechanism and the scripted golden outputs, not a real model. A team's own cases need a case-file format (a follow-up). The CI run is first verified after the owner's push.
