"""Groq chat completions adapter (Phase 8A-2, ADR-42, ADR-43), fully offline: injected fake httpx
transports, the exported protobuf checked at the local OTLP stub. Every response is a SYNTHETIC
fixture and every span is labelled `synthetic`. No request can leave the machine (`offline`)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import ssl
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from llm_insight_sdk import groq_adapter
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.groq_adapter import (
    ENDPOINT_URL,
    MAX_ERROR_BODY_BYTES,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_TOKENS,
    MAX_RESPONSE_BYTES,
    GroqCallError,
    GroqTextAdapter,
    ObservedGroqClient,
    UnsupportedRequestError,
    build_groq_http_client,
    is_json_content_type,
    live_groq_client_problems,
    usage_from_groq_body,
)
from llm_insight_sdk.groq_live import FreePlanSession
from llm_insight_sdk.instrumentation import TokenUsage
from llm_insight_sdk.providers import ERROR_CATEGORIES, TextGenerationAdapter, TextRequest
from tests.conftest import PROMPT_SENTINEL
from tests.groq_fixtures import (
    ENDPOINT,
    ERROR_SENTINEL,
    FAKE_GROQ_KEY,
    GENERATION_SENTINEL,
    MODEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    RESPONSE_ID,
    USAGE,
    FakeGroq,
    Step,
    error_body,
    fail,
    groq_body,
    ok,
)
from tests.openai_fixtures import block_network
from tests.unit.test_instrumentation import ALLOWED_INFERENCE_KEYS, exported

REPO = Path(__file__).resolve().parents[3]
SECRETS = (
    FAKE_GROQ_KEY,
    PROMPT_SENTINEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    ERROR_SENTINEL,
    GENERATION_SENTINEL,
)
# What must never appear in a span: everything above, and the provider's timing and routing data.
SPAN_KEYS = ALLOWED_INFERENCE_KEYS | {"server.address", "server.port"}


@pytest.fixture(autouse=True)
def offline() -> Iterator[list[str]]:
    attempts: list[str] = []
    with block_network(attempts):
        yield attempts
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def observed(telemetry, fake: FakeGroq, **http) -> ObservedGroqClient:
    return ObservedGroqClient(
        telemetry, FAKE_GROQ_KEY, http_client=fake.http_client(**http), provenance="synthetic"
    )


def adapter(telemetry, fake: FakeGroq) -> GroqTextAdapter:
    return GroqTextAdapter(observed(telemetry, fake))


def text_request(**kw) -> TextRequest:
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


# ---------------------------------------------------------------- success and mapping
def test_a_completed_call_emits_exactly_the_contract_span(telemetry, stub):
    """The same file is ingested by backend/tests/unit/test_groq_contract.py: drift on either
    side fails a test."""
    fixture = json.loads(
        (REPO / "docs" / "fixtures" / "groq_client_span.v1.json").read_text(encoding="utf-8")
    )
    outcome = adapter(telemetry, FakeGroq([ok()])).generate_text(
        text_request(prompt_name="smoke", prompt_version="v1")
    )
    assert outcome.status == "completed" and outcome.error_category is None
    span = one_span(telemetry, stub)
    assert span["name"] == fixture["span_name"]
    assert span["kind"] == fixture["span_kind"]
    assert span["status"] == "UNSET" and span["events"] == 0
    a = dict(span["attributes"])
    assert uuid.UUID(a.pop("llminsight.call_id")).version == 4
    expected = dict(fixture["attributes"])
    del expected["llminsight.call_id"]
    assert a == expected
    assert set(span["attributes"]) <= SPAN_KEYS


def test_the_outcome_holds_the_provider_neutral_metadata(telemetry):
    outcome = adapter(telemetry, FakeGroq([ok()])).generate_text(text_request())
    assert outcome.provider == "groq"
    assert (outcome.request_model, outcome.response_model) == (MODEL, MODEL)
    assert outcome.response_id == RESPONSE_ID
    assert outcome.service_tier == "on_demand"
    assert outcome.usage == TokenUsage(input_tokens=20, output_tokens=32)
    assert outcome.duration_ms >= 0
    (attempt,) = outcome.attempts
    assert attempt.attempt == 1 and attempt.usage == outcome.usage
    assert attempt.response_model == MODEL and attempt.error_category is None
    assert outcome.provider_response.text == OUTPUT_SENTINEL


def test_the_adapter_implements_the_provider_protocol(telemetry):
    implementation: TextGenerationAdapter = adapter(telemetry, FakeGroq([]))
    assert implementation.provider == "groq"


def test_the_client_span_is_a_child_of_the_current_span(telemetry, stub):
    with telemetry.tracer.start_as_current_span("demo.request") as root:
        adapter(telemetry, FakeGroq([ok()])).generate_text(text_request())
    spans = exported(telemetry, stub)
    (span,) = client_spans(spans)
    (parent,) = [s for s in spans if s["name"] == "demo.request"]
    assert (
        span["trace_id"] == parent["trace_id"] == format(root.get_span_context().trace_id, "032x")
    )
    assert span["parent_span_id"] == parent["span_id"]


def test_exactly_one_minimal_request_reaches_the_pinned_endpoint(telemetry):
    fake = FakeGroq([ok()])
    adapter(telemetry, fake).generate_text(text_request())
    (req,) = fake.requests
    assert (req.method, str(req.url)) == ("POST", ENDPOINT) and ENDPOINT == ENDPOINT_URL
    assert fake.request_json(0) == {
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT_SENTINEL}],
        "max_completion_tokens": 64,
        "stream": False,
        "include_reasoning": False,
    }
    assert req.headers["authorization"] == f"Bearer {FAKE_GROQ_KEY}"
    assert {name.lower() for name in req.headers} == {
        "accept",
        "accept-encoding",
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "host",
        "user-agent",
    }
    assert req.headers["accept-encoding"] == "identity"
    assert "traceparent" not in req.headers


def test_every_request_carries_the_bounded_timeouts(telemetry):
    fake = FakeGroq([ok()])
    adapter(telemetry, fake).generate_text(text_request())
    assert fake.requests[0].extensions["timeout"] == {
        "connect": 5.0,
        "read": 20.0,
        "write": 5.0,
        "pool": 1.0,
    }


# ---------------------------------------------------------------- usage
@pytest.mark.parametrize(
    ("usage", "check", "expected"),
    [
        ({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}, "consistent", (2, 3)),
        ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "consistent", (0, 0)),
        (None, "missing", None),
        ({"completion_tokens": 3, "total_tokens": 5}, "missing", None),
        ({"prompt_tokens": 2, "total_tokens": 5}, "missing", None),
        ({"prompt_tokens": 2, "completion_tokens": 3}, "missing", None),
        ({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 6}, "inconsistent", None),
        ({"prompt_tokens": "2", "completion_tokens": 3, "total_tokens": 5}, "inconsistent", None),
        ({"prompt_tokens": 2.0, "completion_tokens": 3, "total_tokens": 5}, "inconsistent", None),
        ({"prompt_tokens": True, "completion_tokens": 3, "total_tokens": 4}, "inconsistent", None),
        ({"prompt_tokens": -1, "completion_tokens": 3, "total_tokens": 2}, "inconsistent", None),
        (
            {"prompt_tokens": 2**31, "completion_tokens": 0, "total_tokens": 2**31},
            "inconsistent",
            None,
        ),
        ("not-an-object", "inconsistent", None),
        ([1, 2, 3], "inconsistent", None),
    ],
)
def test_usage_is_read_or_left_unknown(usage, check, expected):
    reading = usage_from_groq_body({} if usage is None else {"usage": usage})
    assert reading.check == check
    if expected is None:
        assert reading.usage is None
    else:
        assert (reading.usage.input_tokens, reading.usage.output_tokens) == expected
        assert reading.usage.source == "provider_reported"


BASE = {"prompt_tokens": 20, "completion_tokens": 32, "total_tokens": 52}


def test_undocumented_detail_counts_are_subsets_and_absent_ones_stay_none():
    reading = usage_from_groq_body(
        {
            "usage": {
                **BASE,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 30},
            }
        }
    )
    assert reading.usage == TokenUsage(
        input_tokens=20, output_tokens=32, cache_read_input_tokens=4, reasoning_output_tokens=30
    )
    absent = usage_from_groq_body({"usage": BASE}).usage
    assert absent.cache_read_input_tokens is None and absent.reasoning_output_tokens is None


@pytest.mark.parametrize(
    "extra",
    [
        {"prompt_tokens_details": {"cached_tokens": 21}},
        {"completion_tokens_details": {"reasoning_tokens": 33}},
        {"prompt_tokens_details": {"cached_tokens": "4"}},
        {"completion_tokens_details": {"reasoning_tokens": True}},
        {"prompt_tokens_details": "4"},
        {"usage_breakdown": {"models": [{"model": "a"}, {"model": "b"}]}},
    ],
)
def test_unusable_detail_counts_make_the_whole_usage_unknown(extra):
    reading = usage_from_groq_body({"usage": {**BASE, **extra}})
    assert (reading.usage, reading.check) == (None, "inconsistent")


def test_a_top_level_multi_model_breakdown_makes_the_usage_unknown():
    body = {"usage": BASE, "usage_breakdown": {"models": [{"model": "a"}]}}
    assert usage_from_groq_body(body).usage is None


@pytest.mark.parametrize(
    "usage", [None, {"prompt_tokens": 2}, {**USAGE, "total_tokens": 999}, "bad"]
)
def test_unknown_usage_is_never_invented_on_the_span(telemetry, stub, usage):
    fake = FakeGroq([Step(200, groq_body(usage=False) | ({"usage": usage} if usage else {}))])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert outcome.status == "completed" and outcome.usage is None
    assert outcome.attempts[0].usage is None
    a = one_span(telemetry, stub)["attributes"]
    assert a["llminsight.usage_source"] == "unknown"
    assert not [k for k in a if k.startswith("gen_ai.usage.")]


# ---------------------------------------------------------------- outcomes
@pytest.mark.parametrize(
    ("step", "status", "category", "reason", "span_status", "error_type", "finish"),
    [
        (ok(finish="length"), "incomplete", None, "length", "UNSET", None, ["length"]),
        (
            ok(finish="length", content=None),
            "incomplete",
            None,
            "length",
            "UNSET",
            None,
            ["length"],
        ),
        (
            ok(finish="content_filter"),
            "blocked",
            "content_blocked",
            "content_filter",
            "UNSET",
            None,
            ["content_filter"],
        ),
        (
            ok(content=""),
            "failed",
            "empty_response",
            "stop",
            "ERROR",
            "groq.response.empty",
            ["stop"],
        ),
        (
            ok(content="   "),
            "failed",
            "empty_response",
            "stop",
            "ERROR",
            "groq.response.empty",
            ["stop"],
        ),
        (
            ok(content=None),
            "failed",
            "empty_response",
            "stop",
            "ERROR",
            "groq.response.empty",
            ["stop"],
        ),
        (
            ok(finish="error"),
            "failed",
            "response_failed",
            "error",
            "ERROR",
            "groq.response.failed",
            ["error"],
        ),
        (
            ok(finish=None),
            "failed",
            "unexpected_status",
            None,
            "ERROR",
            "groq.response.unexpected_finish",
            None,
        ),
        (
            ok(finish="tool_calls"),
            "failed",
            "unexpected_status",
            "tool_calls",
            "ERROR",
            "groq.response.unexpected_finish",
            ["tool_calls"],
        ),
        (
            ok(finish="brand_new_reason"),
            "failed",
            "unexpected_status",
            "brand_new_reason",
            "ERROR",
            "groq.response.unexpected_finish",
            ["brand_new_reason"],
        ),
    ],
)
def test_a_returned_response_maps_to_its_outcome(
    telemetry, stub, step, status, category, reason, span_status, error_type, finish
):
    fake = FakeGroq([step])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == (status, category)
    assert outcome.provider_reason == reason
    assert outcome.incomplete_reason == (reason if status == "incomplete" else None)
    assert len(fake.requests) == 1 and len(outcome.attempts) == 1
    span = one_span(telemetry, stub)
    assert (span["status"], span["attributes"].get("error.type")) == (span_status, error_type)
    assert span["attributes"].get("gen_ai.response.finish_reasons") == finish
    assert_no_secrets(span, repr(outcome))


def test_length_is_never_continued_automatically(telemetry):
    fake = FakeGroq([ok(finish="length")])  # a second request would raise in the fake
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert outcome.status == "incomplete" and len(fake.requests) == 1


def test_empty_choices_are_a_failure_and_keep_reported_usage(telemetry, stub):
    outcome = adapter(
        telemetry, FakeGroq([Step(200, groq_body() | {"choices": []})])
    ).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("failed", "empty_response")
    assert outcome.usage == TokenUsage(input_tokens=20, output_tokens=32)
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "groq.response.empty"


@pytest.mark.parametrize("finish", ["stop", "length", "content_filter"])
def test_a_different_response_model_is_a_failure_and_stays_visible(telemetry, stub, finish):
    other = "openai/gpt-oss-120b"
    outcome = adapter(telemetry, FakeGroq([ok(model=other, finish=finish)])).generate_text(
        text_request()
    )
    assert (outcome.status, outcome.error_category) == ("failed", "unexpected_status")
    assert (outcome.request_model, outcome.response_model) == (MODEL, other)
    a = one_span(telemetry, stub)["attributes"]
    assert (a["gen_ai.request.model"], a["gen_ai.response.model"]) == (MODEL, other)
    assert a["error.type"] == "groq.response.model_mismatch"


@pytest.mark.parametrize("response_id", [None, "", "has space", "x" * 257, 123])
def test_a_missing_or_invalid_response_id_is_never_invented(telemetry, stub, response_id):
    body = groq_body(response_id=None)
    if response_id is not None:
        body["id"] = response_id
    outcome = adapter(telemetry, FakeGroq([Step(200, body)])).generate_text(text_request())
    assert outcome.status == "completed" and outcome.response_id is None
    assert "gen_ai.response.id" not in one_span(telemetry, stub)["attributes"]


def test_a_credential_shaped_response_id_is_withheld_and_counted(telemetry, stub):
    leaked = "gsk" + "_" + "Ab3" * 18
    fake = FakeGroq([ok(response_id=leaked)])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert outcome.response_id is None
    assert telemetry.stats().attributes_withheld == 1
    assert leaked not in json.dumps(exported(telemetry, stub))
    assert leaked not in repr(outcome)


# ---------------------------------------------------------------- HTTP failures
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
        (424, "http_error"),
        (429, "rate_limited"),
        (498, "provider_error"),
        (499, "http_error"),
        (500, "provider_error"),
        (502, "provider_error"),
        (503, "provider_error"),
        (504, "provider_error"),
        (206, "http_error"),
    ],
)
def test_an_http_failure_is_an_error_outcome_and_is_never_retried(
    telemetry, stub, status, category, caplog
):
    fake = FakeGroq([fail(status)])  # a second request would raise in the fake
    with caplog.at_level(logging.DEBUG):
        outcome = adapter(telemetry, fake).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", category)
    assert category in ERROR_CATEGORIES
    assert len(fake.requests) == 1
    assert outcome.usage is None and outcome.response_model is None and outcome.response_id is None
    assert outcome.provider_response is None
    (attempt,) = outcome.attempts
    assert (attempt.attempt, attempt.error_category, attempt.usage) == (1, category, None)
    span = one_span(telemetry, stub)
    assert (span["status"], span["status_message"]) == ("ERROR", "")
    a = span["attributes"]
    assert a["error.type"] == str(status) and a["llminsight.usage_source"] == "unknown"
    assert_no_secrets(span, repr(outcome), caplog.text)


def test_blocked_api_access_is_a_visible_precondition_failure(telemetry):
    fake = FakeGroq([fail(400, "invalid_request_error", code="blocked_api_access")])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert outcome.error_category == "precondition_failed"
    assert outcome.provider_reason == "blocked_api_access"


def test_only_a_validated_keyword_of_an_error_is_kept(telemetry):
    bad = "not a keyword: " + ERROR_SENTINEL
    outcome = adapter(telemetry, FakeGroq([Step(400, error_body(bad))])).generate_text(
        text_request()
    )
    assert outcome.provider_reason is None and outcome.error_category == "invalid_request"
    assert_no_secrets(repr(outcome))


@pytest.mark.parametrize(
    "step",
    [
        Step(429, raw=b"not json", content_type="text/plain"),
        Step(500, raw=b""),
        Step(429, ["not", "an", "object"]),
        Step(429, {"error": "just a string"}),
        Step(429, raw=b"x" * (MAX_ERROR_BODY_BYTES + 1)),
    ],
)
def test_an_unreadable_error_body_still_reports_the_status(telemetry, step):
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert outcome.status == "error" and outcome.provider_reason is None
    assert outcome.error_category in {"rate_limited", "provider_error"}


@pytest.mark.parametrize(
    ("exc", "category", "error_type"),
    [
        (httpx.ConnectTimeout, "timeout", "timeout"),
        (httpx.ReadTimeout, "timeout", "timeout"),
        (httpx.WriteTimeout, "timeout", "timeout"),
        (httpx.PoolTimeout, "timeout", "timeout"),
        (httpx.ConnectError, "connection_error", "httpx.ConnectError"),
        (httpx.ReadError, "connection_error", "httpx.ReadError"),
        (httpx.RemoteProtocolError, "connection_error", "httpx.RemoteProtocolError"),
    ],
)
def test_an_ambiguous_network_failure_is_never_retried(telemetry, stub, exc, category, error_type):
    fake = FakeGroq([Step(raise_=exc)])  # a second request would raise in the fake
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", category)
    assert len(fake.requests) == 1 and len(outcome.attempts) == 1
    assert outcome.usage is None
    assert one_span(telemetry, stub)["attributes"]["error.type"] == error_type


@pytest.mark.parametrize(
    ("exc", "error_type"),
    [(KeyboardInterrupt, "KeyboardInterrupt"), (asyncio.CancelledError, "cancelled")],
)
def test_cancellation_propagates_ends_the_span_and_is_not_retried(telemetry, stub, exc, error_type):
    fake = FakeGroq([Step(raise_=exc)])
    with pytest.raises(exc):
        adapter(telemetry, fake).generate_text(text_request())
    assert len(fake.requests) == 1
    span = one_span(telemetry, stub)
    assert (span["status"], span["attributes"]["error.type"]) == ("ERROR", error_type)
    assert span["events"] == 0


def test_an_unexpected_internal_error_is_an_outcome_without_its_message(telemetry, stub):
    class Boom(RuntimeError):
        def __init__(self) -> None:
            super().__init__(PROMPT_SENTINEL)

    outcome = adapter(telemetry, FakeGroq([Step(raise_=Boom)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "unexpected_error")
    assert_no_secrets(one_span(telemetry, stub), repr(outcome))


# ---------------------------------------------------------------- malformed, truncated, oversized
@pytest.mark.parametrize(
    "step",
    [
        pytest.param(Step(raw=b"{not json"), id="not-json"),
        pytest.param(Step(raw=b""), id="empty"),
        pytest.param(Step(raw=json.dumps(groq_body()).encode()[:40]), id="truncated"),
        pytest.param(Step(raw=b"[" * 200_000), id="deeply-nested"),
        pytest.param(Step(200, ["a", "list"]), id="list"),
        pytest.param(Step(200, "text"), id="string"),
        pytest.param(Step(200, groq_body(object="chat.completion.chunk")), id="stream-object"),
        pytest.param(
            Step(200, {k: v for k, v in groq_body().items() if k != "object"}), id="no-object"
        ),
        pytest.param(ok(model=None), id="no-model"),
        pytest.param(Step(200, groq_body() | {"model": 7}), id="model-not-string"),
        pytest.param(ok(model="bad model with spaces"), id="model-invalid"),
        pytest.param(ok(model="gsk" + "_" + "Ab3" * 18), id="model-credential"),
        pytest.param(Step(200, groq_body() | {"choices": "none"}), id="choices-not-list"),
        pytest.param(Step(200, groq_body() | {"choices": [1]}), id="choice-not-object"),
        pytest.param(Step(200, groq_body() | {"choices": [{}]}), id="choice-no-message"),
        pytest.param(
            Step(200, groq_body() | {"choices": groq_body()["choices"] * 2}), id="two-choices"
        ),
        pytest.param(
            Step(200, groq_body() | {"choices": [{"message": {"content": 5}}]}),
            id="content-not-string",
        ),
        pytest.param(
            Step(200, groq_body() | {"choices": [{"message": {"role": "user", "content": "x"}}]}),
            id="wrong-role",
        ),
        pytest.param(
            Step(
                200,
                groq_body() | {"choices": [{"message": {"content": "x"}, "finish_reason": 3}]},
            ),
            id="finish-not-string",
        ),
    ],
)
def test_an_unusable_200_is_malformed_with_no_invented_metadata(telemetry, stub, step):
    fake = FakeGroq([step])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "malformed_response")
    assert outcome.response_id is None and outcome.response_model is None and outcome.usage is None
    assert len(fake.requests) == 1
    span = one_span(telemetry, stub)
    assert span["attributes"]["error.type"] == "groq.response.malformed"
    assert span["attributes"]["llminsight.usage_source"] == "unknown"
    assert_no_secrets(span, repr(outcome))


VALID_COMPLETION = json.dumps(groq_body()).encode()


def assert_refused_for_its_content_type(outcome, stub_span) -> None:
    """Nothing of the otherwise valid body was parsed or recorded."""
    assert (outcome.status, outcome.error_category) == ("error", "malformed_response")
    assert outcome.provider_response is None and outcome.usage is None
    assert outcome.response_id is None and outcome.response_model is None
    a = stub_span["attributes"]
    assert a["error.type"] == "groq.response.malformed"
    assert "gen_ai.response.finish_reasons" not in a and "gen_ai.response.id" not in a


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", "APPLICATION/JSON"],
)
def test_the_valid_body_used_below_completes_with_a_json_content_type(telemetry, content_type):
    """The control for the refusals below: only the header differs between them."""
    step = Step(raw=VALID_COMPLETION, content_type=content_type)
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert outcome.status == "completed"


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "text/plain", "application/xml", "application/octet-stream", ""],
)
def test_valid_json_with_a_wrong_content_type_is_refused(telemetry, stub, content_type):
    fake = FakeGroq([Step(raw=VALID_COMPLETION, content_type=content_type)])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert_refused_for_its_content_type(outcome, one_span(telemetry, stub))
    assert len(fake.requests) == 1


ACCEPTED_MEDIA_TYPES = [
    pytest.param("application/json", id="plain"),
    pytest.param("APPLICATION/JSON", id="upper-case"),
    pytest.param("Application/Json", id="mixed-case"),
    pytest.param("application/json; charset=utf-8", id="charset"),
    pytest.param("application/json;charset=UTF-8", id="charset-upper-no-space"),
    pytest.param('application/json; Charset="utf-8"', id="quoted-charset-mixed-name"),
    pytest.param("application/json ; charset=utf-8", id="space-before-semicolon"),
    pytest.param("application/json\t;\tcharset=utf-8", id="tabs"),
    pytest.param("application/json;", id="empty-parameter"),
    pytest.param("application/json; ; charset=utf-8", id="empty-then-charset"),
    pytest.param("application/json; profile=groq", id="other-parameter"),
    pytest.param('application/json; note="a;b, c"', id="quoted-delimiters"),
    pytest.param('application/json; note="say \\"hi\\""', id="quoted-escapes"),
    pytest.param('application/json; note="charset=latin1"', id="charset-only-inside-quotes"),
    pytest.param("application/json; charset=utf-8; profile=groq", id="charset-and-other"),
]
REJECTED_MEDIA_TYPES = [
    pytest.param("application/x-not-json", id="x-not-json"),
    pytest.param("application/jsonp", id="json-prefix"),
    pytest.param("application/json5", id="json-digit"),
    pytest.param("application/json-patch+json", id="json-patch"),
    pytest.param("application/vnd.api+json", id="plus-json"),
    pytest.param("application/problem+json", id="problem-json"),
    pytest.param("text/json", id="text-json"),
    pytest.param("text/plain", id="text-plain"),
    pytest.param("text/plain; note=json", id="json-in-parameter"),
    pytest.param("text/html; charset=utf-8", id="html"),
    pytest.param('application/xml; note="application/json"', id="json-in-quotes"),
    pytest.param("json", id="bare-word"),
    pytest.param("application", id="type-only"),
    pytest.param("application/", id="empty-subtype"),
    pytest.param("/json", id="empty-type"),
    pytest.param("application/json/extra", id="extra-path"),
    pytest.param("application/ json", id="space-after-slash"),
    pytest.param("application /json", id="space-before-slash"),
    pytest.param(" application/json", id="leading-space"),
    pytest.param("application/json charset=utf-8", id="missing-semicolon"),
    pytest.param("application/json; charset", id="parameter-without-value"),
    pytest.param("application/json; charset=", id="empty-value"),
    pytest.param("application/json; =utf-8", id="empty-name"),
    pytest.param("application/json; charset = utf-8", id="spaces-around-equals"),
    pytest.param('application/json; charset="utf-8', id="unterminated-quote"),
    pytest.param("application/json; a=b c", id="unquoted-space"),
    pytest.param("application/json; a=b,c", id="unquoted-comma"),
    pytest.param("application/json, text/html", id="comma-joined-values"),
    pytest.param("application/json; charset=iso-8859-1", id="latin1"),
    pytest.param("application/json; Charset=iso-8859-1", id="latin1-mixed-name"),
    pytest.param("application/json; charset=utf-16", id="utf-16"),
    pytest.param("application/json; charset=utf8", id="unregistered-utf8"),
    pytest.param('application/json; charset=""', id="empty-quoted-charset"),
    pytest.param("application/json; charset=utf-8; charset=utf-8", id="repeated-charset"),
    pytest.param("application/json; charset=utf-8; charset=latin1", id="conflicting-charset"),
    pytest.param("application/jſon", id="unicode-case-folding-s"),
    pytest.param("application/json; a=" + "x" * 300, id="too-long"),
    pytest.param("", id="empty"),
    pytest.param(" ", id="blank"),
]


@pytest.mark.parametrize("value", ACCEPTED_MEDIA_TYPES)
def test_a_json_media_type_is_accepted(value):
    assert is_json_content_type(value) is True


@pytest.mark.parametrize("value", REJECTED_MEDIA_TYPES)
def test_any_other_media_type_is_rejected(value):
    assert is_json_content_type(value) is False


def test_a_pathological_content_type_is_rejected_quickly():
    """A bounded length and a pattern with one parse: no catastrophic backtracking."""
    started = time.monotonic()
    for value in ("application/json" + "; " * 119 + "!", "application/json" + " " * 239 + "!"):
        assert len(value) <= 256 and is_json_content_type(value) is False
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize(
    "content_type",
    [
        "application/x-not-json",
        "application/jsonp",
        "text/plain; note=json",
        "application/json; charset=iso-8859-1",
    ],
)
def test_a_misleading_content_type_stops_a_valid_body_at_the_adapter(telemetry, stub, content_type):
    fake = FakeGroq([Step(raw=VALID_COMPLETION, content_type=content_type)])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert_refused_for_its_content_type(outcome, one_span(telemetry, stub))


@pytest.mark.parametrize(
    "content_type", ['application/json; Charset="utf-8"', "application/json;", "Application/JSON"]
)
def test_a_valid_media_type_reaches_the_parser(telemetry, content_type):
    step = Step(raw=VALID_COMPLETION, content_type=content_type)
    assert adapter(telemetry, FakeGroq([step])).generate_text(text_request()).status == "completed"


def test_the_content_type_is_checked_before_the_body_is_read(telemetry, stub):
    stream = CountingStream(VALID_COMPLETION, 3)
    fake = FakeGroq([Step(stream=stream, content_type="application/x-not-json")])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert_refused_for_its_content_type(outcome, one_span(telemetry, stub))
    assert stream.served == 0 and stream.closed


def test_a_misleading_content_type_keeps_the_body_out_of_every_output(telemetry, stub, caplog):
    body = json.dumps(groq_body(content=OUTPUT_SENTINEL)).encode()
    fake = FakeGroq([Step(raw=body, content_type="application/x-not-json")])
    with caplog.at_level(logging.DEBUG):
        outcome = adapter(telemetry, fake).generate_text(text_request())
    assert_no_secrets(one_span(telemetry, stub), repr(outcome), caplog.text)
    assert FAKE_GROQ_KEY not in json.dumps(dataclasses.asdict(outcome), default=repr)


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
@pytest.mark.parametrize("content_type", ["text/html", "application/x-not-json", ""])
def test_a_non_2xx_response_keeps_its_status_whatever_its_content_type(
    telemetry, stub, status, content_type
):
    """Error handling never depends on the media type, and the error text is still not read."""
    step = Step(status, error_body(), content_type=content_type)
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert outcome.status == "error"
    assert outcome.error_category == groq_adapter.status_category(status)
    span = one_span(telemetry, stub)
    assert span["attributes"]["error.type"] == str(status)
    assert_no_secrets(span, repr(outcome))


def test_several_content_type_headers_are_refused(telemetry, stub):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=VALID_COMPLETION,
            headers=[("content-type", "application/json"), ("content-type", "text/html")],
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
    )
    llm = ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="synthetic")
    outcome = GroqTextAdapter(llm).generate_text(text_request())
    assert_refused_for_its_content_type(outcome, one_span(telemetry, stub))


def test_valid_json_with_no_content_type_is_refused(telemetry, stub):
    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, content=VALID_COMPLETION)
        assert "content-type" not in response.headers
        return response

    http = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
    )
    llm = ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="synthetic")
    outcome = GroqTextAdapter(llm).generate_text(text_request())
    assert_refused_for_its_content_type(outcome, one_span(telemetry, stub))


class CountingStream(httpx.SyncByteStream):
    """A body far larger than the limit. It records how many chunks were actually read."""

    def __init__(self, chunk: bytes, chunks: int) -> None:
        self.chunk, self.total, self.served, self.closed = chunk, chunks, 0, False

    def __iter__(self):
        for _ in range(self.total):
            self.served += 1
            yield self.chunk

    def close(self) -> None:
        self.closed = True


def test_a_compressed_body_is_refused_without_decoding_it(telemetry, stub):
    stream = CountingStream(b"{}", 3)
    step = Step(stream=stream, headers={"content-encoding": "gzip"})
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "malformed_response")
    assert stream.served == 0 and stream.closed
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "groq.response.malformed"


def test_an_oversized_stream_stops_being_read_at_the_limit(telemetry, stub):
    size = 64 * 1024
    stream = CountingStream(b"x" * size, 10_000)
    outcome = adapter(telemetry, FakeGroq([Step(stream=stream)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "malformed_response")
    assert stream.served <= MAX_RESPONSE_BYTES // size + 1 and stream.closed
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "groq.response.oversized"


def test_a_declared_oversized_body_is_refused_without_reading_it(telemetry, stub):
    stream = CountingStream(b"{}", 3)
    step = Step(stream=stream, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)})
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert outcome.error_category == "malformed_response" and stream.served == 0
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "groq.response.oversized"


@pytest.mark.parametrize("declared", ["abc", "-1", "1e9"])
def test_an_invalid_content_length_is_refused(telemetry, declared):
    step = Step(stream=CountingStream(b"{}", 1), headers={"content-length": declared})
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert outcome.error_category == "malformed_response"


def test_a_body_at_the_limit_is_still_read(telemetry):
    padded = json.dumps(groq_body())[:-1] + ', "padding": "'
    body = (padded + " " * (MAX_RESPONSE_BYTES - len(padded) - 2) + '"}').encode()
    assert len(body) == MAX_RESPONSE_BYTES
    outcome = adapter(telemetry, FakeGroq([Step(raw=body)])).generate_text(text_request())
    assert outcome.status == "completed"


def test_a_slowly_dripped_body_hits_the_total_deadline(telemetry, stub, monkeypatch):
    monkeypatch.setattr(groq_adapter, "TOTAL_DEADLINE_SECONDS", -1.0)
    step = Step(stream=CountingStream(b"{}", 5))
    outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "timeout")
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "timeout"


def test_an_error_object_in_a_200_is_a_failure_even_with_choices(telemetry, stub):
    body = groq_body() | error_body("server_error")
    outcome = adapter(telemetry, FakeGroq([Step(200, body)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "provider_error")
    assert outcome.provider_reason == "server_error" and outcome.provider_response is None
    assert outcome.usage is None
    span = one_span(telemetry, stub)
    assert span["attributes"]["error.type"] == "groq.response.error"
    assert_no_secrets(span, repr(outcome))


def test_a_200_blocked_api_access_is_a_precondition_failure(telemetry):
    body = error_body("invalid_request_error", code="blocked_api_access")
    outcome = adapter(telemetry, FakeGroq([Step(200, body)])).generate_text(text_request())
    assert outcome.error_category == "precondition_failed"


# ---------------------------------------------------------------- redirects
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_refused_and_the_key_does_not_travel(telemetry, stub, status):
    step = Step(status, {}, headers={"location": "https://collector.example.invalid/steal"})
    fake = FakeGroq([step])  # following it would be a second request, which raises in the fake
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "http_error")
    assert [str(r.url) for r in fake.requests] == [ENDPOINT]
    assert one_span(telemetry, stub)["attributes"]["error.type"] == str(status)


# ---------------------------------------------------------------- unexpected provider fields
def test_unexpected_provider_fields_are_ignored_and_never_exported(telemetry, stub):
    body = groq_body(
        brand_new_field={"nested": OUTPUT_SENTINEL},
        x_groq={"id": ERROR_SENTINEL, "usage": {"queue_time": 1}},
        system_fingerprint=ERROR_SENTINEL,
    )
    body["choices"][0]["message"]["extra"] = REASONING_SENTINEL
    outcome = adapter(telemetry, FakeGroq([Step(200, body)])).generate_text(text_request())
    assert outcome.status == "completed"
    span = one_span(telemetry, stub)
    assert set(span["attributes"]) <= SPAN_KEYS
    assert_no_secrets(span, repr(outcome))


def test_a_tool_call_is_not_an_answer(telemetry):
    body = groq_body()
    body["choices"][0]["message"]["tool_calls"] = [{"id": "c", "type": "function"}]
    outcome = adapter(telemetry, FakeGroq([Step(200, body)])).generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("failed", "unexpected_status")


def test_an_invalid_service_tier_is_dropped_from_the_outcome(telemetry):
    outcome = adapter(telemetry, FakeGroq([ok(service_tier="not a keyword!")])).generate_text(
        text_request()
    )
    assert outcome.service_tier is None and outcome.status == "completed"


def test_the_service_tier_is_not_exported_under_a_provider_namespace(telemetry, stub):
    adapter(telemetry, FakeGroq([ok()])).generate_text(text_request())
    assert not [k for k in one_span(telemetry, stub)["attributes"] if "service_tier" in k]


# ---------------------------------------------------------------- privacy
@pytest.mark.parametrize(
    "step",
    [
        pytest.param(ok(), id="completed"),
        pytest.param(ok(finish="length"), id="incomplete"),
        pytest.param(fail(400), id="http-400"),
        pytest.param(fail(429), id="http-429"),
        pytest.param(Step(raise_=httpx.ReadTimeout), id="timeout"),
        pytest.param(Step(raw=b"{bad " + ERROR_SENTINEL.encode()), id="malformed"),
    ],
)
def test_content_and_the_key_never_leave_the_outcome_boundary(telemetry, stub, caplog, step):
    with caplog.at_level(logging.DEBUG):
        outcome = adapter(telemetry, FakeGroq([step])).generate_text(text_request())
    assert telemetry.force_flush(5000)
    wire = b"".join(r.raw_body + r.body for r in stub.received)
    serialized = json.dumps(dataclasses.asdict(outcome), default=repr)
    assert_no_secrets(stub.spans(), repr(outcome), serialized, caplog.text)
    for secret in SECRETS:
        assert secret.encode() not in wire


def test_the_key_and_prompt_are_absent_from_every_exception_detail(telemetry):
    llm = observed(telemetry, FakeGroq([Step(raise_=httpx.ConnectError)]))
    with pytest.raises(GroqCallError) as caught:
        llm.create_chat_completion(model=MODEL, input_text=PROMPT_SENTINEL, max_completion_tokens=8)
    exc = caught.value
    assert exc.__cause__ is None and exc.__context__ is None
    assert_no_secrets(str(exc), repr(exc), vars(exc))


def test_a_malformed_body_is_not_kept_on_the_exception(telemetry):
    llm = observed(telemetry, FakeGroq([Step(raw=b"{bad " + ERROR_SENTINEL.encode())]))
    with pytest.raises(GroqCallError) as caught:
        llm.create_chat_completion(model=MODEL, input_text=PROMPT_SENTINEL, max_completion_tokens=8)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_the_completion_and_client_reprs_hide_content_and_the_key(telemetry):
    fake = FakeGroq([ok()])
    llm = observed(telemetry, fake)
    completion = llm.create_chat_completion(
        model=MODEL, input_text=PROMPT_SENTINEL, max_completion_tokens=8
    )
    assert completion.text == OUTPUT_SENTINEL
    assert_no_secrets(repr(completion), repr(llm), str(completion))


def test_a_refusal_never_quotes_the_input(telemetry):
    with pytest.raises(UnsupportedRequestError) as caught:
        observed(telemetry, FakeGroq([])).create_chat_completion(
            model=MODEL, input_text=PROMPT_SENTINEL + "\ud800", max_completion_tokens=8
        )
    assert_no_secrets(str(caught.value))
    assert caught.value.__context__ is None


def test_telemetry_failure_does_not_change_the_call(telemetry, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("telemetry down")

    monkeypatch.setattr(groq_adapter.instrumentation, "inference_span", broken)
    fake = FakeGroq([ok()])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert outcome.status == "completed" and len(fake.requests) == 1


def test_an_observer_error_does_not_change_the_call(telemetry):
    def observer(attempt, outcome):
        raise RuntimeError("observer failed")

    completion = observed(telemetry, FakeGroq([ok()])).create_chat_completion(
        model=MODEL,
        input_text=PROMPT_SENTINEL,
        max_completion_tokens=8,
        attempt_observer=observer,
    )
    assert completion.verdict.status == "completed"


# ---------------------------------------------------------------- refused before sending
@pytest.mark.parametrize(
    "name",
    [
        "tools",
        "tool_choice",
        "functions",
        "function_call",
        "parallel_tool_calls",
        "stream",
        "stream_options",
        "response_format",
        "temperature",
        "top_p",
        "n",
        "stop",
        "seed",
        "user",
        "metadata",
        "store",
        "service_tier",
        "reasoning_format",
        "reasoning_effort",
        "include_reasoning",
        "extra_body",
        "extra_headers",
        "extra_query",
        "base_url",
        "messages",
        "continuation",
    ],
)
def test_every_unsupported_parameter_is_refused_before_sending(telemetry, stub, name):
    fake = FakeGroq([])
    with pytest.raises(UnsupportedRequestError, match=name):
        observed(telemetry, fake).create_chat_completion(
            model=MODEL, input_text=PROMPT_SENTINEL, max_completion_tokens=8, **{name: True}
        )
    assert fake.requests == []
    assert client_spans(exported(telemetry, stub)) == []


@pytest.mark.parametrize(
    "model",
    [
        "",
        " ",
        None,
        5,
        "openai/gpt-oss-20b:free",
        "a/b/c",
        "../v1/models",
        "model?x=1",
        "model#x",
        "has space",
        "x" * 129,
        "groq/compound",
        "groq/compound-mini",
        "GROQ/COMPOUND",
        "compound-beta",
        "gsk" + "_" + "Ab3" * 18,
    ],
)
def test_an_invalid_or_implicit_model_is_refused(telemetry, model):
    fake = FakeGroq([])
    with pytest.raises(UnsupportedRequestError, match="model|compound"):
        adapter(telemetry, fake).generate_text(text_request(model=model))
    assert fake.requests == []


@pytest.mark.parametrize(
    "model", ["openai/gpt-oss-20b", "llama-3.1-8b-instant", "qwen/qwen3.8-27b", "A.b_c-d/e"]
)
def test_a_plain_model_id_is_sent_exactly_as_given(telemetry, model):
    fake = FakeGroq([ok(model=model)])
    adapter(telemetry, fake).generate_text(text_request(model=model))
    assert fake.request_json(0)["model"] == model


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param(None, id="none"),
        pytest.param(5, id="number"),
        pytest.param(["a"], id="list"),
        pytest.param("x" * (MAX_INPUT_BYTES + 1), id="too-long"),
        pytest.param("\ud800", id="lone-surrogate"),
        pytest.param("é" * MAX_INPUT_BYTES, id="too-many-bytes"),
    ],
)
def test_invalid_input_is_refused(telemetry, text):
    fake = FakeGroq([])
    with pytest.raises(UnsupportedRequestError, match="input_text"):
        adapter(telemetry, fake).generate_text(text_request(input_text=text))
    assert fake.requests == []


@pytest.mark.parametrize("tokens", [0, -1, MAX_OUTPUT_TOKENS + 1, True, "8", 8.0, None])
def test_an_invalid_output_limit_is_refused(telemetry, tokens):
    fake = FakeGroq([])
    with pytest.raises(UnsupportedRequestError, match="max_completion_tokens"):
        adapter(telemetry, fake).generate_text(text_request(max_output_tokens=tokens))
    assert fake.requests == []


def test_the_output_limit_bounds_are_accepted(telemetry):
    for tokens in (1, MAX_OUTPUT_TOKENS):
        fake = FakeGroq([ok()])
        adapter(telemetry, fake).generate_text(text_request(max_output_tokens=tokens))
        assert fake.request_json(0)["max_completion_tokens"] == tokens


# ---------------------------------------------------------------- endpoint and routing
@pytest.mark.parametrize(
    "key", ["", " ", "has space", "tab\tkey", "line\nbreak", "kéy", None, 5, "k" * 513]
)
def test_an_invalid_key_is_refused_without_quoting_it(telemetry, key):
    with pytest.raises(ConfigError) as caught:
        ObservedGroqClient(
            telemetry, key, http_client=FakeGroq([]).http_client(), provenance="synthetic"
        )
    if isinstance(key, str) and len(key) > 2:
        assert key not in str(caught.value)


def test_real_provenance_is_refused_at_this_checkpoint(telemetry):
    assert groq_adapter.REAL_EXECUTION_ENABLED is False
    http = build_groq_http_client()
    try:
        with pytest.raises(ConfigError, match="not enabled"):
            ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="real")
    finally:
        http.close()


def test_an_unknown_provenance_is_refused(telemetry):
    with pytest.raises(ConfigError, match="provenance"):
        ObservedGroqClient(
            telemetry, FAKE_GROQ_KEY, http_client=FakeGroq([]).http_client(), provenance="live"
        )


def test_synthetic_provenance_cannot_use_a_real_transport(telemetry):
    http = build_groq_http_client()
    try:
        with pytest.raises(ConfigError, match="MockTransport"):
            ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="synthetic")
    finally:
        http.close()


def test_a_client_of_another_type_is_refused(telemetry):
    class Sub(httpx.Client):
        pass

    for http in (object(), None, Sub(transport=httpx.MockTransport(lambda r: httpx.Response(200)))):
        with pytest.raises(ConfigError):
            ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="synthetic")


def _hook(request: httpx.Request) -> None:  # pragma: no cover - refused before use
    raise AssertionError


MOCK = httpx.MockTransport(lambda request: httpx.Response(200))
ROUTING_MISCONFIGURATIONS = [
    pytest.param({"base_url": "https://collector.example.invalid"}, id="base-url"),
    pytest.param({"follow_redirects": True}, id="redirects"),
    pytest.param({"trust_env": True}, id="trust-env"),
    pytest.param({"mounts": {"https://": MOCK}}, id="mount"),
    pytest.param({"auth": ("user", "pass")}, id="auth"),
    pytest.param({"event_hooks": {"request": [_hook]}}, id="event-hook"),
    pytest.param({"headers": {"Authorization": "Bearer other"}}, id="credential-header"),
    pytest.param({"headers": {"X-Custom": "1"}}, id="custom-header"),
    pytest.param({"params": {"debug": "1"}}, id="query-parameter"),
    pytest.param({"cookies": {"a": "b"}}, id="cookie"),
]


@pytest.mark.parametrize("misconfigured", ROUTING_MISCONFIGURATIONS)
def test_every_routing_misconfiguration_is_refused(telemetry, misconfigured):
    fake = FakeGroq([])
    with pytest.raises(ConfigError, match="http_client"):
        ObservedGroqClient(
            telemetry,
            FAKE_GROQ_KEY,
            http_client=fake.http_client(**misconfigured),
            provenance="synthetic",
        )
    assert fake.requests == []


def test_the_client_is_checked_again_immediately_before_every_send(telemetry):
    fake = FakeGroq([])
    llm = observed(telemetry, fake)
    llm._http.follow_redirects = True
    with pytest.raises(ConfigError, match="redirects"):
        GroqTextAdapter(llm).generate_text(text_request())
    assert fake.requests == []


def test_a_request_that_is_not_the_pinned_one_is_never_sent(telemetry, monkeypatch):
    fake = FakeGroq([])
    llm = observed(telemetry, fake)
    original = llm._http.build_request

    def rerouted(method, url, **kw):
        return original(method, "https://api.groq.com/other", **kw)

    monkeypatch.setattr(llm._http, "build_request", rerouted)
    with pytest.raises(ConfigError, match="pinned"):
        GroqTextAdapter(llm).generate_text(text_request())
    assert fake.requests == []


@pytest.mark.parametrize(
    "name",
    [
        "GROQ_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
    ],
)
def test_the_environment_cannot_reroute_a_request(telemetry, monkeypatch, name):
    monkeypatch.setenv(name, "https://collector.example.invalid:1/")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")  # for the OTLP exporter's own connection
    fake = FakeGroq([ok()])
    adapter(telemetry, fake).generate_text(text_request())
    assert [str(r.url) for r in fake.requests] == [ENDPOINT]
    http = build_groq_http_client()
    try:
        assert http._mounts == {}
    finally:
        http.close()


def test_the_adapter_reads_no_environment_variable():
    source = (Path(groq_adapter.__file__)).read_text(encoding="utf-8")
    assert "import os" not in source and "os.environ" not in source and "getenv" not in source


# The real path is exercised only as far as refusing, with the lock lifted: never a send.
@pytest.fixture
def live_http() -> Iterator[httpx.Client]:
    http = build_groq_http_client()
    yield http
    http.close()


def test_the_built_client_has_no_boundary_problem(live_http):
    assert live_groq_client_problems(live_http) == []
    assert live_http.follow_redirects is False and live_http._trust_env is False
    assert live_http._transport._pool._retries == 0
    assert live_http._transport._pool._ssl_context.verify_mode.name == "CERT_REQUIRED"


@pytest.mark.parametrize(
    ("misconfigured", "expected"),
    [
        pytest.param({"follow_redirects": True}, "redirects", id="redirects"),
        pytest.param({"trust_env": True}, "environment", id="trust-env"),
        pytest.param({"base_url": "https://x.invalid"}, "base URL", id="base-url"),
        pytest.param({"timeout": 600.0}, "timeouts", id="timeout"),
        pytest.param({"transport": MOCK}, "HTTPTransport", id="mock-transport"),
        pytest.param({"verify": False}, "TLS", id="tls-off"),
        pytest.param({"proxy": "http://127.0.0.1:9"}, "mount", id="proxy"),
    ],
)
def test_each_live_boundary_condition_alone_refuses(misconfigured, expected):
    kw = {"trust_env": False, "follow_redirects": False, "timeout": groq_adapter.GROQ_TIMEOUT}
    kw.update(misconfigured)
    http = httpx.Client(**kw)
    try:
        problems = live_groq_client_problems(http)
    finally:
        http.close()
    assert any(expected in p for p in problems), problems


def test_transport_retries_are_a_live_boundary_problem():
    http = httpx.Client(
        transport=httpx.HTTPTransport(retries=2),
        trust_env=False,
        follow_redirects=False,
        timeout=groq_adapter.GROQ_TIMEOUT,
    )
    try:
        assert any("retries" in p for p in live_groq_client_problems(http))
    finally:
        http.close()


def _tls_context(mode: ssl.VerifyMode, *, hostname: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = hostname
    context.verify_mode = mode
    return context


UNSAFE_TLS = [
    pytest.param(ssl.CERT_OPTIONAL, True, id="optional-certificate-with-hostname-check"),
    pytest.param(ssl.CERT_OPTIONAL, False, id="optional-certificate-no-hostname-check"),
    pytest.param(ssl.CERT_NONE, False, id="no-certificate-no-hostname-check"),
    pytest.param(ssl.CERT_REQUIRED, False, id="required-certificate-no-hostname-check"),
]
TLS_PROBLEM = "TLS certificate and hostname verification must be enabled"


def _client_with_tls(mode: ssl.VerifyMode, hostname: bool) -> httpx.Client:
    """The built client's settings with only the TLS context changed. No connection is opened."""
    return httpx.Client(
        verify=_tls_context(mode, hostname=hostname),
        trust_env=False,
        follow_redirects=False,
        timeout=groq_adapter.GROQ_TIMEOUT,
    )


