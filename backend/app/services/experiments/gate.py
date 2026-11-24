"""Regression gate for prompt versions (Phase 10E, ADR-58). Pure: results in, verdict out.

A candidate prompt version is compared with a baseline on the same cases (paired):

- `regressions`: cases that pass under the baseline and fail under the candidate;
- `lost`: cases that pass under the baseline and are not evaluated under the candidate
  (timeout, provider error, no output) — a lost pass is never silently ignored;
- `improvements`: cases that fail under the baseline and pass under the candidate.

The gate passes when `regressions + lost <= max_regressions` and the candidate's pass rate
(passed / evaluated) is at least `min_pass_rate`. The statistics of ADR-53 (Wilson interval,
exact McNemar) are reported for reading only: with the small golden sets of this project they
can rarely reach significance, so the gate decides on counts, which a single regression moves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any

from app.services import ab_statistics

CONTRACT = "llminsight.eval_gate.v1"


@dataclass(frozen=True)
class CaseResult:
    case: str  # a stable key, e.g. G01
    version: str
    passed: bool | None  # None = not evaluated
    code: str


def _rate(passed: int, evaluated: int) -> Decimal | None:
    if evaluated == 0:
        return None
    return (Decimal(passed) / Decimal(evaluated)).quantize(Decimal("0.000001"))


def evaluate(
    results: Iterable[CaseResult],
    *,
    baseline: str,
    candidate: str,
    max_regressions: int = 0,
    min_pass_rate: Fraction = Fraction(0),
) -> dict[str, Any]:
    if baseline == candidate:
        raise ValueError("baseline and candidate must differ")
    if max_regressions < 0 or not Fraction(0) <= min_pass_rate <= Fraction(1):
        raise ValueError("max_regressions >= 0 and 0 <= min_pass_rate <= 1")
    by: dict[str, dict[str, CaseResult]] = {}
    for r in results:
        if r.version not in (baseline, candidate):
            raise ValueError(f"unexpected version {r.version!r}")
        if r.case in by.get(r.version, {}):
            raise ValueError(f"duplicate result for {r.case} under {r.version}")
        by.setdefault(r.version, {})[r.case] = r
    base, cand = by.get(baseline, {}), by.get(candidate, {})
    if set(base) != set(cand) or not base:
        raise ValueError("both versions need results for the same, non-empty set of cases")
    cases = sorted(base)
    regressions = [c for c in cases if base[c].passed is True and cand[c].passed is False]
    lost = [c for c in cases if base[c].passed is True and cand[c].passed is None]
    improvements = [c for c in cases if base[c].passed is False and cand[c].passed is True]

    def side(rows: dict[str, CaseResult]) -> dict[str, Any]:
        passed = sum(r.passed is True for r in rows.values())
        failed = sum(r.passed is False for r in rows.values())
        bounds = ab_statistics.wilson_interval(passed, passed + failed)
        return {
            "passed": passed,
            "failed": failed,
            "not_evaluated": len(rows) - passed - failed,
            "pass_rate": _rate(passed, passed + failed),
            "pass_rate_wilson_95": None
            if bounds is None
            else {"low": bounds[0], "high": bounds[1]},
        }

    b, c = side(base), side(cand)
    rate = Fraction(c["passed"], c["passed"] + c["failed"]) if c["passed"] + c["failed"] else None
    reasons = []
    if len(regressions) + len(lost) > max_regressions:
        reasons.append("regressions")
    if rate is None or rate < min_pass_rate:
        reasons.append("pass_rate")
    return {
        "contract_version": CONTRACT,
        "baseline": {"prompt_version": baseline, **b},
        "candidate": {"prompt_version": candidate, **c},
        "cases": len(cases),
        "regressions": regressions,
        "lost": lost,
        "improvements": improvements,
        "paired_test": ab_statistics.exact_mcnemar(
            sum(base[x].passed is True and cand[x].passed is False for x in cases),
            sum(base[x].passed is False and cand[x].passed is True for x in cases),
        ),
        "thresholds": {
            "max_regressions": max_regressions,
            "min_pass_rate": Decimal(min_pass_rate.numerator) / Decimal(min_pass_rate.denominator),
        },
        "passed": not reasons,
        "failed_on": reasons,
    }
