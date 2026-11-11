"""OpenRouter chat completions adapter (ADR-46), fully offline: injected fake httpx transports,
the exported protobuf checked at the local OTLP stub. Every response is a SYNTHETIC fixture and
every span is labelled `synthetic`. No request can leave the machine (`offline`)."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_insight_sdk import groq_adapter, openrouter_adapter
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.groq_live import FreePlanSession
from llm_insight_sdk.instrumentation import TokenUsage
from llm_insight_sdk.openrouter_adapter import (
    ENDPOINT_URL,
    MAX_OUTPUT_TOKENS,
    FreeRoutingSignals,
    ObservedOpenRouterClient,
    OpenRouterCallError,
    OpenRouterTextAdapter,
    UnsupportedRequestError,
    build_openrouter_http_client,
    is_free_model_id,
    live_openrouter_client_problems,
    parse_completion,
    usage_from_openrouter_body,
)
from llm_insight_sdk.openrouter_live import FreeModelSession
from llm_insight_sdk.providers import ERROR_CATEGORIES, TextGenerationAdapter, TextRequest
from tests.conftest import PROMPT_SENTINEL
from tests.groq_fixtures import Step
from tests.openai_fixtures import block_network
from tests.openrouter_fixtures import (
    ENDPOINT,
    FAKE_OPENROUTER_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    PAID_MODEL,
    RESPONSE_ID,
    SENTINELS,
    FakeOpenRouter,
    error_body,
    fail,
    ok,
    openrouter_body,
    usage_with,
)
from tests.unit.test_instrumentation import ALLOWED_INFERENCE_KEYS, exported

REPO = Path(__file__).resolve().parents[3]
SECRETS = (*SENTINELS, PROMPT_SENTINEL)
SPAN_KEYS = ALLOWED_INFERENCE_KEYS | {"server.address", "server.port"}


@pytest.fixture(autouse=True)
def offline() -> Iterator[list[str]]:
    attempts: list[str] = []
    with block_network(attempts):
        yield attempts
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def observed(telemetry, fake: FakeOpenRouter, **http: Any) -> ObservedOpenRouterClient:
    return ObservedOpenRouterClient(
        telemetry, FAKE_OPENROUTER_KEY, http_client=fake.http_client(**http), provenance="synthetic"
    )


def adapter(telemetry, fake: FakeOpenRouter) -> OpenRouterTextAdapter:
    return OpenRouterTextAdapter(observed(telemetry, fake))


def text_request(**kw: Any) -> TextRequest:
    kw.setdefault("model", MODEL)
    kw.setdefault("input_text", PROMPT_SENTINEL)
    kw.setdefault("max_output_tokens", 64)
    return TextRequest(**kw)


def client_spans(spans):
    return [s for s in spans if s["kind"] == "CLIENT"]


def assert_no_secrets(*items: object) -> None:
    text = json.dumps(items, default=repr)
    for secret in SECRETS:
        assert secret not in text


def one_span(telemetry, stub):
    (span,) = client_spans(exported(telemetry, stub))
    return span


def raw(body: object) -> bytes:
    return json.dumps(body).encode()


# ---------------------------------------------------------------- the exact request
def test_a_completed_call_emits_exactly_the_contract_span(telemetry, stub):
    """The same file is ingested by backend/tests/unit/test_openrouter_contract.py."""
    fixture = json.loads(
        (REPO / "docs" / "fixtures" / "openrouter_client_span.v1.json").read_text(encoding="utf-8")
    )
    outcome = adapter(telemetry, FakeOpenRouter([ok()])).generate_text(
        text_request(prompt_name="smoke", prompt_version="v1")
    )
    assert outcome.status == "completed" and outcome.error_category is None
    span = one_span(telemetry, stub)
    assert span["name"] == fixture["span_name"]
    assert span["kind"] == fixture["span_kind"]
    assert span["status"] == "UNSET" and span["events"] == 0
    attrs = dict(span["attributes"])
    assert uuid.UUID(attrs.pop("llminsight.call_id")).version == 4
    expected = dict(fixture["attributes"])
    del expected["llminsight.call_id"]
    assert attrs == expected
    assert set(span["attributes"]) <= SPAN_KEYS
    assert_no_secrets(span)


def test_the_request_is_exactly_the_pinned_body_with_free_only_routing(telemetry):
    fake = FakeOpenRouter([ok()])
    adapter(telemetry, fake).generate_text(text_request())
    (request,) = fake.requests
    assert str(request.url) == ENDPOINT == ENDPOINT_URL and request.method == "POST"
    assert fake.request_json(0) == {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT_SENTINEL}],
        "max_tokens": 64,
        "stream": False,
        "reasoning": {"exclude": True},
        "provider": {
            "allow_fallbacks": False,
            "data_collection": "deny",
            "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
        },
    }
    assert request.headers["authorization"] == f"Bearer {FAKE_OPENROUTER_KEY}"
    assert {name.lower() for name in request.headers} == groq_adapter._SENT_HEADERS
    # No model list, router, sort, plugin, attribution or trace header can ride along.
    body = request.content.decode()
    for word in ('"models"', '"route"', '"sort"', '"plugins"', '"order"', "HTTP-Referer"):
        assert word not in body and word.lower() not in {h.lower() for h in request.headers}


def test_the_routing_constraints_cannot_be_changed_through_the_request():
    body = openrouter_adapter.request_body(MODEL, "x", 16)
    body["provider"]["allow_fallbacks"] = True
    assert openrouter_adapter.PROVIDER_ROUTING["allow_fallbacks"] is False
    assert openrouter_adapter.request_body(MODEL, "x", 16)["provider"]["allow_fallbacks"] is False


@pytest.mark.parametrize(
    "name",
    [
        "models",
        "route",
        "provider",
        "plugins",
        "tools",
        "tool_choice",
        "temperature",
        "top_p",
        "stream",
        "response_format",
        "transforms",
        "user",
        "seed",
        "reasoning",
        "usage",
    ],
)
def test_every_other_parameter_is_refused_before_anything_is_sent(telemetry, name):
    fake = FakeOpenRouter([])
    with pytest.raises(UnsupportedRequestError, match=name):
        observed(telemetry, fake).create_chat_completion(
            model=MODEL, input_text="x", max_tokens=16, **{name: "anything"}
        )
    assert fake.requests == []


@pytest.mark.parametrize(
    "model",
    [
        PAID_MODEL,  # a paid model: no `:free` suffix
        "openrouter/free",  # a router that picks a model
        "openrouter/auto",  # a router that can pick a paid model
        "openrouter/auto:free",
        "synthetic-org/synthetic-model-7b:nitro",
        "synthetic-org/synthetic-model-7b:floor",
        "synthetic-org/synthetic-model-7b:free:extended",
        "synthetic-org/synthetic-model-7b:FREE",
        "~synthetic-org/latest:free",
        "Synthetic-Org/model:free",
        "synthetic-model:free",  # no organisation
        "synthetic-org/a/b:free",
        "synthetic-org/model:free?x=1",
        " synthetic-org/model:free",
        "",
        None,
        42,
    ],
)
def test_only_one_explicit_free_variant_model_is_accepted(telemetry, model):
    assert not is_free_model_id(model)
    fake = FakeOpenRouter([])
    with pytest.raises(UnsupportedRequestError, match="model"):
        observed(telemetry, fake).create_chat_completion(model=model, input_text="x", max_tokens=16)
    assert fake.requests == []


def test_a_free_variant_model_id_is_accepted():
    assert is_free_model_id(MODEL)
    assert is_free_model_id("google/gemma-4-26b-a4b-it:free")


@pytest.mark.parametrize("limit", [0, MAX_OUTPUT_TOKENS + 1, True, 64.0, "64", None])
def test_the_output_limit_is_bounded(telemetry, limit):
    fake = FakeOpenRouter([])
    with pytest.raises(UnsupportedRequestError, match="max_tokens"):
        observed(telemetry, fake).create_chat_completion(
            model=MODEL, input_text="x", max_tokens=limit
        )
    assert fake.requests == []


@pytest.mark.parametrize(
    "text",
    ["", "   ", None, "\ud800", "x" * (32_768 + 1)],
    ids=["empty", "blank", "none", "surrogate", "oversized"],
)
def test_the_input_must_be_one_bounded_text(telemetry, text):
    fake = FakeOpenRouter([])
    with pytest.raises(UnsupportedRequestError, match="input_text"):
        observed(telemetry, fake).create_chat_completion(
            model=MODEL, input_text=text, max_tokens=16
        )
    assert fake.requests == []


# ---------------------------------------------------------------- outcomes
def test_the_outcome_holds_the_provider_neutral_metadata(telemetry):
    outcome = adapter(telemetry, FakeOpenRouter([ok()])).generate_text(text_request())
    assert outcome.provider == "openrouter"
    assert (outcome.request_model, outcome.response_model) == (MODEL, MODEL)
    assert outcome.response_id == RESPONSE_ID and outcome.service_tier is None
    assert outcome.usage == TokenUsage(
        input_tokens=24,
        output_tokens=40,
        cache_read_input_tokens=4,
        cache_write_input_tokens=2,
        reasoning_output_tokens=8,
    )
    (attempt,) = outcome.attempts
    assert attempt.attempt == 1 and attempt.usage == outcome.usage
    assert outcome.provider_response.text == OUTPUT_SENTINEL
    assert outcome.provider_response.signals == FreeRoutingSignals("zero", "false")
    assert_no_secrets(repr(outcome))


def test_the_adapter_implements_the_provider_protocol(telemetry):
    implementation: TextGenerationAdapter = adapter(telemetry, FakeOpenRouter([]))
    assert implementation.provider == "openrouter"


@pytest.mark.parametrize(
    ("kw", "status", "category", "span_error", "reason"),
    [
        ({"finish": "length"}, "incomplete", None, None, "length"),
        ({"finish": "content_filter"}, "blocked", "content_blocked", None, "content_filter"),
        ({"content": ""}, "failed", "empty_response", "openrouter.response.empty", "stop"),
        ({"content": None}, "failed", "empty_response", "openrouter.response.empty", "stop"),
        ({"choices": []}, "failed", "empty_response", "openrouter.response.empty", None),
        ({"finish": "error"}, "failed", "response_failed", "openrouter.response.failed", "error"),
        (
            {"choice_extra": {"error": {"code": 502, "message": "x"}}},
            "failed",
            "response_failed",
            "openrouter.response.failed",
            "stop",
        ),
        (
            {"finish": "tool_calls"},
            "failed",
            "unexpected_status",
            "openrouter.response.unexpected_finish",
            "tool_calls",
        ),
        (
            {"finish": "something_new"},
            "failed",
            "unexpected_status",
            "openrouter.response.unexpected_finish",
            "something_new",
        ),
        (
            {"finish": None},
            "failed",
            "unexpected_status",
            "openrouter.response.unexpected_finish",
            None,
        ),
        (
            {"model": PAID_MODEL},
            "failed",
            "unexpected_status",
            "openrouter.response.model_mismatch",
            None,  # the completed verdict's reason, kept as the Groq adapter keeps it
        ),
    ],
)
def test_each_finish_is_classified_from_the_response_alone(
    telemetry, stub, kw, status, category, span_error, reason
):
    outcome = adapter(telemetry, FakeOpenRouter([ok(**kw)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category, outcome.provider_reason) == (
        status,
        category,
        reason,
    )
    span = one_span(telemetry, stub)
    if span_error is None:
        assert span["status"] == "UNSET" and "error.type" not in span["attributes"]
    else:
        assert span["status"] == "ERROR" and span["attributes"]["error.type"] == span_error
    assert_no_secrets(span, repr(outcome))


def test_a_tool_call_is_never_a_completion(telemetry):
    body = openrouter_body()
    body["choices"][0]["message"]["tool_calls"] = [{"id": "x", "type": "function"}]
    outcome = adapter(telemetry, FakeOpenRouter([Step(200, body)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("failed", "unexpected_status")


def test_a_response_naming_the_paid_model_keeps_both_models_visible(telemetry):
    outcome = adapter(telemetry, FakeOpenRouter([ok(model=PAID_MODEL)])).generate_text(
        text_request()
    )
    assert (outcome.request_model, outcome.response_model) == (MODEL, PAID_MODEL)
    assert outcome.status == "failed"


@pytest.mark.parametrize(
    ("code", "error_type", "category", "reason"),
    [
        (429, None, "rate_limited", "429"),
        (402, None, "payment_required", "402"),
        (502, "provider_unavailable", "provider_error", "provider_unavailable"),
        (None, None, "provider_error", None),
        ("429", None, "provider_error", None),  # not an integer code
        (True, None, "provider_error", None),
        (200, None, "provider_error", "200"),
    ],
)
def test_an_error_in_a_200_response_is_an_error_never_a_completion(
    telemetry, stub, code, error_type, category, reason
):
    body = error_body(code, error_type)
    outcome = adapter(telemetry, FakeOpenRouter([Step(200, body)])).generate_text(text_request())
    assert outcome.status == "error"
    assert (outcome.error_category, outcome.provider_reason) == (category, reason)
    assert outcome.usage is None and outcome.response_model is None
    span = one_span(telemetry, stub)
    assert span["attributes"]["error.type"] == "openrouter.response.error"
    assert_no_secrets(span, repr(outcome))


def test_an_error_beside_choices_in_a_200_response_still_fails(telemetry):
    body = {**openrouter_body(), **error_body(503)}
    outcome = adapter(telemetry, FakeOpenRouter([Step(200, body)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "provider_error")


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, "invalid_request"),
        (401, "authentication_failed"),
        (402, "payment_required"),
        (403, "permission_denied"),
        (404, "not_found"),
        (408, "timeout"),
        (413, "invalid_request"),
        (422, "invalid_request"),
        (429, "rate_limited"),
        (500, "provider_error"),
        (502, "provider_error"),
        (503, "provider_error"),
        (418, "http_error"),
        (301, "http_error"),
    ],
)
def test_each_http_status_is_one_error_and_never_retried(telemetry, stub, status, category):
    fake = FakeOpenRouter([fail(status, "moderation"), ok()])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert len(fake.requests) == 1 and len(fake.steps) == 1  # the second step is never used
    assert (outcome.status, outcome.error_category) == ("error", category)
    assert outcome.provider_reason == "moderation"
    span = one_span(telemetry, stub)
    assert span["attributes"]["error.type"] == str(status)
    assert_no_secrets(span, repr(outcome))


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (httpx.ConnectError, "connection_error"),
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectTimeout, "timeout"),
        (httpx.RemoteProtocolError, "connection_error"),
    ],
)
def test_a_transport_failure_is_one_attempt_and_never_retried(telemetry, exc, category):
    fake = FakeOpenRouter([Step(raise_=exc), ok()])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert len(fake.requests) == 1
    assert (outcome.status, outcome.error_category) == ("error", category)
    assert len(outcome.attempts) == 1


@pytest.mark.parametrize(
    "step",
    [
        Step(200, raw=b"not json"),
        Step(200, raw=b"NaN"),
        Step(200, body=[openrouter_body()]),
        Step(200, body=openrouter_body(object="chat.completion.chunk")),
        Step(200, body=openrouter_body(model=None)),
        Step(200, body=openrouter_body(model="model with spaces")),
        Step(200, body=openrouter_body(choices=[{}, {}])),
        Step(200, body=openrouter_body(choices="x")),
        Step(200, body=openrouter_body(choices=[{"message": {"role": "user", "content": "x"}}])),
        Step(200, body=openrouter_body(choices=[{"message": {"content": 7}}])),
        Step(200, body=openrouter_body(), content_type="text/plain"),
        Step(200, body=openrouter_body(), content_type="application/x-not-json"),
        Step(
            200,
            stream=httpx.ByteStream(raw(openrouter_body())),
            headers={"content-encoding": "gzip"},
        ),
        Step(200, body=openrouter_body(), headers={"content-length": str(2**21)}),
    ],
    ids=lambda s: "step",
)
def test_an_unusable_200_response_is_malformed(telemetry, step):
    outcome = adapter(telemetry, FakeOpenRouter([step])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "malformed_response")
    assert_no_secrets(repr(outcome))


def test_an_oversized_streamed_body_is_refused_while_reading(telemetry):
    class Endless(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                yield b"x" * 65_536

    outcome = adapter(telemetry, FakeOpenRouter([Step(200, stream=Endless())])).generate_text(
        text_request()
    )
    assert (outcome.status, outcome.error_category) == ("error", "malformed_response")


# ---------------------------------------------------------------- usage and free signals
@pytest.mark.parametrize(
    ("usage", "check"),
    [
        (None, "missing"),
        ("x", "inconsistent"),
        (usage_with(prompt_tokens=None), "missing"),
        (usage_with(total_tokens=None), "missing"),
        (usage_with(total_tokens=65), "inconsistent"),
        (usage_with(prompt_tokens=True), "inconsistent"),
        (usage_with(prompt_tokens=-1), "inconsistent"),
        (usage_with(prompt_tokens=24.0), "inconsistent"),
        (usage_with(prompt_tokens_details={"cached_tokens": 25}), "inconsistent"),
        (
            usage_with(prompt_tokens_details={"cached_tokens": 20, "cache_write_tokens": 5}),
            "inconsistent",
        ),
        (usage_with(completion_tokens_details={"reasoning_tokens": 41}), "inconsistent"),
        (usage_with(completion_tokens_details="x"), "inconsistent"),
    ],
)
def test_unusable_usage_is_unknown_never_zero(usage, check):
    body = openrouter_body(usage=False)
    if usage is not None:
        body["usage"] = usage
    reading = usage_from_openrouter_body(body)
    assert reading.usage is None and reading.check == check


def test_absent_details_stay_unknown():
    reading = usage_from_openrouter_body(
        openrouter_body(usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7})
    )
    assert reading.usage == TokenUsage(input_tokens=3, output_tokens=4)
    assert reading.usage.cache_read_input_tokens is None


@pytest.mark.parametrize(
    ("changes", "signals"),
    [
        ({}, FreeRoutingSignals("zero", "false")),
        ({"cost": 0.0}, FreeRoutingSignals("zero", "false")),
        ({"cost": 0.000001}, FreeRoutingSignals("nonzero", "false")),
        ({"cost": None}, FreeRoutingSignals("absent", "false")),
        ({"cost": "0"}, FreeRoutingSignals("invalid", "false")),
        ({"cost": False}, FreeRoutingSignals("invalid", "false")),
        ({"cost": -1}, FreeRoutingSignals("invalid", "false")),
        ({"cost": float("nan")}, FreeRoutingSignals("invalid", "false")),
        ({"is_byok": True}, FreeRoutingSignals("zero", "true")),
        ({"is_byok": None}, FreeRoutingSignals("zero", "absent")),
        ({"is_byok": 0}, FreeRoutingSignals("zero", "invalid")),
        ({"is_byok": "false"}, FreeRoutingSignals("zero", "invalid")),
    ],
)
def test_the_free_routing_signals_confirm_only_an_exact_zero_and_false(changes, signals):
    body = openrouter_body(usage=usage_with(**changes))
    assert parse_completion(raw(body), MODEL).signals == signals


def test_the_free_routing_signals_without_usage_are_absent():
    assert parse_completion(raw(openrouter_body(usage=False)), MODEL).signals == (
        FreeRoutingSignals("absent", "absent")
    )


def test_cost_and_byok_are_never_exported(telemetry, stub):
    adapter(
        telemetry, FakeOpenRouter([ok(usage=usage_with(cost=0.25, is_byok=True))])
    ).generate_text(text_request())
    span = one_span(telemetry, stub)
    text = json.dumps(span)
    assert "cost" not in text and "byok" not in text and "0.25" not in text
    assert set(span["attributes"]) <= SPAN_KEYS


def test_an_unusable_response_id_is_withheld_and_counted(telemetry, stub):
    outcome = adapter(
        telemetry, FakeOpenRouter([ok(response_id="bad id with spaces")])
    ).generate_text(text_request())
    assert outcome.response_id is None and outcome.status == "completed"


# ---------------------------------------------------------------- the client boundary
def test_real_provenance_is_refused_while_execution_is_locked(telemetry):
    assert openrouter_adapter.REAL_EXECUTION_ENABLED is False
    with pytest.raises(ConfigError, match="not enabled"):
        ObservedOpenRouterClient(
            telemetry,
            FAKE_OPENROUTER_KEY,
            http_client=build_openrouter_http_client(),
            provenance="real",
            send_gate=FreeModelSession({}),
        )


def test_a_real_client_needs_exactly_the_openrouter_send_gate(telemetry, monkeypatch):
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", True)
    for gate in (None, FreePlanSession({}), object()):
        with pytest.raises(ConfigError, match="send_gate"):
            ObservedOpenRouterClient(
                telemetry,
                FAKE_OPENROUTER_KEY,
                http_client=build_openrouter_http_client(),
                provenance="real",
                send_gate=gate,  # type: ignore[arg-type]
            )


def test_synthetic_provenance_needs_a_mock_transport_and_no_gate(telemetry):
    with pytest.raises(ConfigError, match="MockTransport"):
        ObservedOpenRouterClient(
            telemetry,
            FAKE_OPENROUTER_KEY,
            http_client=build_openrouter_http_client(),
            provenance="synthetic",
        )
    with pytest.raises(ConfigError, match="send_gate"):
        ObservedOpenRouterClient(
            telemetry,
            FAKE_OPENROUTER_KEY,
            http_client=FakeOpenRouter([]).http_client(),
            provenance="synthetic",
            send_gate=FreeModelSession({}),
        )


@pytest.mark.parametrize(
    "http",
    [
        {"base_url": "https://evil.invalid"},
        {"follow_redirects": True},
        {"trust_env": True},
        {"headers": {"X-Title": "x"}},
        {"headers": {"Authorization": "Bearer other"}},
        {"auth": ("u", "p")},
        {"params": {"a": "b"}},
        {"cookies": {"a": "b"}},
        {"event_hooks": {"request": [lambda r: None]}},
    ],
    ids=lambda h: next(iter(h)),
)
def test_a_client_that_could_redirect_the_key_is_refused(telemetry, http):
    with pytest.raises(ConfigError):
        observed(telemetry, FakeOpenRouter([]), **http)


def test_the_built_client_is_the_audited_shape():
    client = build_openrouter_http_client()
    try:
        assert live_openrouter_client_problems(client) == []
    finally:
        client.close()


def test_a_client_with_another_timeout_or_a_proxy_is_refused():
    client = httpx.Client(trust_env=False, follow_redirects=False, timeout=60)
    proxied = httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=openrouter_adapter.OPENROUTER_TIMEOUT,
        proxy="http://127.0.0.1:9",
    )
    try:
        assert any("timeouts" in p for p in live_openrouter_client_problems(client))
        assert live_openrouter_client_problems(proxied)
        assert live_openrouter_client_problems(object()) != []
    finally:
        client.close()
        proxied.close()


@pytest.mark.parametrize("key", ["", "has space", "ключ", None, 7])
def test_the_api_key_must_be_visible_ascii(telemetry, key):
    with pytest.raises(ConfigError, match="api_key"):
        ObservedOpenRouterClient(
            telemetry, key, http_client=FakeOpenRouter([]).http_client(), provenance="synthetic"
        )


def test_failures_hold_codes_only():
    error = OpenRouterCallError("429", "rate_limited", http_status=429, provider_reason="x")
    assert error.__cause__ is None and str(error) == "429"


# ---------------------------------------------------------------- module boundaries
def test_every_category_the_adapter_can_report_is_a_stable_one():
    assert set(openrouter_adapter._STATUS_CATEGORIES.values()) <= ERROR_CATEGORIES
    for category in ("provider_error", "http_error", "malformed_response", "response_failed"):
        assert category in ERROR_CATEGORIES


def test_the_core_sdk_never_imports_this_adapter_or_httpx(tmp_path):
    code = (
        "import sys, llm_insight_sdk, llm_insight_sdk.providers;"
        "print('llm_insight_sdk.openrouter_adapter' in sys.modules, 'httpx' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False"]


def test_the_openrouter_extra_is_exactly_pinned():
    import importlib.metadata

    dist = importlib.metadata.distribution("llm-insight-sdk")
    extras = sorted(r for r in dist.requires or [] if 'extra == "openrouter"' in r)
    assert extras == [
        'certifi==2026.10.19; extra == "openrouter"',
        'httpx==0.28.1; extra == "openrouter"',
    ]


def test_the_adapter_never_reads_error_text_upstream_or_native_finish():
    source = Path(openrouter_adapter.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(("#", '"', "`"))
    )
    for name in ('"native_finish_reason"', '"provider_name"', '"raw"', '"reasoning"', '"provider"'):
        assert f".get({name})" not in code
    # The choice's message object is read; the provider's error message never is.
    assert 'choice.get("message")' in code and 'error.get("message")' not in code