@pytest.mark.parametrize(("mode", "hostname"), UNSAFE_TLS)
def test_unsafe_tls_is_the_only_live_boundary_problem(mode, hostname):
    with _client_with_tls(mode, hostname) as http:
        assert live_groq_client_problems(http) == [TLS_PROBLEM]


@pytest.mark.parametrize(("mode", "hostname"), UNSAFE_TLS)
def test_unsafe_tls_is_refused_before_any_request(telemetry, stub, monkeypatch, mode, hostname):
    """The `offline` fixture also fails this test if any connection is attempted."""
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    with _client_with_tls(mode, hostname) as http, pytest.raises(ConfigError, match="TLS"):
        ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="real")
    assert client_spans(exported(telemetry, stub)) == []


def test_a_client_subclass_is_a_live_boundary_problem():
    class Sub(httpx.Client):
        pass

    with Sub() as http:
        assert "exactly an httpx.Client" in live_groq_client_problems(http)[-1]


def test_a_library_version_other_than_the_audited_one_is_a_problem(live_http, monkeypatch):
    monkeypatch.setitem(groq_adapter.AUDITED_LIBRARY_VERSIONS, "httpx", "0.0.1")
    assert any("httpx" in p for p in live_groq_client_problems(live_http))


def test_with_the_lock_lifted_a_mock_transport_is_still_refused_for_real(telemetry, monkeypatch):
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    fake = FakeGroq([])
    with pytest.raises(ConfigError, match="HTTPTransport"):
        ObservedGroqClient(
            telemetry, FAKE_GROQ_KEY, http_client=fake.http_client(), provenance="real"
        )
    assert fake.requests == []


