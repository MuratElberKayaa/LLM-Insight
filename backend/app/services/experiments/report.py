"""Experiment report `llminsight.experiment_report.v1` (Phase 4C, B5). Pure: built only from the
recorded `experiments` / `experiment_results` rows, never from traces or estimates.

Definitions (per prompt version):
- `passed` / `failed` / `not_evaluated`: results with `passed` true / false / NULL;
- `pass_rate = passed / (passed + failed)`: NULL ("not evaluated") results are excluded from
  the denominator (T40); `value` is null when the denominator is 0; 6 decimal places, always
  with numerator and denominator;
- `duration_ms`: linear-interpolated p50/p95 (the `percentile_cont` / NumPy `linear` rule of
  METRICS_CATALOG) over results with a recorded duration, with `n`; `p95_low_sample` below 20;
- `cost.known_cost_usd`: exact `Decimal` sum of computed costs (null when none is computed,
  never "0"), with the computed / unknown counts; `complete` only when every result is priced.

Comparison (A vs B) never names a better version on incomplete evidence:
- quality is compared on **paired** cases, evaluated (non-NULL) under both versions. A case
  NULL under both for a structural reason (`STRUCTURAL_CODES`: it has no usable expected
  output or rule) is excluded by design. Any other NULL (timeout, provider error, no scripted
  output, limits, budget) makes the comparison `partial`, and `higher_pass_rate` stays null;
- cost is compared only when both versions are completely priced: a partially priced total is
  never set against a fully priced one;
- latency is reported per version only; no difference is claimed.
"""

from collections import Counter
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any

CONTRACT = "llminsight.experiment_report.v1"
STRUCTURAL_CODES = frozenset(
    {"expected_output_missing", "expected_output_invalid", "rule_unsupported"}
)
LOW_SAMPLE_P95 = 20
_SIX = Decimal("0.000001")


def _money(value: Decimal | None) -> str | None:
    """Money is a decimal string (API convention, ARCHITECTURE.md §5.2); null stays null."""
    return None if value is None else str(value)


LIMITATIONS = (
    (
        "synthetic_mock",
        "Outputs come from a deterministic mock provider with scripted SYNTHETIC answers; they"
        " say nothing about how a real model would behave.",
    ),
    (
        "synthetic_latency",
        "Durations are scripted synthetic values, not measured latency.",
    ),
    (
        "synthetic_cost",
        "Costs are estimates for synthetic token counts at the stored price of (openai, model)"
        " at the experiment's start; nothing was spent. Unknown cost stays unknown.",
    ),
    (
        "deterministic_rules_only",
        "Quality means passing each case's deterministic rule (llminsight.rule.v1); there is no"
        " semantic, similarity or language-model judgement.",
    ),
    (
        "small_sample",
        "Pass rates come from small samples; no statistical significance is computed.",
    ),
    (
        "single_attempt",
        "One simulated attempt per case and prompt version; the mock does not retry.",
    ),
)


def detail_code(passed: bool | None, detail: str | None) -> str:
    if passed is True:
        return "pass"
    if not detail or ":" not in detail:
        return "unknown"
    return detail.split(":", 1)[0]


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    value = None
    if denominator:
        value = (Decimal(numerator) / Decimal(denominator)).quantize(_SIX, ROUND_HALF_EVEN)
    return {"value": value, "numerator": numerator, "denominator": denominator}


