"""R08 (Phase 1 final): credentials and personal data in otherwise permitted string fields.

Every "secret" here is SYNTHETIC and non-working, assembled at runtime so no credential-shaped
literal sits in the source or in pytest node IDs (the doc check scans both).
"""

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Status as SpanStatus

from app.services.ingestion import attrmap, sensitive
from app.services.ingestion.normalize import normalize
from tests.otlp import MS, T0, inference_attrs, kvs, request, span

FAKE = {
    "openai": "sk-" + "proj-" + "Q7x" * 8,
    "anthropic": "sk-" + "ant-api03-" + "Z9" * 10,
    "ingest": "lli_" + "AbCd1234" + "_" + "k" * 30,
    "aws": "AKIA" + "Q" * 16,
    "github": "ghp_" + "G1" * 12,
    "github_pat": "github_pat_" + "P2" * 12,
    "slack": "xox" + "b-" + "1234567890-abcdef",
    "google": "AIza" + "S" * 35,
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + ".sig",
    "bearer": "Bearer " + "t0ken" * 4,
    "basic": "Basic " + "dXNlcjpwYXNz" + "MTIzNDU2",
    "url_userinfo": "postgres://app:" + "Pw" * 5 + "@db.internal/prod",
    "assignment": "api_key=" + "x1" * 6,
    "password_assignment": "password: " + "hunter" + "22",
    "private_key": "-----BEGIN " + "RSA PRIVATE KEY-----",
}
PII_LABEL = {
    "email": "notify ayse.yilmaz" + "@" + "example.com",
    "intl_phone": "callback +90 5" + "32 111 22 33",
    "local_phone": "callback 0532 111 22 33",
    "national_id": "customer 1234567" + "8901",
    "card": "card 4111 1111 1111 1111",
    "iban": "TR33 0006 1005 1978 6457 8413 26",
}
UNSAFE_CHARS = {
    "newline": "a\nb",
    "crlf": "a\r\nb",
    "tab": "a\tb",
    "nul": "a\x00b",
    "rtl_override": "abc‮def",
    "zero_width": "ab​c",
    "bom": "﻿abc",
    "line_separator": "a b",
}


def span_map(attrs):
    return attrmap.map_span(kvs(attrs))


# ---------------------------------------------------------------- detectors
@pytest.mark.parametrize("name", sorted(FAKE))
def test_obvious_credentials_are_detected(name):
    assert sensitive.contains_credential(FAKE[name])
    assert sensitive.contains_credential(f"prefix {FAKE[name]} suffix")


@pytest.mark.parametrize("name", sorted(PII_LABEL))
def test_personal_data_is_detected_in_free_text(name):
    assert sensitive.contains_personal_data(PII_LABEL[name], free_text=True)


@pytest.mark.parametrize("name", sorted(UNSAFE_CHARS))
def test_control_format_and_separator_characters_are_unsafe(name):
    assert sensitive.has_unsafe_characters(UNSAFE_CHARS[name])


# Valid GenAI / OTel metadata that must not be mistaken for secrets or personal data.
VALID_IDENTIFIERS = [
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
    "ft:gpt-4o-mini-2024-07-18:acme:summarizer:9xYz1234",
    "claude-3-5-sonnet-20241022",
    "claude-3-sonnet@20240229",
    "anthropic.claude-3-sonnet-20240229-v1:0",
    "models/gemini-1.5-pro-002",
    "meta-llama/Llama-3.1-8B-Instruct",
    "text-embedding-3-small",
    "o1-preview",
    "risk-assessment-model",
    "task-router-v2",
]
VALID_OPAQUE = [
    "chatcmpl-9xYzAbCdEf0123456789",
    "resp_67ccd2bed1ec8190",
    "msg_01XFDUDYJgAACzvnptvVoYEL",
]
VALID_LABELS = [
    "chat gpt-4o-mini",
    "POST /v1/summarize",
    "GET /users/{id}",
    "execute_tool get_weather",
    "basic authentication flow",
    "token budget check",
    "auth: login",
    "demo-summarizer",
    "Müşteri özeti servisi",
    "opentelemetry.instrumentation.fastapi",
    "release 2026-10-24",
]


@pytest.mark.parametrize("value", VALID_IDENTIFIERS + VALID_OPAQUE + VALID_LABELS)
def test_valid_metadata_is_not_flagged(value):
    assert not sensitive.contains_credential(value)
    assert not sensitive.has_unsafe_characters(value)
    if value not in VALID_OPAQUE:
        free_text = value in VALID_LABELS
        assert not sensitive.contains_personal_data(value, free_text=free_text)


