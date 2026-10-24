"""Allowlist, always-deny list, alias precedence and value rules (TELEMETRY_CONTRACT.md §3)."""

import pytest

from app.services.ingestion import attrmap
from tests.otlp import kvs


def span_map(attrs):
    return attrmap.map_span(kvs(attrs))


def test_allowlisted_attributes_are_stored_and_unknown_ones_dropped_and_counted():
    m = span_map({"gen_ai.request.model": "gpt-4o-mini", "custom.thing": "x", "host.name": "h"})
    assert m.values == {"gen_ai.request.model": "gpt-4o-mini"}
    assert m.dropped == 2


@pytest.mark.parametrize(
    "key",
    [
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.definitions",
        "gen_ai.prompt.variable",
        "gen_ai.prompt.variable.customer_name",
        "gen_ai.conversation.id",
        "url.full",
        "url.query",
        "http.request.header.authorization",
        "http.response.header.set-cookie",
        "client.address",
        "user.email",
        "enduser.id",
        "exception.message",
        "exception.stacktrace",
        "Authorization",
        "openai.api_key",
        "x.apikey",
        "db.password",
        "my.secret",
        "auth.token",
        "llminsight.session_token",
    ],
)
def test_always_denied_keys_are_never_stored(key):
    assert attrmap.is_denied(key)
    m = span_map({key: "SENSITIVE-VALUE"})
    assert m.values == {} and m.dropped == 1


def test_deny_list_overrides_a_misconfigured_allowlist(monkeypatch):
    broken = dict(attrmap.SPAN_ALLOWLIST)
    broken["gen_ai.input.messages"] = attrmap.STR
    broken["http.request.header.authorization"] = attrmap.STR
    monkeypatch.setattr(attrmap, "SPAN_ALLOWLIST", broken)
    m = attrmap.map_attributes(
        kvs({"gen_ai.input.messages": "hi", "http.request.header.authorization": "Bearer x"}),
        broken,
    )
    assert m.values == {} and m.dropped == 2


def test_explicit_usage_token_keys_are_exempt_from_the_token_substring_rule():
    names = [
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.reasoning.output_tokens",
        "gen_ai.usage.prompt_tokens",
        "gen_ai.usage.completion_tokens",
    ]
    assert not any(attrmap.is_denied(n) for n in names)


def test_request_max_tokens_follows_the_literal_deny_rule():
    """Contract conflict (reported): §3 allowlists gen_ai.request.max_tokens, but the
    always-deny rule matches 'token' and exempts only gen_ai.usage.*_tokens. Deny wins."""
    m = span_map({"gen_ai.request.max_tokens": 256})
    assert m.values == {} and m.dropped == 1


@pytest.mark.parametrize("order", ["standard_first", "alias_first"])
def test_standard_name_wins_over_legacy_alias(order):
    pairs = [("gen_ai.provider.name", "openai"), ("gen_ai.system", "legacy-provider")]
    if order == "alias_first":
        pairs.reverse()
    m = span_map(pairs)
    assert m.values == {"gen_ai.provider.name": "openai"}
    assert m.dropped == 1  # the alias is dropped and counted (T03-S2)


def test_alias_alone_is_mapped_to_the_standard_name_and_not_counted():
    m = span_map(
        {
            "gen_ai.system": "openai",
            "gen_ai.usage.prompt_tokens": 7,
            "gen_ai.usage.completion_tokens": 3,
        }
    )
    assert m.values == {
        "gen_ai.provider.name": "openai",
        "gen_ai.usage.input_tokens": 7,
        "gen_ai.usage.output_tokens": 3,
    }
    assert m.dropped == 0


