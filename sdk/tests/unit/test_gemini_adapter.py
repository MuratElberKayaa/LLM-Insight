"""Gemini Developer API adapter (Phase 6C-A, ADR-33), fully offline: injected fake httpx
transports, the exported protobuf checked at the local OTLP stub. Every response is a
SYNTHETIC fixture and every span is labelled `synthetic`. No request can leave the machine
(`offline` fixture)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator

import httpx
import pytest
from google import genai
from google.genai import types as genai_types

from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.gemini_adapter import (
    GeminiTextAdapter,
    ObservedGeminiClient,
    UnsupportedRequestError,
    assess_gemini_response,
    classify_gemini_error,
    gemini_error_category,
    usage_from_gemini_response,
)
from llm_insight_sdk.instrumentation import TokenUsage
from llm_insight_sdk.providers import ERROR_CATEGORIES, TextRequest
from tests.conftest import DUMMY_KEY, PROMPT_SENTINEL
from tests.gemini_fixtures import (
    ENDPOINT,
    ERROR_SENTINEL,
    FAKE_GEMINI_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    THOUGHT_SENTINEL,
    FakeGemini,
    GStep,
    candidate,
    g_fail,
    g_ok,
    gemini_body,
)
from tests.openai_fixtures import block_network
from tests.unit.test_instrumentation import ALLOWED_INFERENCE_KEYS, exported

SECRETS = (FAKE_GEMINI_KEY, PROMPT_SENTINEL, OUTPUT_SENTINEL, THOUGHT_SENTINEL, ERROR_SENTINEL)


@pytest.fixture(autouse=True)
def offline() -> Iterator[list[str]]:
    attempts: list[str] = []
    with block_network(attempts):
        yield attempts
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def adapter(telemetry, fake: FakeGemini) -> GeminiTextAdapter:
    return GeminiTextAdapter(ObservedGeminiClient(telemetry, fake.client(), provenance="synthetic"))


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


# ---------------------------------------------------------------- success and mapping
def test_successful_call_span_shape_usage_and_correlation(telemetry, stub):
    fake = FakeGemini([g_ok()])
    with telemetry.tracer.start_as_current_span("demo.request") as root:
        outcome = adapter(telemetry, fake).generate_text(
            text_request(prompt_name="smoke", prompt_version="v1")
        )
    assert outcome.status == "completed" and outcome.error_category is None
    assert outcome.response_id == "SYNTHETICgeminiResp01"
    assert outcome.usage == TokenUsage(
        input_tokens=20, output_tokens=32, cache_read_input_tokens=4, reasoning_output_tokens=30
    )
    assert len(outcome.attempts) == 1 and outcome.attempts[0].attempt == 1

    spans = exported(telemetry, stub)
    (span,) = client_spans(spans)
    (parent,) = [s for s in spans if s["name"] == "demo.request"]
    assert (
        span["trace_id"] == parent["trace_id"] == format(root.get_span_context().trace_id, "032x")
    )
    assert span["parent_span_id"] == parent["span_id"]
    assert span["name"] == f"generate_content {MODEL}"
    assert span["kind"] == "CLIENT" and span["status"] == "UNSET" and span["events"] == 0
    a = span["attributes"]
    assert uuid.UUID(a["llminsight.call_id"]).version == 4
    assert a == {
        "gen_ai.operation.name": "generate_content",
        "gen_ai.provider.name": "gcp.gemini",
        "gen_ai.request.model": MODEL,
        "gen_ai.response.model": MODEL,
        "gen_ai.response.id": "SYNTHETICgeminiResp01",
        "gen_ai.response.finish_reasons": ["STOP"],
        "gen_ai.request.max_tokens": 64,
        "gen_ai.prompt.name": "smoke",
        "gen_ai.prompt.version": "v1",
        "gen_ai.usage.input_tokens": 20,
        "gen_ai.usage.output_tokens": 32,  # candidates 2 + thoughts 30 (billed output)
        "gen_ai.usage.cache_read.input_tokens": 4,  # subset of input, not added
        "gen_ai.usage.reasoning.output_tokens": 30,  # subset of output, not added
        "llminsight.usage_source": "provider_reported",
        "llminsight.call_id": a["llminsight.call_id"],
        "llminsight.attempt": 1,
        "llminsight.provenance": "synthetic",
        "server.address": "generativelanguage.googleapis.com",
        "server.port": 443,
    }
    # attrmap.v1 unchanged: no new attribute (server.* are stable core, allowlisted)
    assert set(a) <= ALLOWED_INFERENCE_KEYS | {"server.address", "server.port"}
    assert_no_secrets(spans, repr(outcome))


def test_exactly_one_minimal_request_reaches_the_developer_api_endpoint(telemetry):
    fake = FakeGemini([g_ok()])
    adapter(telemetry, fake).generate_text(text_request())
    (req,) = fake.requests
    assert (req.method, str(req.url)) == ("POST", ENDPOINT)
    assert req.headers["x-goog-api-key"] == FAKE_GEMINI_KEY
    assert "authorization" not in req.headers and "x-goog-user-project" not in req.headers
    # nothing but the text and the output ceiling: no tools, no continuation, no thinking,
    # no system instruction, no cached content, no safety settings
    assert fake.request_json(0) == {
        "contents": [{"parts": [{"text": PROMPT_SENTINEL}], "role": "user"}],
        "generationConfig": {"maxOutputTokens": 64},
    }


def test_numeric_settings_are_recorded_and_sent(telemetry, stub):
    fake = FakeGemini([g_ok()])
    llm = ObservedGeminiClient(telemetry, fake.client(), provenance="synthetic")
    llm.generate_content(
        model=MODEL, contents=PROMPT_SENTINEL, max_output_tokens=100, temperature=0.5, top_p=0.9
    )
    assert fake.request_json(0)["generationConfig"] == {
        "maxOutputTokens": 100,
        "temperature": 0.5,
        "topP": 0.9,
    }
    (span,) = client_spans(exported(telemetry, stub))
    a = span["attributes"]
    assert (a["gen_ai.request.max_tokens"], a["gen_ai.request.temperature"]) == (100, 0.5)
    assert a["gen_ai.request.top_p"] == 0.9


# ---------------------------------------------------------------- usage accounting
def _reading(usage):
    return usage_from_gemini_response(
        genai_types.GenerateContentResponse.model_validate(gemini_body(usage=usage))
    )


@pytest.mark.parametrize(
    ("usage", "check", "expected"),
    [
        # thinking: thoughts are billed output and a subset of it, never added twice
        ({"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7,
          "totalTokenCount": 22}, "consistent", TokenUsage(10, 12, None, None, 7)),
        # cached content is part of the prompt count (subset of input)
        ({"promptTokenCount": 100, "cachedContentTokenCount": 80, "candidatesTokenCount": 3,
          "totalTokenCount": 103}, "consistent", TokenUsage(100, 3, 80, None, None)),
        # cached == prompt is still consistent
        ({"promptTokenCount": 8, "cachedContentTokenCount": 8, "candidatesTokenCount": 1,
          "totalTokenCount": 9}, "consistent", TokenUsage(8, 1, 8, None, None)),
        # JSON omits zero counts: proven 0 only because the total matches
        ({"promptTokenCount": 9, "totalTokenCount": 9}, "consistent",
         TokenUsage(9, 0, None, None, None)),
        # tool-use prompt tokens are input (semconv: all input types)
        ({"promptTokenCount": 5, "toolUsePromptTokenCount": 3, "candidatesTokenCount": 2,
          "totalTokenCount": 10}, "consistent", TokenUsage(8, 2, None, None, None)),
        # reported explicit zeros are kept
        ({"promptTokenCount": 4, "candidatesTokenCount": 0, "thoughtsTokenCount": 0,
          "cachedContentTokenCount": 0, "totalTokenCount": 4}, "consistent",
         TokenUsage(4, 0, 0, None, 0)),
        (None, "missing", None),  # no usageMetadata at all
        ({}, "missing", None),
        ({"candidatesTokenCount": 2, "totalTokenCount": 2}, "missing", None),  # no prompt
        ({"promptTokenCount": 3, "candidatesTokenCount": 2}, "missing", None),  # no total
        # the parts do not add up to the total: never guessed
        ({"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 9},
         "inconsistent", None),
        # thoughts absent but the total says they exist: not assumed 0
        ({"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 35},
         "inconsistent", None),
        ({"promptTokenCount": 3, "cachedContentTokenCount": 4, "totalTokenCount": 3},
         "inconsistent", None),  # cached above prompt
        ({"promptTokenCount": -1, "totalTokenCount": -1}, "inconsistent", None),
    ],
)  # fmt: skip
def test_usage_mapping_is_provider_reported_only_and_never_double_counted(usage, check, expected):
    reading = _reading(usage)
    assert reading.check == check
    if expected is None:
        assert reading.usage is None  # unknown, never 0
    else:
        assert reading.usage == TokenUsage(
            input_tokens=expected.input_tokens,
            output_tokens=expected.output_tokens,
            cache_read_input_tokens=expected.cache_read_input_tokens,
            cache_write_input_tokens=None,
            reasoning_output_tokens=expected.reasoning_output_tokens,
        )


def test_provider_counts_are_reported_as_given(telemetry):
    fake = FakeGemini([g_ok()])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert usage_from_gemini_response(outcome.provider_response).provider_counts == {
        "prompt": 20,
        "candidates": 2,
        "thoughts": 30,
        "cached_content": 4,
        "tool_use_prompt": None,
        "total": 52,
    }


@pytest.mark.parametrize("usage", [None, {"promptTokenCount": 3, "totalTokenCount": 99}])
def test_missing_or_inconsistent_usage_is_exported_as_unknown(telemetry, stub, usage):
    outcome = adapter(telemetry, FakeGemini([g_ok(usage=usage)])).generate_text(text_request())
    assert outcome.status == "completed" and outcome.usage is None
    (span,) = client_spans(exported(telemetry, stub))
    a = span["attributes"]
    assert a["llminsight.usage_source"] == "unknown"
    assert not any(k.startswith("gen_ai.usage.") for k in a)


def test_missing_response_identifiers_are_absent_not_invented(telemetry, stub):
    fake = FakeGemini([g_ok(response_id=None, model_version=None)])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert outcome.status == "completed"
    assert outcome.response_id is None and outcome.response_model is None
    (span,) = client_spans(exported(telemetry, stub))
    assert "gen_ai.response.id" not in span["attributes"]
    assert "gen_ai.response.model" not in span["attributes"]


# ---------------------------------------------------------------- response outcomes
@pytest.mark.parametrize(
    ("step", "status", "reason", "category", "span_status", "error_type", "finish"),
    [
        (g_ok(finish="MAX_TOKENS"), "incomplete", "MAX_TOKENS", None, "UNSET", None,
         ["MAX_TOKENS"]),
        (g_ok(finish="SAFETY"), "blocked", "SAFETY", "content_blocked", "UNSET", None,
         ["SAFETY"]),
        (g_ok(finish="RECITATION"), "blocked", "RECITATION", "content_blocked", "UNSET", None,
         ["RECITATION"]),
        (GStep(200, {"promptFeedback": {"blockReason": "SAFETY"},
                     "usageMetadata": {"promptTokenCount": 9, "totalTokenCount": 9}}),
         "blocked", "SAFETY", "content_blocked", "ERROR", "gcp.gemini.prompt_blocked", None),
        (GStep(200, {}), "failed", None, "empty_response", "ERROR", "gcp.gemini.response.empty",
         None),
        (g_ok(candidates=[]), "failed", None, "empty_response", "ERROR",
         "gcp.gemini.response.empty", None),
        (g_ok(finish="OTHER"), "failed", "OTHER", "response_failed", "ERROR",
         "gcp.gemini.response.failed", ["OTHER"]),
        (g_ok(finish=None), "failed", None, "unexpected_status", "ERROR",
         "gcp.gemini.response.unexpected_finish", None),
    ],
)  # fmt: skip
def test_response_outcomes(
    telemetry, stub, step, status, reason, category, span_status, error_type, finish
):
    fake = FakeGemini([step])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert (outcome.status, outcome.provider_reason, outcome.error_category) == (
        status,
        reason,
        category,
    )
    assert outcome.incomplete_reason == (reason if status == "incomplete" else None)
    assert len(fake.requests) == 1 and len(outcome.attempts) == 1
    (span,) = client_spans(exported(telemetry, stub))
    assert span["status"] == span_status and span["status_message"] == ""
    assert span["attributes"].get("error.type") == error_type
    assert span["attributes"].get("gen_ai.response.finish_reasons") == finish
    assert_no_secrets(span, repr(outcome))


def test_a_continuation_answer_is_never_followed_automatically(telemetry, stub):
    """google-genai 2.30.1 follows continuation tokens by default and merges the usage of
    every hop into one response; the adapter disables it, so one span = one request."""
    cont = g_ok(candidates=[candidate("CONTINUATION", continuationToken="U1lOVEhFVElD")])
    fake = FakeGemini([cont, g_ok()])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert len(fake.requests) == 1  # the scripted second answer was never requested
    assert (outcome.status, outcome.incomplete_reason) == ("incomplete", "CONTINUATION")
    assert outcome.usage is not None and outcome.usage.output_tokens == 32  # not merged
    assert len(client_spans(exported(telemetry, stub))) == 1


def test_assessment_of_a_non_response_is_malformed():
    verdict = assess_gemini_response(object())
    assert (verdict.status, verdict.category) == ("malformed", "malformed_response")


# ---------------------------------------------------------------- failures
@pytest.mark.parametrize(
    ("step", "category", "error_type", "status_keyword"),
    [
        (g_fail(400, "INVALID_ARGUMENT"), "invalid_request", "400", "INVALID_ARGUMENT"),
        (g_fail(400, "FAILED_PRECONDITION"), "precondition_failed", "400", "FAILED_PRECONDITION"),
        (g_fail(401, "UNAUTHENTICATED"), "authentication_failed", "401", "UNAUTHENTICATED"),
        (g_fail(402, "PAYMENT_REQUIRED"), "payment_required", "402", "PAYMENT_REQUIRED"),
        (g_fail(403, "PERMISSION_DENIED"), "permission_denied", "403", "PERMISSION_DENIED"),
        (g_fail(404, "NOT_FOUND"), "not_found", "404", "NOT_FOUND"),
        (g_fail(429, "RESOURCE_EXHAUSTED"), "rate_limited", "429", "RESOURCE_EXHAUSTED"),
        (g_fail(500, "INTERNAL"), "provider_error", "500", "INTERNAL"),
        (g_fail(503, "UNAVAILABLE"), "provider_error", "503", "UNAVAILABLE"),
        (g_fail(504, "DEADLINE_EXCEEDED"), "provider_error", "504", "DEADLINE_EXCEEDED"),
        (GStep(502, raw=b"<html>SYNTHETIC gateway</html>", content_type="text/html"),
         "provider_error", "502", None),
        (GStep(200, raw=b"<html>" + ERROR_SENTINEL.encode() + b"</html>",
               content_type="text/html"),
         "malformed_response", "gcp.gemini.response.malformed", None),
        (GStep(200, raw=b'{"candidates": [', content_type="application/json"),
         "malformed_response", "gcp.gemini.response.malformed", None),
        (GStep(200, {"candidates": "not-a-list", "usageMetadata": {"promptTokenCount": "x"}}),
         "malformed_response", "gcp.gemini.response.malformed", None),
        (GStep(raise_=httpx.ReadTimeout), "timeout", "timeout", None),
        (GStep(raise_=httpx.ConnectTimeout), "timeout", "timeout", None),
        (GStep(raise_=httpx.ConnectError), "connection_error", "httpx.ConnectError", None),
        (GStep(307, raw=b"", headers={"location": "https://evil.example/x"}), "http_error",
         "307", None),
    ],
)  # fmt: skip
def test_failures_are_one_attempt_classified_and_never_retried(
    telemetry, stub, caplog, step, category, error_type, status_keyword
):
    caplog.set_level(logging.DEBUG, logger="llm_insight_sdk")
    fake = FakeGemini([step, g_ok(), g_ok()])
    outcome = adapter(telemetry, fake).generate_text(text_request())
    assert len(fake.requests) == 1  # no retry at any layer, no redirect followed
    assert {r.url.host for r in fake.requests} == {"generativelanguage.googleapis.com"}
    assert outcome.status == "error" and outcome.error_category == category
    assert category in ERROR_CATEGORIES
    assert outcome.provider_reason == status_keyword
    assert outcome.usage is None and outcome.provider_response is None
    assert outcome.attempts[0].error_category == category
    (span,) = client_spans(exported(telemetry, stub))
    assert span["status"] == "ERROR" and span["status_message"] == "" and span["events"] == 0
    assert span["attributes"]["error.type"] == error_type
    assert span["attributes"]["llminsight.usage_source"] == "unknown"
    assert_no_secrets(span, repr(outcome), caplog.text)


def test_classification_never_reads_the_message():
    from google.genai import errors

    exc = errors.ClientError(429, {"error": {"message": ERROR_SENTINEL, "status": "x y"}})
    assert classify_gemini_error(exc) == "429"
    assert gemini_error_category(exc) == "rate_limited"
    assert ERROR_SENTINEL in str(exc)  # why the adapter never uses str(exc)


# ---------------------------------------------------------------- refused before sending
@pytest.mark.parametrize(
    "config",
    [
        {"tools": [{"google_search": {}}]},
        {"tool_config": {}},
        {"http_options": {"base_url": "https://evil.example/"}},
        {"cached_content": "cachedContents/x"},
        {"system_instruction": PROMPT_SENTINEL},
        {"thinking_config": {"thinking_budget": 1000}},
        {"safety_settings": []},
        {"response_mime_type": "application/json"},
        {"candidate_count": 2},
        {"automatic_continuation": True},
        {"automatic_function_calling": {"disable": False}},
        {"continuation_token": b"x"},
        {"labels": {"a": "b"}},
        {"should_return_http_response": True},
    ],
)
def test_unsupported_settings_are_refused_before_sending(telemetry, stub, config):
    fake = FakeGemini([g_ok()])
    llm = ObservedGeminiClient(telemetry, fake.client(), provenance="synthetic")
    with pytest.raises(UnsupportedRequestError):
        llm.generate_content(model=MODEL, contents="x", **config)
    assert fake.requests == []
    assert exported(telemetry, stub) == []


@pytest.mark.parametrize(
    ("model", "contents"),
    [
        ("models/gemini-3.1-flash-lite", "x"),
        ("tunedModels/123", "x"),
        ("gemini-3.1-flash-lite:streamGenerateContent", "x"),
        ("../v1/files", "x"),
        ("gemini?alt=sse", "x"),
        ("", "x"),
        (MODEL, ["turn one", "turn two"]),  # history
        (MODEL, [{"role": "user", "parts": [{"file_data": {"file_uri": "x"}}]}]),  # file
        (MODEL, None),
    ],
)
def test_only_a_plain_model_and_one_text_turn_are_accepted(telemetry, model, contents):
    fake = FakeGemini([g_ok()])
    llm = ObservedGeminiClient(telemetry, fake.client(), provenance="synthetic")
    with pytest.raises(UnsupportedRequestError):
        llm.generate_content(model=model, contents=contents)
    assert fake.requests == []


def test_vertex_ai_and_other_endpoints_are_refused_in_every_mode(telemetry):
    vertex = genai.Client(vertexai=True, api_key=FAKE_GEMINI_KEY)  # express mode, offline
    with pytest.raises(ConfigError):
        ObservedGeminiClient(telemetry, vertex, provenance="synthetic")
    fake = FakeGemini([g_ok()])
    other = fake.client(base_url="https://evil.example/")
    with pytest.raises(ConfigError):
        ObservedGeminiClient(telemetry, other, provenance="synthetic")
    with pytest.raises(ConfigError):
        ObservedGeminiClient(telemetry, fake.client(), provenance="real")  # mock transport
    with pytest.raises(ConfigError):
        ObservedGeminiClient(telemetry, fake.client(), provenance="paid")  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        ObservedGeminiClient(telemetry, object(), provenance="synthetic")  # type: ignore[arg-type]
    assert fake.requests == []


# ---------------------------------------------------------------- privacy
def test_no_content_or_secret_reaches_the_wire_the_log_or_the_outcome(telemetry, stub, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeGemini([g_ok(), g_fail(429, "RESOURCE_EXHAUSTED")])
    a = adapter(telemetry, fake)
    ok = a.generate_text(text_request())
    failed = a.generate_text(text_request())
    assert telemetry.force_flush(5000)
    wire = b"".join(r.raw_body + r.body for r in stub.received)
    for secret in SECRETS + (DUMMY_KEY,):
        assert secret.encode() not in wire
    assert_no_secrets(repr(ok), repr(failed), caplog.text)
    assert ok.provider_response is not None  # the caller still gets the answer object
