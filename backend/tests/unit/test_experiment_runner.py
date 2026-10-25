"""Mock A/B runner (Phase 4C, B4): identical case ids, determinism, single attempts, budget
pre-check and running guard, exact Decimal cost, unknown cost never 0, synthetic provenance."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services import pricing
from app.services.experiments import golden, runner
from app.services.experiments.golden import Scripted
from app.services.experiments.mock_provider import GOLDEN_PROVIDER, ScriptedMockProvider
from tests.unit.test_golden_dataset import VERSIONS, records

AT = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
MODEL = "synthetic-golden-model"
PRICE = pricing.Price(
    id=1,
    version="synthetic-price-v1",
    provider="openai",
    model=MODEL,
    effective_from=AT - timedelta(days=1),
    input_per_million=Decimal("0.1500000000"),
    output_per_million=Decimal("0.6000000000"),
    cached_input_per_million=None,
    cache_write_input_per_million=None,
)


@dataclass
class CountingProvider:
    inner: ScriptedMockProvider
    calls: list[tuple[str, str]] = field(default_factory=list)
    estimates: int = 0

    def call(self, *, model, prompt_version, sanitized_input):
        self.calls.append((prompt_version, sanitized_input))
        return self.inner.call(
            model=model, prompt_version=prompt_version, sanitized_input=sanitized_input
        )

    def estimate(self, *, model, prompt_version, sanitized_input):
        self.estimates += 1
        return self.inner.estimate(
            model=model, prompt_version=prompt_version, sanitized_input=sanitized_input
        )


def run(provider=GOLDEN_PROVIDER, prices=(PRICE,), budget=None, cases=None):
    return runner.run(cases or records(), VERSIONS, MODEL, provider, list(prices), AT, budget)


def test_t38_both_versions_run_on_identical_case_ids_in_order():
    results = run()
    by_version = {v: [r.test_case_id for r in results if r.prompt_version == v] for v in VERSIONS}
    assert by_version[VERSIONS[0]] == by_version[VERSIONS[1]] == list(range(1, 13))
    assert [r.prompt_version for r in results[:2]] == list(VERSIONS)  # A then B per case


def test_t39_every_mock_result_is_synthetic():
    assert {r.provenance for r in run()} == {"synthetic"}


def test_runs_are_deterministic():
    assert run() == run() == run()


def test_exactly_one_attempt_per_case_and_version_no_retries():
    provider = CountingProvider(GOLDEN_PROVIDER)
    run(provider=provider)
    assert len(provider.calls) == 2 * len(golden.CASES)
    assert len(set(provider.calls)) == len(provider.calls)
    assert provider.estimates == 0  # no budget: no pre-check


def test_cost_is_exact_decimal_and_unknown_is_never_zero():
    results = run()
    g01 = next(r for r in results if r.test_case_id == 1 and r.prompt_version == VERSIONS[0])
    # 52 input and 18 output tokens: (52 * 0.15 + 18 * 0.60) / 1e6, exactly
    assert g01.cost.cost_usd == Decimal("0.0000186000")
    assert g01.cost.cost_status == "computed" and g01.cost.price_version == "synthetic-price-v1"
    timeout = next(r for r in results if r.test_case_id == 12 and r.prompt_version == VERSIONS[1])
    assert timeout.cost.cost_usd is None
    assert (timeout.cost.cost_status, timeout.cost.cost_unknown_reason) == (
        "unknown",
        "usage_missing",
    )
    assert timeout.input_tokens is None and timeout.duration_ms is None
    unpriced = run(prices=())
    assert {r.cost.cost_unknown_reason for r in unpriced} == {"price_missing", "usage_missing"}
    assert all(r.cost.cost_usd is None for r in unpriced)


def test_price_in_force_at_the_experiment_start_is_used():
    later = pricing.Price(
        **{
            **PRICE.__dict__,
            "id": 2,
            "version": "later",
            "effective_from": AT + timedelta(hours=1),
            "input_per_million": Decimal("9"),
        }
    )  # noqa: E501
    results = run(prices=(PRICE, later))
    assert {r.cost.price_version for r in results if r.cost.cost_usd is not None} == {
        "synthetic-price-v1"
    }


def test_unknown_inputs_get_no_invented_answer():
    cases = [
        runner.CaseRecord(
            1,
            "SYNTHETIC: not in the script",
            "x",
            {"version": 1, "type": "exact", "output_format": "text"},
        )
    ]  # noqa: E501
    results = run(cases=cases)
    assert [(r.passed, r.code) for r in results] == [(None, "mock_no_scripted_output")] * 2
    assert all(r.evaluation_detail.startswith("mock_no_scripted_output: ") for r in results)


def test_budget_is_checked_before_anything_runs():
    provider = CountingProvider(GOLDEN_PROVIDER)
    with pytest.raises(runner.BudgetRefused) as info:
        run(provider=provider, budget=Decimal("1"))  # G12 v2 has no usage: unknown estimate
    assert info.value.code == "budget_unverifiable"
    assert provider.calls == []  # refused before any call

    priced_only = [c for c in records() if c.id != 12]
    total = runner.check_budget(
        priced_only, VERSIONS, MODEL, GOLDEN_PROVIDER, [PRICE], AT, Decimal(1)
    )
    provider = CountingProvider(GOLDEN_PROVIDER)
    with pytest.raises(runner.BudgetRefused) as info:
        run(provider=provider, cases=priced_only, budget=total - Decimal("0.0000000001"))
    assert info.value.code == "budget_exceeded" and provider.calls == []
    results = run(cases=priced_only, budget=total)  # exactly the estimate: allowed
    assert sum(r.cost.cost_usd for r in results) == total


def test_running_guard_stops_calls_when_actual_cost_exceeds_the_budget():
    """A provider whose real answers cost more than its estimates (never true for the mock)."""

    class Underestimating(ScriptedMockProvider):
        def estimate(self, *, model, prompt_version, sanitized_input):
            return Scripted("x", 1, 1, 1)

        def call(self, *, model, prompt_version, sanitized_input):
            return Scripted('{"title": "x", "priority": "low"}', 100_000, 100_000, 5)

    cases = [c for c in records() if c.id <= 3]
    results = run(provider=Underestimating(golden.SCRIPT), cases=cases, budget=Decimal("0.05"))
    executed = [r for r in results if r.code != runner.BUDGET_EXHAUSTED]
    stopped = [r for r in results if r.code == runner.BUDGET_EXHAUSTED]
    assert len(executed) == 1 and len(stopped) == 5  # 0.075 USD > 0.05 after the first call
    assert all(r.passed is None and r.cost.cost_unknown_reason == "usage_missing" for r in stopped)