# ---------------------------------------------------------------- per-field formats
VALID_FIELD_VALUES = {
    "gen_ai.operation.name": ["chat", "embeddings", "execute_tool", "invoke_agent"],
    "gen_ai.provider.name": ["openai", "gcp.vertex_ai", "aws.bedrock", "azure.ai.openai", "x_ai"],
    "gen_ai.request.model": VALID_IDENTIFIERS,
    "gen_ai.response.model": VALID_IDENTIFIERS,
    "gen_ai.response.id": VALID_OPAQUE,
    "gen_ai.output.type": ["json", "text", "image", "speech"],
    "gen_ai.prompt.name": ["summarize", "support/triage-v2"],
    "gen_ai.prompt.version": ["v1", "2026-10-24.1", "summarizer/v2"],
    "error.type": ["timeout", "500", "openai.RateLimitError", "_OTHER"],
    "server.address": ["api.openai.com", "127.0.0.1", "[::1]", "my-host.internal"],
    "openai.request.service_tier": ["auto", "default", "flex"],
    "openai.response.service_tier": ["scale", "default"],
    "openai.response.system_fingerprint": ["fp_44709d6fcb"],
    "openai.api.type": ["chat_completions", "responses"],
    "http.request.method": ["GET", "POST", "_OTHER"],
    "http.route": ["/v1/summarize", "/users/{id}", "/items/:item_id", "/"],
    "url.scheme": ["http", "https"],
    "llminsight.call_id": ["5f0c6e1a-3b7d-4c2e-9a41-000000000001"],
    "llminsight.validation.rule": ["json_schema", "field_match:summary"],
}


@pytest.mark.parametrize("key,value", [(k, v) for k, vs in VALID_FIELD_VALUES.items() for v in vs])
def test_valid_genai_and_otel_values_are_preserved(key, value):
    m = span_map({key: value})
    assert m.values == {key: value} and m.dropped == 0


def test_valid_finish_reasons_and_resource_attributes_are_preserved():
    m = span_map({"gen_ai.response.finish_reasons": ["stop", "length", "tool_calls"]})
    assert m.values == {"gen_ai.response.finish_reasons": ["stop", "length", "tool_calls"]}
    res = {
        "service.name": "demo-summarizer",
        "service.version": "1.4.2+build.7",
        "service.namespace": "llm insight demo",
        "deployment.environment.name": "production",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": "1.45.1",
        "llminsight.sdk.version": "0.1.0",
    }
    r = attrmap.map_resource(kvs(res))
    assert r.values == res and r.dropped == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("gen_ai.operation.name", "Chat Completion"),
        ("gen_ai.provider.name", "open ai"),
        ("gen_ai.provider.name", "a:b"),
        ("gen_ai.request.model", "gpt 4o"),
        ("gen_ai.request.model", "gpt-4o?key=1"),
        ("gen_ai.response.id", "id with spaces"),
        ("gen_ai.output.type", "free text"),
        ("server.address", "user@host"),
        ("http.route", "/search?q=term"),
        ("http.route", "users/{id}"),
        ("url.scheme", "https://"),
        ("http.request.method", "GET /"),
        ("llminsight.validation.rule", "check the summary"),
    ],
    ids=lambda v: str(v)[:30],
)
def test_values_outside_the_field_format_are_dropped(key, value):
    m = span_map({key: value})
    assert m.values == {} and m.dropped == 1


# ---------------------------------------------------------------- secrets in permitted fields
SECRET_CARRIERS = {
    "gen_ai.request.model": lambda s: f"ft:gpt-4o:acme:{s}",
    "gen_ai.response.model": lambda s: s,
    "gen_ai.response.id": lambda s: s,
    "gen_ai.prompt.name": lambda s: f"summarize/{s}",
    "gen_ai.prompt.version": lambda s: f"v1-{s}"[:64],
    "error.type": lambda s: s,
    "openai.response.system_fingerprint": lambda s: s,
    "http.route": lambda s: f"/v1/{s}",
    "llminsight.validation.rule": lambda s: f"rule:{s}",
}
FORMAT_SAFE_SECRETS = ["openai", "anthropic", "ingest", "aws", "github", "github_pat", "google"]


@pytest.mark.parametrize("secret", FORMAT_SAFE_SECRETS)
@pytest.mark.parametrize("key", sorted(SECRET_CARRIERS))
def test_credentials_hidden_in_format_valid_values_are_dropped(key, secret):
    """The value passes the field's character format, so only the credential layer stops it."""
    value = SECRET_CARRIERS[key](FAKE[secret])
    m = span_map({key: value})
    assert m.values == {} and m.dropped == 1


