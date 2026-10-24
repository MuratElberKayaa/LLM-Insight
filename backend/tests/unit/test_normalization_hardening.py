"""Phase 1C normalization contract hardening (Task A). All telemetry here is SYNTHETIC.

Each test names the Task A rule it covers. The integration counterparts (what reaches the
database) are in tests/integration/test_normalization_persistence.py.
"""

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Status as SpanStatus

from app.services.ingestion import attrmap
from app.services.ingestion import normalize as normalize_mod
from app.services.ingestion.normalize import normalize
from app.services.ingestion.usage import canonical_call_key, usage_row
from tests.otlp import FIXTURE_TRACE_ID, MS, T0, inference_attrs, kvs, request, span

# Every token-count attribute of the pinned GenAI semconv (9ffc7e00c639, client-inference.md),
# including the modality breakdowns that attrmap v1 does not allowlist, plus the ADR-14 aliases.
SEMCONV_TOKEN_COUNTERS = [
    "gen_ai.request.max_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.reasoning.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_write.input_tokens",
    "gen_ai.usage.text.input_tokens",
    "gen_ai.usage.text.output_tokens",
    "gen_ai.usage.text.cache_read.input_tokens",
    "gen_ai.usage.audio.input_tokens",
    "gen_ai.usage.audio.output_tokens",
    "gen_ai.usage.audio.cache_read.input_tokens",
    "gen_ai.usage.image.input_tokens",
    "gen_ai.usage.image.output_tokens",
    "gen_ai.usage.image.cache_read.input_tokens",
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.completion_tokens",
]


def span_map(attrs):
    return attrmap.map_span(kvs(attrs))


def normalized(**kw):
    spans, violations = normalize(request(span(**kw)))
    assert violations == []
    return spans[0]


# ---------------------------------------------------------------- A1: max_tokens
@pytest.mark.parametrize("value", [0, 1, 256, 128_000])
def test_a1_request_max_tokens_is_stored_as_a_number(value):
    m = span_map({"gen_ai.request.max_tokens": value})
    assert m.values == {"gen_ai.request.max_tokens": value} and m.dropped == 0


@pytest.mark.parametrize("value", ["256", 25.6, -1, True, [256]])
def test_a1_invalid_max_tokens_values_are_dropped_and_counted(value):
    m = span_map({"gen_ai.request.max_tokens": value})
    assert m.values == {} and m.dropped == 1


# ---------------------------------------------------------------- A2: token counters
@pytest.mark.parametrize("key", SEMCONV_TOKEN_COUNTERS)
def test_a2_no_semconv_token_counter_is_classified_as_a_credential(key):
    assert not attrmap.is_denied(key)


def test_a2_every_allowlisted_and_alias_key_passes_the_deny_rules():
    keys = [*attrmap.SPAN_ALLOWLIST, *attrmap.SPAN_ALIASES, *attrmap.RESOURCE_ALLOWLIST]
    assert [k for k in keys if attrmap.is_denied(k)] == []


def test_a2_allowlisted_usage_counters_reach_the_mapped_values():
    counters = {
        "gen_ai.usage.input_tokens": 1250,
        "gen_ai.usage.output_tokens": 180,
        "gen_ai.usage.cache_read.input_tokens": 1024,
        "gen_ai.usage.cache_write.input_tokens": 0,
        "gen_ai.usage.reasoning.output_tokens": 64,
        "gen_ai.request.max_tokens": 512,
    }
    m = span_map(counters)
    assert m.values == counters and m.dropped == 0


def test_a2_modality_counters_are_dropped_as_not_allowlisted_not_as_credentials():
    """v1 has no column or allowlist entry for them (a Phase 2 / ADR-15 decision). They are
    dropped by the allowlist and counted, not by the credential rule."""
    m = span_map({"gen_ai.usage.audio.input_tokens": 5})
    assert m.values == {} and m.dropped == 1
    assert not attrmap.is_credential_key("gen_ai.usage.audio.input_tokens")