def _percentile(sorted_values: Sequence[int], p: Fraction) -> Decimal | None:
    if not sorted_values:
        return None
    h = (len(sorted_values) - 1) * p
    lo = int(h)
    hi = min(lo + 1, len(sorted_values) - 1)
    exact = Fraction(sorted_values[lo]) + (h - lo) * (sorted_values[hi] - sorted_values[lo])
    return (Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(_SIX, ROUND_HALF_EVEN)


def _sum(values: list[int | None]) -> dict[str, Any]:
    known = [v for v in values if v is not None]
    return {"sum": sum(known) if known else None, "n": len(known)}


def _variant(slot: str, version: str, rows: Sequence[Any]) -> dict[str, Any]:
    passed = sum(1 for r in rows if r.passed is True)
    failed = sum(1 for r in rows if r.passed is False)
    nulls = [r for r in rows if r.passed is None]
    durations = sorted(r.duration_ms for r in rows if r.duration_ms is not None)
    computed = [r.cost_usd for r in rows if r.cost_status == "computed"]
    unknown = [r for r in rows if r.cost_status == "unknown"]
    return {
        "slot": slot,
        "prompt_version": version,
        "results": len(rows),
        "passed": passed,
        "failed": failed,
        "not_evaluated": len(nulls),
        "not_evaluated_by_code": dict(
            sorted(Counter(detail_code(None, r.evaluation_detail) for r in nulls).items())
        ),
        "pass_rate": _rate(passed, passed + failed),
        "duration_ms": {
            "n": len(durations),
            "p50": _percentile(durations, Fraction(1, 2)),
            "p95": _percentile(durations, Fraction(95, 100)),
            "p95_low_sample": len(durations) < LOW_SAMPLE_P95,
        },
        "tokens": {
            "input": _sum([r.input_tokens for r in rows]),
            "output": _sum([r.output_tokens for r in rows]),
        },
        "cost": {
            "known_cost_usd": _money(sum(computed, Decimal(0))) if computed else None,
            "computed": len(computed),
            "unknown": len(unknown),
            "unknown_by_reason": dict(
                sorted(Counter(r.cost_unknown_reason for r in unknown).items())
            ),
            "complete": bool(rows) and not unknown,
        },
    }


def _quality(by_case: dict[int, dict[str, Any]], a: str, b: str, finished: bool) -> dict[str, Any]:
    paired, excluded, unpaired = [], [], []
    for case_id in sorted(by_case):
        ra, rb = by_case[case_id].get(a), by_case[case_id].get(b)
        if ra is None or rb is None:
            unpaired.append(case_id)
        elif ra.passed is not None and rb.passed is not None:
            paired.append((ra.passed, rb.passed))
        elif (
            ra.passed is None
            and rb.passed is None
            and detail_code(None, ra.evaluation_detail) in STRUCTURAL_CODES
            and detail_code(None, rb.evaluation_detail) in STRUCTURAL_CODES
        ):
            excluded.append(case_id)
        else:
            unpaired.append(case_id)
    a_passed = sum(1 for x, _ in paired if x)
    b_passed = sum(1 for _, y in paired if y)
    reason = None
    if not finished:
        reason = "the experiment has not finished"
    elif unpaired:
        reason = (
            "some cases were evaluated under only one prompt version (timeout, provider error,"
            " no scripted output, a limit or the budget), so the versions are not compared"
        )
    elif not paired:
        reason = "no case was evaluated under both prompt versions"
    complete = reason is None
    higher = None
    if complete:
        higher = "a" if a_passed > b_passed else "b" if b_passed > a_passed else "equal"
    return {
        "status": "complete" if complete else "partial",
        "paired_cases": len(paired),
        "excluded_by_design": len(excluded),
        "unpaired_cases": len(unpaired),
        "a_passed_on_paired": a_passed,
        "b_passed_on_paired": b_passed,
        "higher_pass_rate": higher,
        "reason": reason,
    }


def _cost(variants: list[dict[str, Any]], finished: bool) -> dict[str, Any]:
    va, vb = variants
    if not finished:
        return {
            "status": "incomplete",
            "difference_usd_b_minus_a": None,
            "reason": "the experiment has not finished",
        }
    if not (va["cost"]["complete"] and vb["cost"]["complete"]):
        return {
            "status": "incomplete",
            "difference_usd_b_minus_a": None,
            "reason": (
                "at least one prompt version has results with unknown cost; a partially priced"
                " total is not compared with another total"
            ),
        }
    return {
        "status": "complete",
        "difference_usd_b_minus_a": _money(
            Decimal(vb["cost"]["known_cost_usd"]) - Decimal(va["cost"]["known_cost_usd"])
        ),
        "reason": None,
    }


def build(experiment: Any, dataset_name: str, rows: Sequence[Any], iso: Any) -> dict[str, Any]:
    """`experiment`: the stored row; `rows`: its results; `iso`: datetime -> ISO-8601 Z."""
    a, b = experiment.prompt_version_a, experiment.prompt_version_b
    finished = experiment.finished_at is not None
    by_version = {
        a: [r for r in rows if r.prompt_version == a],
        b: [r for r in rows if r.prompt_version == b],
    }
    by_case: dict[int, dict[str, Any]] = {}
    for r in rows:
        by_case.setdefault(r.test_case_id, {})[r.prompt_version] = r
    variants = [_variant("a", a, by_version[a]), _variant("b", b, by_version[b])]
    ids_a = {r.test_case_id for r in by_version[a]}
    ids_b = {r.test_case_id for r in by_version[b]}
    provenance = Counter(r.provenance for r in rows)
    label = (
        "none"
        if not rows
        else "synthetic"
        if set(provenance) == {"synthetic"}
        else "real"
        if set(provenance) == {"real"}
        else "mixed"
    )
    return {
        "contract_version": CONTRACT,
        "experiment": {
            "id": experiment.id,
            "dataset_id": experiment.dataset_id,
            "dataset_name": dataset_name,
            "prompt_version_a": a,
            "prompt_version_b": b,
            "model": experiment.model,
            "mode": experiment.mode,
            "budget_usd": _money(experiment.budget_usd),
            "started_at": iso(experiment.started_at),
            "finished_at": iso(experiment.finished_at) if finished else None,
            "state": "finished" if finished else "running",
        },
        "evaluation": {
            "method": "deterministic_rules",
            "rule_contract": "llminsight.rule.v1",
            "language_model_used": False,
        },
        "provenance": {
            "label": label,
            "synthetic": provenance.get("synthetic", 0),
            "real": provenance.get("real", 0),
            "simulated_provider": experiment.mode == "mock",
        },
        "cases": {
            "count": len(ids_a | ids_b),
            "ids_identical_across_versions": ids_a == ids_b,
        },
        "variants": variants,
        "comparison": {
            "quality": _quality(by_case, a, b, finished),
            "cost": _cost(variants, finished),
        },
        "limitations": [{"code": c, "text": t} for c, t in LIMITATIONS],
        "results": [
            {
                "test_case_id": r.test_case_id,
                "prompt_version": r.prompt_version,
                "slot": "a" if r.prompt_version == a else "b",
                "outcome": (
                    "passed"
                    if r.passed is True
                    else "failed"
                    if r.passed is False
                    else "not_evaluated"
                ),
                "passed": r.passed,
                "detail_code": detail_code(r.passed, r.evaluation_detail),
                "evaluation_detail": r.evaluation_detail,
                "duration_ms": r.duration_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": _money(r.cost_usd),
                "cost_status": r.cost_status,
                "cost_unknown_reason": r.cost_unknown_reason,
                "provenance": r.provenance,
            }
            for r in sorted(rows, key=lambda r: (r.test_case_id, r.prompt_version != a))
        ],
    }
