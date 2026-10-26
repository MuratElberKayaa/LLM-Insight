"""Experiment report (Phase 4C, B5): pass-rate denominator (T40), percentiles, exact cost with
coverage, and comparisons that never declare a better version on incomplete evidence."""

from collections import namedtuple
from datetime import UTC, datetime
from decimal import Decimal

from app.services.experiments import report

Exp = namedtuple(
    "Exp",
    "id dataset_id prompt_version_a prompt_version_b model mode budget_usd started_at finished_at",
)
Row = namedtuple(
    "Row",
    "test_case_id prompt_version passed evaluation_detail duration_ms input_tokens output_tokens"
    " cost_usd cost_status cost_unknown_reason provenance",
)
T = datetime(2026, 10, 26, 12, tzinfo=UTC)


def exp(finished=True):
    return Exp(1, 2, "a-v1", "b-v2", "m", "mock", None, T, T if finished else None)


def row(case, version, passed, code=None, duration=100, cost="0.0000010000", reason=None):
    detail = None if passed is True else f"{code}: SYNTHETIC detail"
    computed = cost is not None
    return Row(
        case,
        version,
        passed,
        detail,
        duration,
        10,
        5,
        Decimal(cost) if computed else None,
        "computed" if computed else "unknown",
        None if computed else (reason or "usage_missing"),
        "synthetic",
    )


def build(rows, finished=True):
    return report.build(exp(finished), "golden", rows, lambda d: d.isoformat())


def test_t40_not_evaluated_results_leave_the_pass_rate_denominator():
    rows = [
        row(1, "a-v1", True),
        row(2, "a-v1", False, "schema_violation"),
        row(3, "a-v1", None, "expected_output_missing"),
        row(1, "b-v2", True),
        row(2, "b-v2", True),
        row(3, "b-v2", None, "expected_output_missing"),
    ]
    a, b = build(rows)["variants"]
    assert (a["passed"], a["failed"], a["not_evaluated"]) == (1, 1, 1)
    assert a["pass_rate"] == {"value": Decimal("0.500000"), "numerator": 1, "denominator": 2}
    assert b["pass_rate"] == {"value": Decimal("1.000000"), "numerator": 2, "denominator": 2}
    assert a["not_evaluated_by_code"] == {"expected_output_missing": 1}


def test_pass_rate_is_null_without_any_evaluated_result():
    v = build([row(1, "a-v1", None, "provider_timeout"), row(1, "b-v2", None, "provider_timeout")])
    assert v["variants"][0]["pass_rate"] == {"value": None, "numerator": 0, "denominator": 0}


def test_percentiles_use_linear_interpolation_with_n():
    durations = [100, 200, 300, 400, 1000]
    rows = [row(i, "a-v1", True, duration=d) for i, d in enumerate(durations, 1)]
    rows += [row(i, "b-v2", True, duration=None) for i in range(1, 6)]
    a, b = build(rows)["variants"]
    assert a["duration_ms"] == {
        "n": 5,
        "p50": Decimal("300.000000"),
        "p95": Decimal("880.000000"),  # 400 + 0.8 * (1000 - 400), NumPy 'linear'
        "p95_low_sample": True,
    }
    assert b["duration_ms"] == {"n": 0, "p50": None, "p95": None, "p95_low_sample": True}


def test_costs_are_exact_strings_with_coverage():
    rows = [
        row(1, "a-v1", True, cost="0.0000186000"),
        row(2, "a-v1", True, cost="0.0000000001"),
        row(1, "b-v2", True, cost="0.0000186000"),
        row(2, "b-v2", None, "provider_timeout", cost=None),
    ]
    doc = build(rows)
    a, b = doc["variants"]
    assert a["cost"] == {
        "known_cost_usd": "0.0000186001",
        "computed": 2,
        "unknown": 0,
        "unknown_by_reason": {},
        "complete": True,
    }
    assert b["cost"]["known_cost_usd"] == "0.0000186000" and b["cost"]["complete"] is False
    assert b["cost"]["unknown_by_reason"] == {"usage_missing": 1}
    # A partially priced total is never compared with a fully priced one.
    assert doc["comparison"]["cost"]["status"] == "incomplete"
    assert doc["comparison"]["cost"]["difference_usd_b_minus_a"] is None
    assert doc["results"][3]["cost_usd"] is None  # unknown is null, never "0"


