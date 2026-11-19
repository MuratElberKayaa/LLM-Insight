"""Phase 9B: list-price reference (ADR-52, PRICING.md §9).

The reference is computed at read time, outside the cost ledger. These tests pin the verified
catalogue, the equation (shared with the ledger engine), the exact model match, the reasons a
call is not priced, and that "no priced call" is never shown as a zero amount.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from app.services import reference_pricing as ref
from app.services import reference_pricing_catalog as cat

NOW = cat.VERIFIED_AT + timedelta(days=1)
GEMINI, GROQ = "gemini-3.1-flash-lite", "openai/gpt-oss-20b"


def usage(**kw) -> ref.GroupUsage:
    base = {
        "calls": 1,
        "eligible_calls": 1,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "usage_missing": 0,
        "usage_inconsistent": 0,
        "cache_write_calls": 0,
    }
    base.update(kw)
    return ref.GroupUsage(**base)


def block(provider: str, model: str | None, u: ref.GroupUsage, **kw) -> dict:
    return ref.reference_block(provider, model, model, u, now=kw.pop("now", NOW), **kw)


# ---------------------------------------------------------------- the verified catalogue
def test_catalogue_is_the_verified_evidence_and_nothing_else():
    assert cat.VERSION == "reference-2026-11-19"
    assert datetime(2026, 11, 19, 12, 51, 42, tzinfo=UTC) == cat.VERIFIED_AT
    rows = {(e.provider, e.model): e for e in cat.ENTRIES}
    assert set(rows) == {("gcp.gemini", GEMINI), ("groq", GROQ)}
    gemini, groq = rows[("gcp.gemini", GEMINI)], rows[("groq", GROQ)]
    assert (
        gemini.input_per_million,
        gemini.cached_input_per_million,
        gemini.output_per_million,
    ) == (
        Decimal("0.25"),
        Decimal("0.025"),
        Decimal("1.50"),
    )
    assert (groq.input_per_million, groq.cached_input_per_million, groq.output_per_million) == (
        Decimal("0.075"),
        Decimal("0.037"),
        Decimal("0.30"),
    )
    assert (gemini.tier, groq.tier) == ("paid_standard", "on_demand")
    assert gemini.source_url == "https://aistudio.google.com/docs/pricing"
    assert groq.source_url == "https://console.groq.com/docs/model/openai/gpt-oss-20b"


@pytest.mark.parametrize("entry", cat.ENTRIES, ids=lambda e: e.provider)
def test_every_row_is_decimal_utc_usd_and_from_an_official_host(entry):
    for name in ("input_per_million", "output_per_million", "cached_input_per_million"):
        assert type(getattr(entry, name)) is Decimal
    assert entry.currency == "USD" and entry.version == cat.VERSION
    assert entry.verified_at.utcoffset() == timedelta(0)
    url = urlparse(entry.source_url)
    assert url.scheme == "https" and url.hostname in {"aistudio.google.com", "console.groq.com"}
    assert entry.basis


def test_ledger_providers_are_never_in_the_reference_catalogue():
    # OpenAI has ledger prices (ADR-11); OpenRouter's only allowed model is `:free` (price 0).
    assert not {e.provider for e in cat.ENTRIES} & {"openai", "openrouter"}
    assert len({(e.provider, e.model) for e in cat.ENTRIES}) == len(cat.ENTRIES)


def test_a_float_price_is_rejected():
    with pytest.raises(TypeError):
        replace(cat.ENTRIES[0], input_per_million=0.25)
    with pytest.raises(TypeError):
        replace(cat.ENTRIES[0], cached_input_per_million=-Decimal("0.1"))


# ---------------------------------------------------------------- the equation
def test_gemini_worked_example_prices_cached_once_and_thinking_inside_output():
    # (1000 · 0.25 + 200 · 0.025 + 300 · 1.50) / 1e6 = 705 / 1e6
    b = block(
        "gcp.gemini", GEMINI, usage(input_tokens=1200, cached_input_tokens=200, output_tokens=300)
    )
    assert b["available"] is True and b["unavailable_reason"] is None
    assert b["reference_usd"] == "0.0007050000"
    assert (b["calls_priced"], b["calls_not_priced"]) == (1, 0)


def test_groq_example_and_the_sum_of_rows_equals_the_summed_tokens():
    one = block("groq", GROQ, usage(input_tokens=222, output_tokens=96))
    two = block("groq", GROQ, usage(input_tokens=244, output_tokens=363))
    both = block(
        "groq", GROQ, usage(calls=2, eligible_calls=2, input_tokens=466, output_tokens=459)
    )
    assert (one["reference_usd"], two["reference_usd"]) == ("0.0000454500", "0.0001272000")
    assert Decimal(one["reference_usd"]) + Decimal(two["reference_usd"]) == Decimal(
        both["reference_usd"]
    )


def test_real_zero_counts_are_a_known_zero():
    assert block("groq", GROQ, usage())["reference_usd"] == "0.0000000000"


def test_the_metadata_names_version_tier_date_source_and_label():
    b = block("gcp.gemini", GEMINI, usage(input_tokens=1, output_tokens=1))
    assert b["label"] == "reference_not_billed" and b["currency"] == "USD"
    assert b["tier"] == "paid_standard" and b["catalogue_version"] == cat.VERSION
    assert b["verified_at"] == "2026-11-19T12:51:42.000000Z"
    assert b["source_url"] == "https://aistudio.google.com/docs/pricing"
    assert b["stale"] is False


# ---------------------------------------------------------------- what is not priced
def test_a_model_outside_the_catalogue_is_unavailable_and_counted():
    b = block("openai", "gpt-5.4-mini", usage(calls=3, eligible_calls=2, usage_missing=1))
    assert b["available"] is False and b["unavailable_reason"] == "not_in_reference_catalogue"
    assert b["reference_usd"] is None
    assert b["not_priced_by_reason"] == {
        "usage_missing": 1,
        "usage_inconsistent": 0,
        "cache_write_not_representable": 0,
        "not_in_reference_catalogue": 2,
        "price_not_listed": 0,
    }
    assert (b["calls_priced"], b["calls_not_priced"]) == (0, 3)
    for key in ("currency", "tier", "catalogue_version", "verified_at", "source_url", "stale"):
        assert b[key] is None


@pytest.mark.parametrize(
    ("provider", "requested", "response"),
    [
        ("groq", GROQ, "openai/gpt-oss-20b-0901"),  # a different served model: no fuzzy match
        ("groq", "OPENAI/GPT-OSS-20B", None),  # no case folding
        ("gcp.gemini", GROQ, GROQ),  # the provider is part of the identity
        ("groq", None, None),
    ],
)
def test_the_model_must_match_exactly(provider, requested, response):
    b = ref.reference_block(provider, requested, response, usage(input_tokens=5), now=NOW)
    assert b["unavailable_reason"] == "not_in_reference_catalogue"


def test_the_requested_model_is_used_only_without_a_response_model():
    b = ref.reference_block("groq", GROQ, None, usage(input_tokens=10, output_tokens=10), now=NOW)
    assert b["available"] is True
    b = ref.reference_block(
        "groq", "other", GROQ, usage(input_tokens=10, output_tokens=10), now=NOW
    )
    assert b["available"] is True


def test_no_priced_call_is_never_shown_as_zero():
    u = usage(calls=3, eligible_calls=0, usage_missing=1, usage_inconsistent=1, cache_write_calls=1)
    b = block("groq", GROQ, u)
    assert b["available"] is False and b["unavailable_reason"] == "no_priced_calls"
    assert b["reference_usd"] is None
    assert b["not_priced_by_reason"]["usage_missing"] == 1
    assert b["not_priced_by_reason"]["usage_inconsistent"] == 1
    assert b["not_priced_by_reason"]["cache_write_not_representable"] == 1
    assert (b["calls_priced"], b["calls_not_priced"]) == (0, 3)


def test_ineligible_calls_are_excluded_but_the_rest_is_priced():
    u = usage(
        calls=4,
        eligible_calls=2,
        input_tokens=100,
        output_tokens=10,
        usage_missing=1,
        cache_write_calls=1,
    )
    b = block("groq", GROQ, u)
    assert b["available"] is True and (b["calls_priced"], b["calls_not_priced"]) == (2, 2)
    assert sum(b["not_priced_by_reason"].values()) == b["calls_not_priced"]


def test_cached_tokens_without_a_cached_price_are_never_charged_at_the_input_rate():
    no_cache = tuple(replace(e, cached_input_per_million=None) for e in cat.ENTRIES)
    u = usage(calls=2, eligible_calls=2, input_tokens=100, cached_input_tokens=40, output_tokens=5)
    b = block("groq", GROQ, u, catalogue=no_cache)
    assert b["available"] is False and b["unavailable_reason"] == "price_not_listed"
    assert b["reference_usd"] is None and b["not_priced_by_reason"]["price_not_listed"] == 2
    # Without cached tokens the same catalogue prices the call.
    assert block("groq", GROQ, usage(input_tokens=100), catalogue=no_cache)["available"] is True


@pytest.mark.parametrize(
    ("u", "message"),
    [
        (dict(calls=1, eligible_calls=2), "eligible"),
        (dict(eligible_calls=1, input_tokens=10, cached_input_tokens=11), "cached"),
        (dict(calls=1, eligible_calls=1, usage_missing=1), "counts"),
        (dict(input_tokens=-1), "negative"),
    ],
)
def test_impossible_group_counts_are_refused(u, message):
    with pytest.raises(ValueError):
        block("groq", GROQ, usage(**u))


# ---------------------------------------------------------------- staleness
def test_stale_after_thirty_days_but_still_computed():
    edge = cat.VERIFIED_AT + cat.STALE_AFTER
    assert timedelta(days=30) == cat.STALE_AFTER
    assert block("groq", GROQ, usage(input_tokens=1), now=edge)["stale"] is False
    late = block("groq", GROQ, usage(input_tokens=1), now=edge + timedelta(seconds=1))
    assert late["stale"] is True and late["available"] is True


def test_now_must_be_timezone_aware():
    with pytest.raises(ValueError):
        block("groq", GROQ, usage(), now=datetime(2026, 12, 1))