def test_with_the_lock_lifted_a_changed_client_is_refused_before_the_send(
    telemetry, monkeypatch, live_http, stub
):
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    llm = ObservedGroqClient(
        telemetry,
        FAKE_GROQ_KEY,
        http_client=live_http,
        provenance="real",
        send_gate=FreePlanSession({}),
    )
    live_http.follow_redirects = True
    with pytest.raises(ConfigError, match="redirects"):
        GroqTextAdapter(llm).generate_text(text_request())
    assert client_spans(exported(telemetry, stub)) == []


# ---------------------------------------------------------------- real sockets, loopback only
class Loopback:
    """One scripted raw HTTP response per POST, over a real TCP connection on 127.0.0.1."""

    def __init__(self) -> None:
        self.scripts: list[tuple[bytes, float]] = []
        self.paths: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers["content-length"]))
                owner.paths.append(self.path)
                raw, stall = owner.scripts.pop(0)
                self.wfile.write(raw)
                self.wfile.flush()
                time.sleep(stall)
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/chat"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def loopback(monkeypatch) -> Iterator[Loopback]:
    """The real httpx transport against a local socket. The endpoint constant and the
    mock-transport requirement are the only things replaced."""
    server = Loopback()
    monkeypatch.setattr(groq_adapter, "ENDPOINT_URL", server.url)
    monkeypatch.setattr(groq_adapter, "_check_http_client", lambda http, provenance: None)
    monkeypatch.setattr(
        groq_adapter, "GROQ_TIMEOUT", httpx.Timeout(connect=2.0, read=0.3, write=2.0, pool=1.0)
    )
    yield server
    server.stop()