# ---------------------------------------------------------------- A3/A4: credential classes
CREDENTIAL_KEYS = [
    "token",
    "auth.token",
    "access_token",
    "refresh_token",
    "id_token",
    "accessToken",
    "llminsight.session_token",
    "oauth.access_tokens",
    "x-auth-token",
    "bearer",
    "jwt",
    "authorization",
    "Authorization",
    "proxy_authorization",
    "api_key",
    "apiKey",
    "APIKey",
    "x-api-key",
    "openai.api_key",
    "gen_ai.request.api_key",
    "password",
    "db.password",
    "passwordHash",
    "passwd",
    "pwd",
    "client_secret",
    "aws.secret_access_key",
    "my.secret",
    "private_key",
    "privateKey",
    "signing_key",
    "encryption.keys",
    "credentials",
    "cookie",
    "set-cookie",
    "gen_ai.usage.input_tokens.token",
    "gen_ai.usage.input_tokens_secret",
]


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_a3_credential_keys_are_classified_and_never_stored(key):
    assert attrmap.is_credential_key(key)
    m = span_map({key: "SENSITIVE-VALUE"})
    assert m.values == {} and m.dropped == 1


@pytest.mark.parametrize(
    "key",
    [
        "gen_ai.request.max_tokens",
        "max_tokens",
        "input_tokens",
        "inputTokens",
        "tokens_per_second",
        "author.name",
        "keyboard.layout",
        "cache.key",
        "monkey.count",
    ],
)
def test_a3_classification_is_by_word_not_by_substring(key):
    assert not attrmap.is_credential_key(key)


def test_a3_credential_rule_overrides_a_misconfigured_allowlist():
    broken = dict(attrmap.SPAN_ALLOWLIST)
    broken["auth.token"] = attrmap.LABEL
    broken["openai.api_key"] = attrmap.LABEL
    m = attrmap.map_attributes(kvs({"auth.token": "t", "openai.api_key": "k"}), broken)
    assert m.values == {} and m.dropped == 2


@pytest.mark.parametrize(
    "key",
    [
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.definitions",
        "gen_ai.prompt.variable.customer",
        "http.request.header.x-custom",
        "http.response.header.location",
        "url.full",
        "url.query",
    ],
)
def test_a4_content_and_header_attributes_stay_excluded(key):
    assert attrmap.is_denied(key)
    assert span_map({key: "x"}).values == {}


# ---------------------------------------------------------------- A5: aliases
@pytest.mark.parametrize("order", ["standard_first", "alias_first"])
@pytest.mark.parametrize(
    "standard,alias,std_value,alias_value",
    [
        ("gen_ai.provider.name", "gen_ai.system", "openai", "legacy"),
        ("gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", 100, 999),
        ("gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens", 20, 999),
    ],
)
def test_a5_standard_name_overrides_every_legacy_alias(
    order, standard, alias, std_value, alias_value
):
    pairs = [(standard, std_value), (alias, alias_value)]
    if order == "alias_first":
        pairs.reverse()
    m = span_map(pairs)
    assert m.values == {standard: std_value} and m.dropped == 1


# ---------------------------------------------------------------- A7: span name fallback
@pytest.mark.parametrize("name", ["", "\x00"])
def test_a7_missing_span_name_gets_a_display_fallback(name):
    s = normalized(name=name)
    # Empty -> display fallback; a NUL byte is unsafe -> the distinct R08 fallback, counted.
    assert s.name == ("unknown_span" if name == "" else "redacted_span")
    assert s.server_dropped_attributes_count == (0 if name == "" else 1)


# ---------------------------------------------------------------- A8: IDs never truncated
@pytest.mark.parametrize(
    "raw_ids,reason",
    [
        ({"trace_id": bytes.fromhex(FIXTURE_TRACE_ID) + b"\x01"}, "INVALID_TRACE_ID"),  # 17 B
        ({"trace_id": bytes.fromhex(FIXTURE_TRACE_ID)[:15]}, "INVALID_TRACE_ID"),  # 15 B
        ({"span_id": b"\x01" * 9}, "INVALID_SPAN_ID"),
        ({"span_id": b"\x01" * 7}, "INVALID_SPAN_ID"),
        ({"parent_span_id": b"\x02" * 9}, "INVALID_PARENT_SPAN_ID"),
        ({"parent_span_id": b"\x02" * 16}, "INVALID_PARENT_SPAN_ID"),
    ],
)
def test_a8_wrong_length_ids_are_rejected_never_truncated(raw_ids, reason):
    spans, violations = normalize(request(span(raw_ids=raw_ids)))
    assert spans == []  # nothing usable, so nothing truncated into a valid-looking ID
    assert [v.reason for v in violations] == [reason]