@pytest.mark.parametrize("name", sorted(FAKE))
def test_any_credential_in_a_label_is_dropped(name):
    r = attrmap.map_resource(kvs({"service.name": f"svc {FAKE[name]}"}))
    assert r.values == {} and r.dropped == 1


@pytest.mark.parametrize("name", sorted(PII_LABEL))
def test_personal_data_in_a_label_is_dropped(name):
    r = attrmap.map_resource(kvs({"deployment.environment.name": PII_LABEL[name]}))
    assert r.values == {} and r.dropped == 1


@pytest.mark.parametrize(
    "value",
    ["ft:gpt-4o:ayse.yilmaz" + "@" + "example.com", "gpt-4o-12345678901", "model-0532111223344"],
    ids=["email", "long-digits", "phone-digits"],
)
def test_personal_data_in_an_identifier_is_dropped(value):
    assert span_map({"gen_ai.request.model": value}).values == {}


@pytest.mark.parametrize("name", sorted(UNSAFE_CHARS))
def test_unsafe_characters_drop_labels_and_identifiers(name):
    assert attrmap.map_resource(kvs({"service.name": UNSAFE_CHARS[name]})).values == {}
    assert span_map({"gen_ai.request.model": f"gpt{UNSAFE_CHARS[name]}"}).values == {}


def test_checks_run_before_truncation_so_no_secret_prefix_survives():
    value = "a" * 240 + " " + FAKE["openai"]  # the key starts inside the first 256 chars
    assert attrmap.safe_string(attrmap.LABEL, value) is None
    value = "a" * 300 + " " + FAKE["openai"]  # the key would be cut off entirely
    assert attrmap.safe_string(attrmap.LABEL, value) is None  # still dropped, not truncated


def test_opaque_ids_skip_only_the_personal_data_layer():
    """Random provider IDs can contain digit runs; credentials are still refused."""
    digits = "chatcmpl-" + "1" * 20
    assert span_map({"gen_ai.response.id": digits}).values == {"gen_ai.response.id": digits}
    assert span_map({"gen_ai.response.id": FAKE["openai"]}).values == {}


def test_choice_fields_accept_only_their_vocabulary():
    assert span_map({"llminsight.provenance": "real"}).values == {"llminsight.provenance": "real"}
    assert span_map({"llminsight.usage_source": FAKE["openai"]}).values == {}


# ---------------------------------------------------------------- span and scope fields
def test_unsafe_span_name_becomes_redacted_span_and_is_counted():
    spans, _ = normalize(request(span(name=f"call {FAKE['bearer']}")))
    assert spans[0].name == "redacted_span"
    assert spans[0].server_dropped_attributes_count == 1


@pytest.mark.parametrize(
    "scope,expected_name,expected_version,withheld",
    [
        (("llm_insight_sdk", "0.1.0"), "llm_insight_sdk", "0.1.0", 0),
        ((f"sdk {FAKE['jwt']}", "0.1.0"), None, "0.1.0", 1),
        (("llm_insight_sdk", FAKE["aws"]), "llm_insight_sdk", None, 1),
        ((PII_LABEL["email"], "1\n2"), None, None, 2),
    ],
    ids=["valid", "jwt-name", "aws-version", "both"],
)
def test_unsafe_scope_fields_become_null_and_are_counted_on_every_span(
    scope, expected_name, expected_version, withheld
):
    spans, _ = normalize(
        request(span(span_id="1" * 16), span(span_id="2" * 16, start=T0 + MS), scope=scope)
    )
    for s in spans:
        assert (s.scope_name, s.scope_version) == (expected_name, expected_version)
        assert s.server_dropped_attributes_count == withheld


def test_exception_type_with_a_credential_is_not_used_as_error_type():
    s = span(status_code=SpanStatus.STATUS_CODE_ERROR)
    ev = s.events.add(name="exception", time_unix_nano=T0)
    ev.attributes.extend(kvs({"exception.type": FAKE["github"]}))
    spans, _ = normalize(request(s))
    assert spans[0].error_type is None


def test_valid_inference_span_is_unchanged_by_the_privacy_layer():
    spans, _ = normalize(request(span(name="chat gpt-4o-mini", attrs=inference_attrs())))
    s = spans[0]
    assert s.name == "chat gpt-4o-mini" and s.server_dropped_attributes_count == 0
    assert s.attributes == inference_attrs()
