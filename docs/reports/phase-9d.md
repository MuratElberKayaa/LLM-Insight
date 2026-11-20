# Phase 9D — Confidence intervals and the exact paired test (F5)

**Date:** 2026-11-19 (UTC). **Branch:** `feature/phase-9b-reference-price`. No migration, no new write path and no provider request.

**Authorization:** the owner asked for the remaining short-term phases to run in one batch, taking the recommended option at each decision and recording it (2026-11-19). The decision is recorded as ADR-53.

## 1. Result

Both A/B reports now show how much their small samples can establish:

- **Every pass rate** has a 95 % Wilson interval.
- **Every complete paired comparison** has an exact two-sided McNemar test.

The two stored live A/B runs (Groq `openai/gpt-oss-20b` and Gemini `gemini-3.1-flash-lite`, read-only from the local database) give the same picture:

| Model | v1 passed | v1 95 % interval | v2 passed | v2 95 % interval | Discordant pairs | Exact p |
|---|---|---|---|---|---|---|
| openai/gpt-oss-20b | 3 / 6 | 18.76 % – 81.24 % | 5 / 6 | 43.65 % – 96.99 % | 0 v1-only, 2 v2-only | 0.5 |
| gemini-3.1-flash-lite | 3 / 6 | 18.76 % – 81.24 % | 5 / 6 | 43.65 % – 96.99 % | 0 v1-only, 2 v2-only | 0.5 |

v2 passed more cases on both models, and both disagreements favour v2, but the difference is **not significant**. With six paired cases, p < 0.05 needs at least six disagreements, all in the same direction. A non-significant result is not evidence that the versions are equal, and no winner is named (ADR-51).

## 2. Decisions (recommended options, recorded)

| Question | Chosen | Rejected |
|---|---|---|
| Interval for a pass rate | Wilson score, 95 % | Wald (it collapses at 0 or n passes); bootstrap (random, and meaningless at n = 6) |
| Test for A vs B | Exact two-sided McNemar on paired cases, computed with integers, decided on the exact fraction | Fisher's exact test and the two-proportion z-test (both ignore the pairing) |
| Mock report contract | Additive: `pass_rate_wilson_95` per variant, `paired_test` on a complete comparison; `higher_pass_rate` unchanged | Turning `higher_pass_rate` into a significance verdict (it would change ADR-25) |
| Live import | Statistics derived at read time from the stored cases; the snapshot is unchanged; `statistical_significance` reads `exact_mcnemar` | Rewriting the immutable snapshot |
| Module name | `ab_statistics.py` | `statistics.py`, which shadows the standard library module that `imported_experiments.py` uses for medians |

## 3. Changes

| Area | File | Change |
|---|---|---|
| Statistics | `backend/app/services/ab_statistics.py` (new) | `wilson_interval`, `mcnemar_p_fraction`, `exact_mcnemar`, `min_discordant_for`; Decimal results, half-even rounding to 6 places |
| Mock report | `backend/app/services/experiments/report.py` | Adds `pass_rate_wilson_95` and `paired_test`; the `small_sample` limitation text now describes both |
| Live import | `backend/app/api/imported_experiments.py` | `derived_statistics()` in `document()` |
| Frontend | `frontend/lib/api/{experiment-types,experiment-schema,imported-experiments}.ts` | Strict decoders: interval bounds, interval ⇔ evaluated, test ⇔ complete, discordant pairs = difference of passes, no significance below the minimum, significance matching p |
| Frontend | `frontend/components/experiments/{values,experiment-report,imported-report}.tsx` | Interval row and column; one sentence per test |
| Docs | `ADR.md` (ADR-53), `METRICS_CATALOG.md` (M16), `ARCHITECTURE.md` §5 | — |

## 4. Tests

| Command | Result |
|---|---|
| `backend: … pytest tests/unit/test_ab_statistics.py` | 21 passed. The Wilson reference values come from an independent float computation; the McNemar values are exact fractions |
| `backend: LLMI_TEST_REQUIRE_DB=1 … pytest` (full) | **2008 passed, 0 failed, 0 skipped** |
| `frontend: npm run typecheck`, `npm run lint` | pass |
| `frontend: npm test` | **430 passed, 0 failed, 0 skipped** (18 new) |
| `backend: ruff check .`, `ruff format --check .` | pass |

**Independent checks.**

- Four Wilson values I first typed from memory were wrong: 3/6, 5/6, 1/12 and 10/12. A separate float computation replaced them before any test ran against them, and the code then matched that computation.
- The golden integration experiment (7/11 and 4/10) is pinned the same way.
- The import test checks that the stored snapshot has no `statistics` key and still reads `not_computed`.

**Mutation check** (unit tests; each mutant restored afterwards).

| Mutant | Result |
|---|---|
| `Z_95` rounded to 1.96 | caught (7 failed) |
| Binomial tail off by one | caught (11) |
| One-sided p-value | caught (11) |
| Concordant pairs counted as discordant | caught (1) |
| Test reported on a partial comparison | missed at first; a unit assertion was added; caught (2) |
| `p < 0.05` → `p <= 0.05` | not caught: **equivalent**. p = 2k / 2ⁿ is a dyadic fraction and never equals 1/20 |
| Exact 0 lower bound removed | not caught: **equivalent**. The Wilson formula gives exactly 0 at k = 0 |

**Not run locally:** Playwright E2E. Its gate runs only in GitHub Actions. `phase4d.spec.ts` still asserted the old "no significance test" text, and that expectation was updated to the new test sentence. Until CI runs, this is unverified.

## 5. Limits

The intervals and the test describe sampling uncertainty only. They do not cover provider non-determinism beyond one sample per version, the synthetic inputs, or the operator-imported rule results (ADR-51).