def test_invalid_standard_value_falls_back_to_a_valid_alias():
    m = span_map([("gen_ai.usage.input_tokens", "100"), ("gen_ai.usage.prompt_tokens", 100)])
    assert m.values == {"gen_ai.usage.input_tokens": 100}
    assert m.dropped == 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("gen_ai.usage.input_tokens", "1250"),  # string, not int
        ("gen_ai.usage.input_tokens", 12.5),
        ("gen_ai.usage.output_tokens", -1),  # negative
        ("gen_ai.request.temperature", float("nan")),  # JSONB has no NaN
        ("gen_ai.request.temperature", float("inf")),
        ("gen_ai.request.stream", "true"),
        ("gen_ai.response.finish_reasons", ["stop", 1]),
        ("gen_ai.response.finish_reasons", "stop"),
        ("llminsight.attempt", 0),
        ("llminsight.attempt", 2**31),
        ("llminsight.provenance", "REAL"),
        ("llminsight.provenance", "mixed"),
        ("llminsight.usage_source", "guessed"),
        ("gen_ai.operation.name", ""),
        ("gen_ai.response.id", ""),
        ("gen_ai.request.model", b"bytes"),
    ],
)
def test_wrong_type_or_out_of_range_values_are_dropped_and_counted(key, value):
    m = span_map({key: value})
    assert key not in m.values and m.dropped == 1


def test_numbers_accept_int_and_finite_double():
    m = span_map({"gen_ai.request.temperature": 0.7, "gen_ai.request.top_p": 1})
    assert m.values == {"gen_ai.request.temperature": 0.7, "gen_ai.request.top_p": 1}


def test_strings_truncated_to_256_arrays_to_16_and_nul_replaced():
    m = span_map(
        {
            "gen_ai.request.model": "m" * 300,
            "gen_ai.response.finish_reasons": [f"r{i}" for i in range(20)],
            "http.route": "/a\x00b",
        }
    )
    assert m.values["gen_ai.request.model"] == "m" * 256
    assert m.values["gen_ai.response.finish_reasons"] == [f"r{i}" for i in range(16)]
    assert m.values["http.route"] == "/a�b"
    assert m.dropped == 0


@pytest.mark.parametrize(
    "key,limit",
    [("gen_ai.response.id", 256), ("llminsight.call_id", 64), ("gen_ai.provider.name", 64)],
)
def test_identity_strings_are_dropped_not_truncated_when_too_long(key, limit):
    assert span_map({key: "x" * limit}).values == {key: "x" * limit}
    m = span_map({key: "x" * (limit + 1)})
    assert m.values == {} and m.dropped == 1


def test_duplicate_key_first_value_wins_and_repeat_is_counted():
    m = span_map([("http.route", "/first"), ("http.route", "/second")])
    assert m.values == {"http.route": "/first"} and m.dropped == 1


def test_resource_allowlist():
    m = attrmap.map_resource(kvs({"service.name": "svc", "host.name": "h", "process.pid": 1}))
    assert m.values == {"service.name": "svc"} and m.dropped == 2


def test_inference_detection_uses_operation_name_only():
    assert attrmap.is_inference({"gen_ai.operation.name": "chat"})
    assert attrmap.is_inference({"gen_ai.operation.name": "embeddings"})
    assert not attrmap.is_inference({"gen_ai.operation.name": "execute_tool"})
    assert not attrmap.is_inference({"gen_ai.request.model": "gpt-4o-mini"})


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Error code: 401 - Incorrect API key provided: sk-abcDEF123456789", "abcDEF123"),
        ("upstream said Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("bad api_key=hunter2hunter2 in config", "hunter2"),
        ("password: correct-horse", "correct-horse"),
        ("key lli_AbCd1234_abcd-efgh-ijkl was rejected", "abcd-efgh"),
    ],
    ids=["openai-key", "bearer-header", "api-key-assignment", "password", "ingest-key"],
)
def test_status_message_scrubbing_masks_credential_like_text(text, secret):
    out = attrmap.scrub_free_text(text)
    assert secret not in out
    assert "[REDACTED]" in out


def test_status_message_scrubbing_keeps_ordinary_text():
    assert attrmap.scrub_free_text("rate limited (429), retry in 2s") == (
        "rate limited (429), retry in 2s"
    )
