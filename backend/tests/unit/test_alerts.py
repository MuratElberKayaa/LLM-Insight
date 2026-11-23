"""Phase 10F (ADR-59): alert rules, strict parsing and read-time evaluation. SYNTHETIC values."""

import copy
from decimal import Decimal

import pytest

from app.services import alerts

DOC = {
    "version": "llminsight.alert_rules.v1",
    "rules": [
        {"id": "err", "metric": "inference_error_rate", "threshold": "0.05", "min_n": 20},
        {"id": "lat", "metric": "inference_latency_p95_ms", "threshold": "10000", "min_n": 20},
        {"id": "ttfc", "metric": "time_to_first_chunk_p95_ms", "threshold": "2000", "min_n": 20},
        {"id": "cost", "metric": "known_cost_usd", "threshold": "1.00", "min_n": 1},
    ],
}


def inference(
    *, n=40, failed=4, p95=12000.0, ttfc_n=0, ttfc_p95=None, cost="0.5", priced=40, unknown=0
):
    return {
        "errors": {
            "total": {"n": n, "failed": failed, "rate": None if n == 0 else round(failed / n, 6)}
        },
        "latency": {"total": {"n": n, "p95_ms": p95 if n else None}},
        "time_to_first_chunk": {"total": {"n": ttfc_n, "p95_ms": ttfc_p95}},
        "cost": {
            "total": {
                "known_cost_usd": cost,
                "computed_cost_calls": priced,
                "unknown_cost_calls": unknown,
            }
        },
    }


def states(**kw):
    return {s["id"]: s for s in alerts.evaluate(alerts.parse_rules(DOC), inference(**kw))}


def test_the_shipped_rules_load():
    rules = alerts.load_rules()
    assert {r.metric for r in rules} == set(alerts.METRICS)


def test_firing_ok_and_insufficient_data():
    s = states()
    assert s["err"]["state"] == "firing" and s["err"]["value"] == "0.1"  # 4/40 >= 0.05
    assert s["lat"]["state"] == "firing"
    assert s["ttfc"]["state"] == "insufficient_data" and s["ttfc"]["value"] is None
    assert s["cost"]["state"] == "ok" and s["cost"]["lower_bound"] is False


def test_threshold_is_inclusive_and_below_is_ok():
    assert states(failed=2)["err"]["state"] == "firing"  # exactly 0.05
    assert states(failed=1)["err"]["state"] == "ok"


def test_too_few_observations_never_fire():
    s = states(n=19, failed=19)
    assert s["err"]["state"] == "insufficient_data" and s["err"]["n"] == 19


def test_a_partial_cost_below_the_threshold_is_a_lower_bound():
    s = states(cost="0.9", priced=10, unknown=5)
    assert s["cost"]["state"] == "ok" and s["cost"]["lower_bound"] is True
    s = states(cost="1.2", priced=10, unknown=5)
    assert s["cost"]["state"] == "firing" and s["cost"]["lower_bound"] is False


def test_no_priced_call_is_not_zero_cost():
    s = states(cost=None, priced=0, unknown=40)
    assert s["cost"]["state"] == "insufficient_data" and s["cost"]["value"] is None


def test_values_are_exact_strings():
    s = states(ttfc_n=25, ttfc_p95=2000.5)
    assert s["ttfc"]["state"] == "firing" and s["ttfc"]["value"] == "2000.5"
    assert Decimal(s["lat"]["threshold"]) == Decimal("10000")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(version="v0"),
        lambda d: d.update(extra=1),
        lambda d: d.update(rules=[]),
        lambda d: d["rules"][0].update(metric="cpu"),
        lambda d: d["rules"][0].update(threshold=0.05),  # a float
        lambda d: d["rules"][0].update(threshold="NaN"),
        lambda d: d["rules"][0].update(threshold="-1"),
        lambda d: d["rules"][0].update(threshold="1.5"),  # an error rate above 1
        lambda d: d["rules"][0].update(min_n=0),
        lambda d: d["rules"][0].update(min_n=True),
        lambda d: d["rules"][0].update(id="has space"),
        lambda d: d["rules"][1].update(id="err"),
        lambda d: d["rules"][0].pop("min_n"),
    ],
)
def test_malformed_rules_are_refused(mutate):
    doc = copy.deepcopy(DOC)
    mutate(doc)
    with pytest.raises(alerts.RulesError):
        alerts.parse_rules(doc)


def test_an_unreadable_file_is_refused(tmp_path):
    p = tmp_path / "rules.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(alerts.RulesError):
        alerts.load_rules(p)