def test_unknown_cost_only_variant_has_null_known_cost_not_zero():
    doc = build([row(1, "a-v1", True, cost=None), row(1, "b-v2", True, cost=None)])
    assert doc["variants"][0]["cost"]["known_cost_usd"] is None


def test_complete_comparison_reports_the_difference_and_higher_pass_rate():
    rows = [
        row(1, "a-v1", True, cost="0.0000020000"),
        row(2, "a-v1", False, "field_mismatch", cost="0.0000020000"),
        row(1, "b-v2", True, cost="0.0000010000"),
        row(2, "b-v2", True, cost="0.0000010000"),
        row(3, "a-v1", None, "expected_output_missing"),  # excluded by design (both NULL)
        row(3, "b-v2", None, "expected_output_missing"),
    ]
    rows = [
        r._replace(
            cost_usd=r.cost_usd or Decimal("0.0000010000"),
            cost_status="computed",
            cost_unknown_reason=None,
        )
        for r in rows
    ]  # noqa: E501
    q = build(rows)["comparison"]
    assert q["quality"] == {
        "status": "complete",
        "paired_cases": 2,
        "excluded_by_design": 1,
        "unpaired_cases": 0,
        "a_passed_on_paired": 1,
        "b_passed_on_paired": 2,
        "higher_pass_rate": "b",
        "reason": None,
    }
    assert q["cost"] == {
        "status": "complete",
        "difference_usd_b_minus_a": "-0.0000020000",
        "reason": None,
    }


def test_an_execution_failure_on_one_side_makes_the_quality_comparison_partial():
    rows = [
        row(1, "a-v1", True),
        row(1, "b-v2", None, "provider_timeout", cost=None),
        row(2, "a-v1", False, "schema_violation"),
        row(2, "b-v2", True),
    ]
    q = build(rows)["comparison"]["quality"]
    assert q["status"] == "partial" and q["higher_pass_rate"] is None
    assert q["unpaired_cases"] == 1 and q["paired_cases"] == 1
    assert "only one prompt version" in q["reason"]


def test_an_unfinished_experiment_is_never_compared():
    doc = build([row(1, "a-v1", True), row(1, "b-v2", True)], finished=False)
    assert doc["experiment"]["state"] == "running" and doc["experiment"]["finished_at"] is None
    assert doc["comparison"]["quality"]["higher_pass_rate"] is None
    assert doc["comparison"]["cost"]["status"] == "incomplete"


def test_case_identity_and_provenance_are_reported():
    doc = build([row(1, "a-v1", True), row(2, "a-v1", True), row(1, "b-v2", True)])
    assert doc["cases"] == {"count": 2, "ids_identical_across_versions": False}
    assert doc["provenance"] == {
        "label": "synthetic",
        "synthetic": 3,
        "real": 0,
        "simulated_provider": True,
    }
    assert doc["evaluation"]["language_model_used"] is False
    assert {lim["code"] for lim in doc["limitations"]} >= {"synthetic_mock", "small_sample"}


def test_money_is_always_a_plain_decimal_string():
    # Phase 4D fix: str(Decimal) uses scientific notation below 1e-6 ("1.500E-7").
    assert report.money(Decimal("0.0000001500")) == "0.0000001500"
    assert report.money(Decimal("1E-10")) == "0.0000000001"
    assert report.money(Decimal("-0.0000030000")) == "-0.0000030000"
    assert report.money(Decimal("9999999999.9999999999")) == "9999999999.9999999999"
    assert report.money(None) is None
