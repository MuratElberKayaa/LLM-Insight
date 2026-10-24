"""Usage-ledger derivation: call identity precedence (T23-S2), tokens, unknown cost."""

from datetime import UTC, datetime

import pytest

from app.services.ingestion.normalize import normalize
from app.services.ingestion.usage import canonical_call_key, ns_to_datetime, usage_row
from tests.otlp import FIXTURE_LLM, FIXTURE_TRACE_ID, inference_attrs, request, span


def norm(attrs, **kw):
    spans, violations = normalize(request(span(span_id=FIXTURE_LLM, attrs=attrs, **kw)))
    assert violations == []
    return spans[0]


def row(attrs, **kw):
    return usage_row(7, norm(attrs, **kw))


def test_non_inference_span_has_no_usage_row():
    assert row({"http.route": "/v1/summarize", "gen_ai.usage.input_tokens": 5}) is None
    assert row(inference_attrs(gen_ai__operation__name="execute_tool")) is None


def test_key_precedence_response_id_first():
    r = row(inference_attrs())
    assert r["canonical_call_key"] == "resp:openai:chatcmpl-SYNTHETIC-T1"
    assert r["call_key_source"] == "provider_response_id"


def test_key_precedence_sdk_attempt_without_response_id():
    r = row(inference_attrs(gen_ai__response__id=None, llminsight__attempt=2))
    assert r["canonical_call_key"] == "sdk:5f0c6e1a-3b7d-4c2e-9a41-000000000001:2"
    assert r["call_key_source"] == "sdk_attempt"
    assert r["attempt_id"] == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"gen_ai__response__id": None, "llminsight__call_id": None, "llminsight__attempt": None},
        {"gen_ai__response__id": None, "llminsight__attempt": None},  # call_id alone
        {"gen_ai__response__id": None, "llminsight__call_id": None},  # attempt alone
    ],
)
def test_key_precedence_span_fallback(overrides):
    r = row(inference_attrs(**overrides))
    assert r["canonical_call_key"] == f"span:{FIXTURE_TRACE_ID}:{FIXTURE_LLM}"
    assert r["call_key_source"] == "span_fallback"


def test_missing_provider_is_unknown_and_used_in_the_key():
    r = row(inference_attrs(gen_ai__provider__name=None))
    assert r["provider"] == "unknown"
    assert r["canonical_call_key"] == "resp:unknown:chatcmpl-SYNTHETIC-T1"


def test_response_key_longer_than_300_chars_falls_through_but_id_is_kept():
    long_id = "r" * 256
    s = norm(inference_attrs(gen_ai__response__id=long_id, gen_ai__provider__name="p" * 64))
    key, source = canonical_call_key(s, "p" * 64)
    assert source == "sdk_attempt" and len(key) <= 300
    assert usage_row(1, s)["provider_response_id"] == long_id


def test_complete_usage_is_provider_reported_with_unknown_cost_not_zero():
    r = row(inference_attrs(gen_ai__usage__cache_read__input_tokens=60))
    assert (r["input_tokens"], r["output_tokens"], r["cached_input_tokens"]) == (100, 20, 60)
    assert r["reasoning_output_tokens"] is None and r["cache_write_input_tokens"] is None
    assert r["usage_source"] == "provider_reported"
    assert r["cost_usd"] is None and r["cost_status"] == "unknown"
    assert r["cost_unknown_reason"] == "price_missing"  # no price catalogue in Phase 1B
    assert r["price_version"] is None and r["model_price_id"] is None


@pytest.mark.parametrize("missing", ["gen_ai__usage__input_tokens", "gen_ai__usage__output_tokens"])
def test_missing_tokens_stay_null_with_unknown_source(missing):
    r = row(inference_attrs(**{missing: None}))
    key = missing.removeprefix("gen_ai__usage__")
    assert r[key] is None  # never 0 (T18)
    assert r["usage_source"] == "unknown"
    assert r["cost_unknown_reason"] == "usage_missing"


def test_missing_usage_source_claim_defaults_to_provider_reported_when_tokens_present():
    assert row(inference_attrs(llminsight__usage_source=None))["usage_source"] == (
        "provider_reported"
    )


def test_estimated_claim_is_kept():
    assert row(inference_attrs(llminsight__usage_source="estimated"))["usage_source"] == (
        "estimated"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"gen_ai__usage__cache_read__input_tokens": 101},
        {
            "gen_ai__usage__cache_read__input_tokens": 60,
            "gen_ai__usage__cache_write__input_tokens": 41,
        },
        {"gen_ai__usage__reasoning__output_tokens": 21},
    ],
)
def test_inconsistent_subsets_are_stored_as_received_with_reason(overrides):
    r = row(inference_attrs(**overrides))
    assert r["cost_status"] == "unknown"
    assert r["cost_unknown_reason"] == "usage_inconsistent"


def test_subsets_equal_to_totals_are_consistent():
    r = row(
        inference_attrs(
            gen_ai__usage__cache_read__input_tokens=100,
            gen_ai__usage__reasoning__output_tokens=20,
        )
    )
    assert r["cost_unknown_reason"] == "price_missing"


def test_usage_copies_span_identity_models_and_provenance():
    r = row(inference_attrs(), start=1_792_836_000_015_000_123)
    assert (r["trace_id"], r["span_id"]) == (FIXTURE_TRACE_ID, FIXTURE_LLM)
    assert r["requested_model"] == "gpt-4o-mini"
    assert r["response_model"] == "gpt-4o-mini-2024-07-18"  # never merged with the request
    assert r["operation_name"] == "chat" and r["provenance"] == "synthetic"
    assert r["started_at"] == datetime(2026, 10, 24, 10, 0, 0, 15000, tzinfo=UTC)


def test_ns_to_datetime_is_exact_and_truncates_to_microseconds():
    assert ns_to_datetime(1) == datetime(1970, 1, 1, tzinfo=UTC)
    assert ns_to_datetime(1_792_836_001_240_000_999) == datetime(
        2026, 10, 24, 10, 0, 1, 240000, tzinfo=UTC
    )
    assert ns_to_datetime(2**63 - 1).year == 2262