def loopback_call(telemetry, server: Loopback, raw: bytes, stall: float = 0.0):
    server.scripts.append((raw, stall))
    with httpx.Client(trust_env=False, follow_redirects=False) as http:
        llm = ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=http, provenance="synthetic")
        return GroqTextAdapter(llm).generate_text(text_request())


def http_response(body: bytes, *headers: str, status: str = "200 OK") -> bytes:
    head = [f"HTTP/1.1 {status}", "Content-Type: application/json", *headers]
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


def test_a_real_socket_response_is_read_and_parsed(telemetry, loopback):
    body = json.dumps(groq_body()).encode()
    raw = http_response(body, f"Content-Length: {len(body)}")
    outcome = loopback_call(telemetry, loopback, raw)
    assert outcome.status == "completed" and outcome.provider_response.text == OUTPUT_SENTINEL
    assert len(loopback.paths) == 1


def test_a_connection_closed_mid_body_is_a_connection_error_and_is_not_retried(
    telemetry, stub, loopback
):
    raw = http_response(json.dumps(groq_body()).encode()[:30], "Content-Length: 5000")
    outcome = loopback_call(telemetry, loopback, raw)
    assert (outcome.status, outcome.error_category) == ("error", "connection_error")
    assert len(loopback.paths) == 1 and outcome.usage is None
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "httpx.RemoteProtocolError"


