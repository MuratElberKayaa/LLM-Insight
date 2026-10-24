"""Cost engine (app.services.pricing): T17, T19, T20, T21 and the selection/applicability
rules. Every expected cost below is computed by hand in the comment next to it.

Prices named `synthetic-*` are SYNTHETIC fixtures for arithmetic only; the `openai` rows are
the verified catalogue values (app.services.pricing_catalog, docs/PRICING.md)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services import pricing, pricing_catalog
from app.services.pricing import CostOutcome, Price, Tokens

AT = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
OPENAI_STANDARD = {"openai.response.service_tier": "default", "server.address": "api.openai.com"}


def _verified(model: str, pid: int = 1) -> Price:
    e = next(e for e in pricing_catalog.ENTRIES if e.model == model)
    return Price(
        id=pid,
        version=e.version,
        provider=e.provider,
        model=e.model,
        effective_from=e.effective_from,
        input_per_million=e.input_per_million,
        output_per_million=e.output_per_million,
        cached_input_per_million=e.cached_input_per_million,
        cache_write_input_per_million=e.cache_write_input_per_million,
    )


def _synthetic(**kw) -> Price:
    base = dict(
        id=99,
        version="synthetic-v1",
        provider="synthetic.test",
        model="synthetic-model",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        cached_input_per_million=Decimal("0.5"),
        cache_write_input_per_million=Decimal("1.25"),
    )
    return Price(**(base | kw))


def tokens(i=None, o=None, cr=None, cw=None, r=None) -> Tokens:
    return Tokens(input=i, output=o, cache_read=cr, cache_write=cw, reasoning=r)


def assess(
    t: Tokens,
    prices,
    *,
    provider="openai",
    req=None,
    resp=None,
    attrs=None,
    at=AT,
    source="provider_reported",
) -> CostOutcome:
    return pricing.assess(
        tokens=t,
        usage_source=source,
        provider=provider,
        requested_model=req,
        response_model=resp,
        attributes=OPENAI_STANDARD if attrs is None else attrs,
        started_at=at,
        prices=prices,
    )


# ---------------------------------------------------------------- T19: exact Decimal cost
def test_t19_hand_computed_cost_gpt_5_4_mini_snapshot():
    # uncached 200 * 0.75 = 150; cached 1000 * 0.075 = 75; output 300 * 4.50 = 1350
    # (150 + 75 + 1350) / 1e6 = 0.001575
    price = _verified("gpt-5.4-mini-2026-03-17", pid=7)
    out = assess(
        tokens(1200, 300, 1000, 0, 200), [price], req="gpt-5.4-mini", resp="gpt-5.4-mini-2026-03-17"
    )
    assert out == CostOutcome(Decimal("0.0015750000"), "computed", None, "openai-2026-10-24", 7)
    assert isinstance(out.cost_usd, Decimal)
    assert out.cost_usd.as_tuple().exponent == -10  # NUMERIC(20,10) scale


def test_t19_hand_computed_cost_gpt_5_mini():
    # uncached 904 * 0.25 = 226; cached 4096 * 0.025 = 102.4; output 1500 * 2.00 = 3000
    # 3328.4 / 1e6 = 0.0033284
    out = assess(tokens(5000, 1500, 4096, None, 1024), [_verified("gpt-5-mini")], resp="gpt-5-mini")
    assert out.cost_usd == Decimal("0.0033284000")


def test_t19_hand_computed_cost_gpt_4_1_mini_without_subsets():
    # 1_000_000 * 0.40 + 250_000 * 1.60 = 400_000 + 400_000 -> 0.8
    out = assess(tokens(1_000_000, 250_000), [_verified("gpt-4.1-mini")], resp="gpt-4.1-mini")
    assert out.cost_usd == Decimal("0.8000000000")


def test_cost_is_exact_where_float_arithmetic_is_not():
    # 0.1 + 0.2 style: 3 tokens at 0.1/M and 3 at 0.2/M -> 0.0000009 exactly
    price = _synthetic(input_per_million=Decimal("0.1"), output_per_million=Decimal("0.2"))
    out = assess(tokens(3, 3), [price], provider="synthetic.test", resp="synthetic-model")
    assert out.cost_usd == Decimal("0.0000009000")
    assert 3 * 0.1 / 1e6 + 3 * 0.2 / 1e6 != 0.0000009  # what a float would have produced


# ---------------------------------------------------------------- T20 / T21: subsets once
def test_t20_cached_tokens_are_priced_once_and_not_added_to_input():
    price = _synthetic()
    # uncached (1000 - 600) * 1 = 400; cached 600 * 0.5 = 300; output 0 -> 700 / 1e6
    out = assess(tokens(1000, 0, 600), [price], provider="synthetic.test", resp="synthetic-model")
    assert out.cost_usd == Decimal("0.0007000000")
    double_counted = (1000 * 1 + 600 * Decimal("0.5")) / 1_000_000
    assert out.cost_usd != double_counted


def test_cache_write_tokens_are_a_priced_subset_of_input():
    price = _synthetic()
    # uncached (1000 - 200 - 300) * 1 = 500; read 200 * 0.5 = 100; write 300 * 1.25 = 375
    out = assess(
        tokens(1000, 0, 200, 300), [price], provider="synthetic.test", resp="synthetic-model"
    )
    assert out.cost_usd == Decimal("0.0009750000")


def test_t21_reasoning_tokens_are_part_of_output_and_not_added_again():
    price = _synthetic()
    # output 500 (of which 400 reasoning) * 2 = 1000 -> 0.001; never (500 + 400) * 2
    out = assess(
        tokens(0, 500, None, None, 400), [price], provider="synthetic.test", resp="synthetic-model"
    )
    assert out.cost_usd == Decimal("0.0010000000")


def test_absent_subsets_count_as_zero_only_inside_the_formula():
    price = _synthetic(cached_input_per_million=None, cache_write_input_per_million=None)
    out = assess(tokens(10, 10), [price], provider="synthetic.test", resp="synthetic-model")
    assert out.cost_usd == Decimal("0.0000300000")  # 10 * 1 + 10 * 2


def test_zero_reported_usage_has_a_known_zero_cost():
    out = assess(tokens(0, 0, 0, 0, 0), [_verified("gpt-5-mini")], resp="gpt-5-mini")
    assert (out.cost_status, out.cost_usd) == ("computed", Decimal("0E-10"))


# ---------------------------------------------------------------- rounding policy
@pytest.mark.parametrize(
    "count,per_million,expected",
    [
        (1, "0.00025", "0.0000000002"),  # 2.5e-10 -> half-even down
        (1, "0.00035", "0.0000000004"),  # 3.5e-10 -> half-even up
        (3, "0.00005", "0.0000000002"),  # 1.5e-10 -> up to even
        (1, "0.00005", "0.0000000000"),  # 0.5e-10 -> to even (0); stays computed
        (1, "0.00026", "0.0000000003"),  # 2.6e-10 -> nearest
    ],
)
def test_single_half_even_rounding_to_ten_places(count, per_million, expected):
    price = _synthetic(input_per_million=Decimal(per_million))
    out = assess(tokens(count, 0), [price], provider="synthetic.test", resp="synthetic-model")
    assert out.cost_usd == Decimal(expected)
    assert format(out.cost_usd, "f") == expected  # how the read API renders money


def test_rounding_happens_once_on_the_exact_sum_not_per_term():
    # two terms of 0.6e-10 each: per-term rounding would give 2e-10, the exact sum 1.2e-10
    price = _synthetic(input_per_million=Decimal("0.00006"), output_per_million=Decimal("0.00006"))
    out = assess(tokens(1, 1), [price], provider="synthetic.test", resp="synthetic-model")
    assert out.cost_usd == Decimal("0.0000000001")


def test_a_cost_that_does_not_fit_numeric_20_10_is_unknown_not_an_error():
    price = _synthetic(input_per_million=Decimal("1000"))
    out = assess(tokens(2**62, 0), [price], provider="synthetic.test", resp="synthetic-model")
    assert out == pricing.unknown("usage_inconsistent")


def test_largest_plausible_bigint_counts_stay_exact():
    price = _synthetic(input_per_million=Decimal("0.0000000001"))
    out = assess(tokens(2**63 - 1, 0), [price], provider="synthetic.test", resp="synthetic-model")
    # (2^63 - 1) * 1e-10 / 1e6 = 9223372036854775807e-16 -> 922.3372036855 (half-even)
    assert out.cost_usd == Decimal("922.3372036855")


@pytest.mark.parametrize("value", [0.25, 1, "0.25", None, Decimal("NaN"), Decimal("-0.1")])
def test_prices_must_be_finite_non_negative_decimals(value):
    with pytest.raises(TypeError, match="Decimal"):
        _synthetic(input_per_million=value)


def test_optional_prices_reject_floats_too():
    with pytest.raises(TypeError):
        _synthetic(cached_input_per_million=0.5)


# ---------------------------------------------------------------- T17 / T18: usage rules
@pytest.mark.parametrize(
    "t,source,reason",
    [
        (tokens(None, 5), "unknown", "usage_missing"),
        (tokens(5, None), "unknown", "usage_missing"),
        (tokens(10, 5), "estimated", "usage_missing"),  # T17: estimates are never priced
        (tokens(10, 5), "unknown", "usage_missing"),
        (tokens(10, 5, 8, 3), "provider_reported", "usage_inconsistent"),  # 8 + 3 > 10
        (tokens(10, 5, 11), "provider_reported", "usage_inconsistent"),
        (tokens(10, 5, None, None, 6), "provider_reported", "usage_inconsistent"),
        (tokens(None, None, 11), "unknown", "usage_missing"),  # no total to compare with
        (tokens(-1, 5), "provider_reported", "usage_inconsistent"),
        (tokens(True, 5), "provider_reported", "usage_inconsistent"),
    ],
)
def test_usage_rules_decide_before_any_price(t, source, reason):
    out = assess(t, [_verified("gpt-5-mini")], resp="gpt-5-mini", source=source)
    assert out == pricing.unknown(reason)
    assert out.cost_usd is None and out.price_version is None and out.model_price_id is None


def test_inconsistency_wins_over_missing_usage():
    out = assess(
        tokens(10, None, 11), [_verified("gpt-5-mini")], resp="gpt-5-mini", source="unknown"
    )
    assert out.cost_unknown_reason == "usage_inconsistent"


def test_subset_exactly_equal_to_total_is_consistent():
    out = assess(tokens(10, 5, 10, 0, 5), [_verified("gpt-5-mini")], resp="gpt-5-mini")
    # 10 * 0.025 + 5 * 2 = 10.25 -> 0.00001025
    assert out.cost_usd == Decimal("0.0000102500")


# ---------------------------------------------------------------- price_missing cases
def test_non_zero_term_without_a_price_is_price_missing_never_full_rate():
    price = _synthetic(cached_input_per_million=None)
    out = assess(tokens(100, 10, 1), [price], provider="synthetic.test", resp="synthetic-model")
    assert out == pricing.unknown("price_missing")


def test_cache_write_tokens_on_a_model_without_cache_write_price_are_price_missing():
    out = assess(tokens(100, 10, 0, 5), [_verified("gpt-5-mini")], resp="gpt-5-mini")
    assert out == pricing.unknown("price_missing")


def test_no_price_rows_is_price_missing():
    assert assess(tokens(10, 5), [], resp="gpt-5-mini") == pricing.unknown("price_missing")


def test_no_model_at_all_is_price_missing():
    out = assess(tokens(10, 5), [_verified("gpt-5-mini")], req=None, resp=None)
    assert out == pricing.unknown("price_missing")


# ---------------------------------------------------------------- exact model matching
CATALOGUE = [_verified(e.model, pid=i) for i, e in enumerate(pricing_catalog.ENTRIES, 1)]


def test_response_model_is_matched_exactly():
    out = assess(tokens(10, 5), CATALOGUE, req="gpt-5-mini", resp="gpt-5-mini-2025-08-07")
    assert out.model_price_id == next(p.id for p in CATALOGUE if p.model == "gpt-5-mini-2025-08-07")


def test_unknown_snapshot_is_not_priced_at_its_alias_rate():
    # The request names a priced alias, but the response names a snapshot with no evidence.
    out = assess(tokens(10, 5), CATALOGUE, req="gpt-5-mini", resp="gpt-5-mini-2099-01-01")
    assert out == pricing.unknown("price_missing")


def test_requested_model_is_used_only_when_no_response_model_exists():
    out = assess(tokens(10, 5), CATALOGUE, req="gpt-4.1-mini", resp=None)
    assert out.cost_status == "computed"
    assert out.model_price_id == next(p.id for p in CATALOGUE if p.model == "gpt-4.1-mini")


@pytest.mark.parametrize(
    "provider,model",
    [
        ("openai", "GPT-5-MINI"),  # case matters
        ("openai", "gpt-5-mini "),
        ("openai", "gpt-5"),  # a prefix of nothing, and not a fuzzy match
        ("openai", "gpt-5-mini-high"),
        ("OpenAI", "gpt-5-mini"),  # provider namespaces are exact (semconv: `openai`)
        ("azure.ai.openai", "gpt-5-mini"),
        ("openai", "ft:gpt-5-mini:org:custom:id1"),  # fine-tunes are not base-priced
    ],
)
def test_no_alias_prefix_case_or_fuzzy_matching(provider, model):
    out = assess(tokens(10, 5), CATALOGUE, provider=provider, resp=model)
    assert out == pricing.unknown("price_missing")


# ---------------------------------------------------------------- event-time selection
V1 = _synthetic(id=1, version="synthetic-v1", effective_from=datetime(2026, 1, 1, tzinfo=UTC))
V2 = _synthetic(
    id=2,
    version="synthetic-v2",
    effective_from=datetime(2026, 6, 1, tzinfo=UTC),
    input_per_million=Decimal("3"),
)


@pytest.mark.parametrize(
    "at,expected",
    [
        (datetime(2025, 12, 31, 23, 59, 59, 999999, tzinfo=UTC), None),
        (datetime(2026, 1, 1, tzinfo=UTC), "synthetic-v1"),  # effective_from is inclusive
        (datetime(2026, 5, 31, 23, 59, 59, 999999, tzinfo=UTC), "synthetic-v1"),
        (datetime(2026, 6, 1, tzinfo=UTC), "synthetic-v2"),  # v2 closes v1's period
        (datetime(2030, 1, 1, tzinfo=UTC), "synthetic-v2"),
    ],
)
def test_price_in_force_at_event_time(at, expected):
    for rows in ([V1, V2], [V2, V1]):  # order of the loaded rows does not matter
        chosen = pricing.select_price(rows, "synthetic.test", "synthetic-model", at)
        assert (chosen.version if chosen else None) == expected


def test_event_time_decides_the_cost_not_the_ingest_time():
    old = assess(
        tokens(1000, 0),
        [V1, V2],
        provider="synthetic.test",
        resp="synthetic-model",
        at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    new = assess(
        tokens(1000, 0),
        [V1, V2],
        provider="synthetic.test",
        resp="synthetic-model",
        at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert (old.cost_usd, old.price_version, old.model_price_id) == (
        Decimal("0.0010000000"),
        "synthetic-v1",
        1,
    )
    assert (new.cost_usd, new.price_version, new.model_price_id) == (
        Decimal("0.0030000000"),
        "synthetic-v2",
        2,
    )


def test_event_before_the_verified_catalogue_is_price_missing():
    before = pricing_catalog.EFFECTIVE_FROM - timedelta(microseconds=1)
    out = assess(tokens(10, 5), CATALOGUE, resp="gpt-5-mini", at=before)
    assert out == pricing.unknown("price_missing")


# ---------------------------------------------------------------- applicability (tiers)
@pytest.mark.parametrize(
    "attrs",
    [
        {},  # tier not reported: standard pricing cannot be shown
        {"server.address": "api.openai.com"},
        {"openai.response.service_tier": "default"},  # endpoint unknown
        {**OPENAI_STANDARD, "openai.response.service_tier": "flex"},
        {**OPENAI_STANDARD, "openai.response.service_tier": "priority"},  # Fast mode
        {**OPENAI_STANDARD, "openai.response.service_tier": "scale"},
        {**OPENAI_STANDARD, "openai.response.service_tier": "auto"},
        {**OPENAI_STANDARD, "server.address": "eu.api.openai.com"},  # regional uplift
        {**OPENAI_STANDARD, "server.address": "us.api.openai.com"},
        {**OPENAI_STANDARD, "server.address": "localhost"},
    ],
)
def test_openai_price_applies_only_to_the_standard_tier_on_the_global_endpoint(attrs):
    out = assess(tokens(10, 5), CATALOGUE, resp="gpt-5-mini", attrs=attrs)
    assert out == pricing.unknown("price_missing")


def test_request_tier_alone_does_not_prove_the_served_tier():
    attrs = {"openai.request.service_tier": "default", "server.address": "api.openai.com"}
    assert assess(tokens(10, 5), CATALOGUE, resp="gpt-5-mini", attrs=attrs).cost_status == (
        "unknown"
    )


def test_providers_without_billing_modes_need_no_tier():
    out = assess(
        tokens(1, 1), [_synthetic()], provider="synthetic.test", resp="synthetic-model", attrs={}
    )
    assert out.cost_status == "computed"


def test_compute_cost_refuses_unvalidated_usage():
    with pytest.raises(ValueError):
        pricing.compute_cost(tokens(None, 1), _synthetic())
    with pytest.raises(ValueError):
        pricing.compute_cost(tokens(1, 1, 5), _synthetic())


def test_outcome_columns_satisfy_the_table_checks():
    computed = assess(tokens(10, 5), CATALOGUE, resp="gpt-5-mini").as_columns()
    assert computed["cost_usd"] is not None and computed["cost_unknown_reason"] is None
    assert computed["price_version"] and computed["model_price_id"]
    missing = pricing.unknown("price_missing").as_columns()
    assert missing == {
        "cost_usd": None,
        "cost_status": "unknown",
        "cost_unknown_reason": "price_missing",
        "price_version": None,
        "model_price_id": None,
    }


def test_price_rows_are_frozen():
    with pytest.raises(AttributeError):
        V1.input_per_million = Decimal("9")  # type: ignore[misc]
    assert replace(V1, id=3).input_per_million == V1.input_per_million