@pytest.mark.parametrize(
    "key,limit",
    [("gen_ai.response.id", 256), ("llminsight.call_id", 64), ("gen_ai.provider.name", 64)],
)
def test_a8_identity_attributes_over_their_limit_are_dropped_not_truncated(key, limit):
    m = span_map({key: "y" * (limit + 1)})
    assert key not in m.values and m.dropped == 1


# ---------------------------------------------------------------- A9: prompt_version
@pytest.mark.parametrize("value", ["v1", "2026-10-24.1", "summarizer/v2", "v1.2.3+rc.1", "a" * 64])
def test_a9_bounded_prompt_versions_are_accepted(value):
    assert span_map({"gen_ai.prompt.version": value}).values == {"gen_ai.prompt.version": value}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 65,  # too long: dropped, never truncated (would merge versions)
        "v 1",
        "v1\n",
        "line1\r\nline2",
        "user@example.com",
        "-v1",
        ".hidden",
        "v1\x00",
        "v1‮",  # right-to-left override
        "sürüm1",  # non-ASCII
        "summarize the customer's complaint",
    ],
    ids=lambda v: f"len{len(v)}",
)
def test_a9_free_text_or_unbounded_prompt_versions_are_dropped_and_counted(value):
    m = span_map({"gen_ai.prompt.version": value})
    assert m.values == {} and m.dropped == 1


def test_a9_prompt_version_must_be_a_string():
    assert span_map({"gen_ai.prompt.version": 1}).values == {}


# ---------------------------------------------------------------- A10: schema-exact values
def test_a10_dropped_attribute_count_saturates_at_the_int_column_limit(monkeypatch):
    monkeypatch.setattr(normalize_mod, "MAX_DROPPED_COUNT", 5)
    s = normalized(attrs={f"custom.k{i}": i for i in range(9)})
    assert s.server_dropped_attributes_count == 5


def test_a10_dropped_count_includes_span_and_resource_drops():
    spans, _ = normalize(
        request(
            span(attrs={"custom.a": 1, "auth.token": "x"}),
            resource={"service.name": "svc", "host.name": "h"},
        )
    )
    assert spans[0].server_dropped_attributes_count == 3


@pytest.mark.parametrize(
    "overrides,usage_source,reason",
    [
        ({}, "provider_reported", "price_missing"),
        ({"llminsight.usage_source": "estimated"}, "estimated", "price_missing"),
        ({"llminsight.usage_source": None}, "provider_reported", "price_missing"),
        ({"gen_ai.usage.output_tokens": None}, "unknown", "usage_missing"),
        ({"gen_ai.usage.input_tokens": None}, "unknown", "usage_missing"),
        ({"gen_ai.usage.cache_read.input_tokens": 101}, "provider_reported", "usage_inconsistent"),
        ({"gen_ai.usage.reasoning.output_tokens": 21}, "provider_reported", "usage_inconsistent"),
        (
            {"gen_ai.usage.output_tokens": None, "gen_ai.usage.cache_read.input_tokens": 101},
            "unknown",
            "usage_inconsistent",
        ),
    ],
)
def test_a10_usage_source_and_cost_reason_follow_the_locked_enums(overrides, usage_source, reason):
    row = usage_row(1, normalized(attrs=inference_attrs(**{k: v for k, v in overrides.items()})))
    assert row["usage_source"] == usage_source
    assert row["usage_source"] in ("provider_reported", "estimated", "unknown")
    assert (row["cost_status"], row["cost_unknown_reason"], row["cost_usd"]) == (
        "unknown",
        reason,
        None,
    )