def test_a_stalled_real_read_times_out_and_is_not_retried(telemetry, stub, loopback):
    raw = http_response(b'{"id":', "Content-Length: 5000")
    outcome = loopback_call(telemetry, loopback, raw, stall=1.0)
    assert (outcome.status, outcome.error_category) == ("error", "timeout")
    assert len(loopback.paths) == 1
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "timeout"


def test_a_real_oversized_chunked_body_is_cut_off(telemetry, stub, loopback):
    chunk = b"x" * 65_536
    chunks = b"".join(b"10000\r\n" + chunk + b"\r\n" for _ in range(40)) + b"0\r\n\r\n"
    outcome = loopback_call(
        telemetry, loopback, http_response(chunks, "Transfer-Encoding: chunked")
    )
    assert outcome.error_category == "malformed_response"
    assert one_span(telemetry, stub)["attributes"]["error.type"] == "groq.response.oversized"


def test_a_real_redirect_is_not_followed(telemetry, loopback):
    raw = http_response(
        b"{}", "Location: /elsewhere", "Content-Length: 2", status="307 Temporary Redirect"
    )
    outcome = loopback_call(telemetry, loopback, raw)
    assert outcome.error_category == "http_error"
    assert loopback.paths == ["/chat"]


# ---------------------------------------------------------------- module boundaries
def test_the_core_sdk_never_imports_this_adapter_or_httpx(tmp_path):
    code = (
        "import sys, llm_insight_sdk, llm_insight_sdk.providers;"
        "print('llm_insight_sdk.groq_adapter' in sys.modules, 'httpx' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "False"]


def test_the_groq_extra_is_exactly_pinned():
    import importlib.metadata

    dist = importlib.metadata.distribution("llm-insight-sdk")
    extras = sorted(r for r in dist.requires or [] if 'extra == "groq"' in r)
    assert extras == [
        'certifi==2026.10.19; extra == "groq"',
        'httpx==0.28.1; extra == "groq"',
    ]


def test_every_category_the_adapter_can_report_is_a_stable_one():
    source = Path(groq_adapter.__file__).read_text(encoding="utf-8")
    assert set(groq_adapter._STATUS_CATEGORIES.values()) <= ERROR_CATEGORIES
    for category in (
        "malformed_response",
        "empty_response",
        "response_failed",
        "unexpected_status",
        "content_blocked",
        "provider_error",
        "precondition_failed",
        "connection_error",
        "unexpected_error",
        "invalid_request",
        "http_error",
    ):
        assert category in ERROR_CATEGORIES and f'"{category}"' in source