# ---------------------------------------------------------------- A11: no fabrication
def test_a11_missing_tokens_provider_and_models_are_not_invented():
    attrs = inference_attrs(
        gen_ai__provider__name=None,
        gen_ai__request__model=None,
        gen_ai__response__model=None,
        gen_ai__usage__input_tokens=None,
        gen_ai__usage__output_tokens=None,
    )
    row = usage_row(1, normalized(attrs=attrs))
    assert row["provider"] == "unknown"  # the schema's explicit marker (LEN(64) NOT NULL)
    assert row["requested_model"] is None and row["response_model"] is None
    for col in (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "reasoning_output_tokens",
    ):
        assert row[col] is None
    assert row["cost_usd"] is None and row["usage_source"] == "unknown"


# ---------------------------------------------------------------- A12: provenance trust
@pytest.mark.parametrize(
    "claim,response_id,status,expected",
    [
        (None, "chatcmpl-X", 0, "unknown"),  # a response ID alone never makes a span real
        ("real", "chatcmpl-X", 0, "real"),  # stored as asserted; still unverified
        ("real", None, 0, "unknown"),  # T48-S2 downgrade
        ("real", None, SpanStatus.STATUS_CODE_OK, "unknown"),
        ("real", None, SpanStatus.STATUS_CODE_ERROR, "real"),  # failed calls have no ID
        ("synthetic", "chatcmpl-X", 0, "synthetic"),
        ("synthetic", None, 0, "synthetic"),
    ],
)
def test_a12_real_is_a_client_claim_that_is_only_ever_downgraded(
    claim, response_id, status, expected
):
    attrs = inference_attrs(llminsight__provenance=claim, gen_ai__response__id=response_id)
    if claim is None:
        attrs.pop("llminsight.provenance", None)
    if response_id is None:
        attrs.pop("gen_ai.response.id", None)
    assert normalized(attrs=attrs, status_code=status).provenance == expected


def test_a12_non_inference_spans_never_carry_a_claimed_provenance():
    s = normalized(attrs={"llminsight.provenance": "real", "gen_ai.response.id": "x"})
    assert s.provenance == "unknown"


# ---------------------------------------------------------------- error identifiers (R21)
@pytest.mark.parametrize("value", ["timeout", "500", "openai.RateLimitError", "_OTHER", "a$b"])
def test_error_type_identifiers_are_kept(value):
    assert span_map({"error.type": value}).values == {"error.type": value}


@pytest.mark.parametrize(
    "value",
    ["", "rate limited by upstream", "Error: key sk-abc", "x\ny", "a" * 257, "tür"],
    ids=lambda v: f"len{len(v)}",
)
def test_error_type_descriptions_are_dropped_and_counted(value):
    m = span_map({"error.type": value})
    assert m.values == {} and m.dropped == 1


def test_exception_type_event_is_used_only_when_it_is_an_identifier():
    s = span(status_code=SpanStatus.STATUS_CODE_ERROR)
    ev = s.events.add(name="exception", time_unix_nano=T0 + MS)
    ev.attributes.extend(kvs({"exception.type": "Incorrect API key provided: sk-x"}))
    spans, _ = normalize(request(s))
    assert spans[0].error_type is None


# ---------------------------------------------------------------- call-key namespace (Task E)
def test_provider_namespace_is_escaped_so_keys_cannot_collide():
    a = normalized(attrs=inference_attrs(gen_ai__provider__name="a:b", gen_ai__response__id="c"))
    b = normalized(attrs=inference_attrs(gen_ai__provider__name="a", gen_ai__response__id="b:c"))
    ka, _ = canonical_call_key(a, "a:b")
    kb, _ = canonical_call_key(b, "a")
    assert ka == "resp:a%3Ab:c" and kb == "resp:a:b:c" and ka != kb
    pct, _ = canonical_call_key(a, "a%3Ab")
    assert pct == "resp:a%253Ab:c" != ka


def test_ordinary_provider_keys_are_unchanged_by_escaping():
    s = normalized(attrs=inference_attrs())
    assert canonical_call_key(s, "openai") == (
        "resp:openai:chatcmpl-SYNTHETIC-T1",
        "provider_response_id",
    )
