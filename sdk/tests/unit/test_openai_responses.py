"""OpenAI Responses API adapter (Phase 6A), fully offline: injected fake transports, the
exported protobuf checked at the local OTLP stub. Every response is a SYNTHETIC fixture and
every span is labelled `synthetic`. No request can leave the machine (`offline` fixture)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator

import httpx2
import openai
import pytest

from llm_insight_sdk.openai_adapter import (
    ObservedOpenAIClient,
    OpenAIResponsesTextAdapter,
    UnsupportedRequestError,
    error_category,
    is_unambiguously_retryable,
)
from llm_insight_sdk.providers import ERROR_CATEGORIES, TextRequest
from tests.conftest import PROMPT_SENTINEL, SYSTEM_SENTINEL
from tests.openai_fixtures import (
    COMPLETION_SENTINEL,
    ERROR_SENTINEL,
    FAKE_API_KEY,
    REQUEST_ID_SENTINEL,
    FakeOpenAI,
    Step,
    block_network,
    fail,
    quota_exhausted,
    response_body,
    response_ok,
)
from tests.unit.test_instrumentation import ALLOWED_INFERENCE_KEYS, exported
from tests.unit.test_openai_adapter import OPENAI_KEYS

MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"


@pytest.fixture(autouse=True)
def offline() -> Iterator[list[str]]:
    attempts: list[str] = []
    with block_network(attempts):
        yield attempts
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def observed(telemetry, fake: FakeOpenAI) -> ObservedOpenAIClient:
    return ObservedOpenAIClient(
        telemetry, fake.client(), provenance="synthetic", backoff_seconds=0.0
    )


def respond(llm: ObservedOpenAIClient, **kw):
    kw.setdefault("model", MODEL)
    kw.setdefault("input", PROMPT_SENTINEL)
    kw.setdefault("service_tier", "default")
    return llm.create_response(**kw)


def clients(spans):
    return sorted(
        (s for s in spans if s["kind"] == "CLIENT"),
        key=lambda s: s["attributes"]["llminsight.attempt"],
    )


def text_request(**kw) -> TextRequest:
    kw.setdefault("model", MODEL)
    kw.setdefault("input_text", PROMPT_SENTINEL)
    kw.setdefault("max_output_tokens", 64)
    return TextRequest(**kw)


# ---------------------------------------------------------------- success and mapping
def test_successful_response_span_shape_usage_and_correlation(telemetry, stub):
    fake = FakeOpenAI([response_ok(response_id="resp_SYNTHETIC_shape")])
    with telemetry.tracer.start_as_current_span("demo.request") as root:
        result = respond(
            observed(telemetry, fake),
            instructions=SYSTEM_SENTINEL,
            max_output_tokens=400,
            temperature=0.2,
            prompt_name="summarize",
            prompt_version="v1",
        )
    assert isinstance(result, openai.types.responses.Response)
    assert result.output_text == COMPLETION_SENTINEL  # returned to the caller, never exported

    spans = exported(telemetry, stub)
    (span,) = clients(spans)
    (parent,) = [s for s in spans if s["name"] == "demo.request"]
    assert (
        span["trace_id"] == parent["trace_id"] == format(root.get_span_context().trace_id, "032x")
    )
    assert span["parent_span_id"] == parent["span_id"]
    assert span["name"] == f"chat {MODEL}"
    assert span["kind"] == "CLIENT" and span["status"] == "UNSET" and span["events"] == 0
    a = span["attributes"]
    assert uuid.UUID(a["llminsight.call_id"]).version == 4
    assert a == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL,
        "gen_ai.response.model": SNAPSHOT,
        "gen_ai.response.id": "resp_SYNTHETIC_shape",
        "gen_ai.request.max_tokens": 400,
        "gen_ai.request.temperature": 0.2,
        "gen_ai.prompt.name": "summarize",
        "gen_ai.prompt.version": "v1",
        "gen_ai.usage.input_tokens": 1200,
        "gen_ai.usage.output_tokens": 300,
        "gen_ai.usage.cache_read.input_tokens": 1000,
        "gen_ai.usage.cache_write.input_tokens": 0,
        "gen_ai.usage.reasoning.output_tokens": 200,
        "llminsight.usage_source": "provider_reported",
        "llminsight.call_id": a["llminsight.call_id"],
        "llminsight.attempt": 1,
        "llminsight.provenance": "synthetic",
        "openai.api.type": "responses",
        "openai.request.service_tier": "default",
        "openai.response.service_tier": "default",
        "server.address": "api.openai.com",
        "server.port": 443,
    }
    assert set(a) <= ALLOWED_INFERENCE_KEYS | OPENAI_KEYS  # attrmap.v1 unchanged


def test_request_is_sent_once_with_store_false_and_passed_through(telemetry, stub):
    fake = FakeOpenAI([response_ok()])
    respond(observed(telemetry, fake), instructions=SYSTEM_SENTINEL, max_output_tokens=50)
    assert len(fake.requests) == 1
    assert str(fake.requests[0].url) == "https://api.openai.com/v1/responses"
    sent = fake.request_json(0)
    assert sent["store"] is False
    assert sent["input"] == PROMPT_SENTINEL and sent["instructions"] == SYSTEM_SENTINEL
    assert sent["max_output_tokens"] == 50 and sent["service_tier"] == "default"
    assert "stream" not in sent and "background" not in sent
    assert fake.requests[0].headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    assert len(clients(exported(telemetry, stub))) == 1  # one call, one span


def test_explicit_store_false_is_accepted(telemetry, stub):
    fake = FakeOpenAI([response_ok()])
    respond(observed(telemetry, fake), store=False)
    assert fake.request_json(0)["store"] is False


def test_json_text_format_is_recorded_as_output_type(telemetry, stub):
    fake = FakeOpenAI([response_ok()])
    fmt = {"format": {"type": "json_schema", "name": "x", "schema": {"type": "object"}}}
    respond(observed(telemetry, fake), text=fmt)
    (span,) = clients(exported(telemetry, stub))
    assert span["attributes"]["gen_ai.output.type"] == "json"


def test_missing_usage_stays_unknown_never_estimated(telemetry, stub):
    fake = FakeOpenAI([response_ok(usage=None)])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    a = span["attributes"]
    assert not any(k.startswith("gen_ai.usage.") for k in a)
    assert a["llminsight.usage_source"] == "unknown"
    assert outcome.status == "completed" and outcome.usage is None
    assert outcome.attempts[0].usage is None


def test_usage_with_wrong_types_is_unknown(telemetry, stub):
    usage = {"input_tokens": "1200", "output_tokens": 3.5, "total_tokens": True}
    fake = FakeOpenAI([response_ok(usage=usage)])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    assert not any(k.startswith("gen_ai.usage.") for k in span["attributes"])
    assert outcome.usage is None


def test_unreported_usage_details_stay_absent(telemetry, stub):
    usage = {"input_tokens": 40, "output_tokens": 9, "total_tokens": 49}
    fake = FakeOpenAI([response_ok(usage=usage)])
    respond(observed(telemetry, fake))
    (span,) = clients(exported(telemetry, stub))
    a = span["attributes"]
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (40, 9)
    for subset in ("cache_read.input", "cache_write.input", "reasoning.output"):
        assert f"gen_ai.usage.{subset}_tokens" not in a


def test_missing_response_id_is_absent_and_not_an_error(telemetry, stub):
    fake = FakeOpenAI([response_ok(response_id=None)])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    assert "gen_ai.response.id" not in span["attributes"]
    assert span["status"] == "UNSET"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 1200
    assert outcome.status == "completed" and outcome.response_id is None


def test_incomplete_response_records_the_provider_reason(telemetry, stub):
    fake = FakeOpenAI([response_ok(status="incomplete", incomplete_reason="max_output_tokens")])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    assert span["attributes"]["gen_ai.response.finish_reasons"] == ["max_output_tokens"]
    assert span["status"] == "UNSET"  # a returned, billed response, not an error
    assert outcome.status == "incomplete" and outcome.error_category is None
    assert outcome.incomplete_reason == "max_output_tokens"  # Phase 6B: never "completed"


def test_completed_and_failed_responses_have_no_incomplete_reason(telemetry, stub):
    fake = FakeOpenAI(
        [response_ok(), response_ok(status="failed", incomplete_reason="max_output_tokens")]
    )
    adapter = OpenAIResponsesTextAdapter(observed(telemetry, fake))
    assert adapter.generate_text(text_request()).incomplete_reason is None
    failed = adapter.generate_text(text_request())
    assert failed.status == "failed" and failed.incomplete_reason is None


@pytest.mark.parametrize(
    ("status", "error_type", "category"),
    [
        ("failed", "openai.response.failed", "response_failed"),
        ("in_progress", "openai.response.unexpected_status", "unexpected_status"),
        ("cancelled", "openai.response.unexpected_status", "unexpected_status"),
    ],
)
def test_failed_or_unexpected_status_marks_the_span(telemetry, stub, status, error_type, category):
    fake = FakeOpenAI([response_ok(status=status, usage=None)])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    assert span["status"] == "ERROR" and span["status_message"] == ""
    assert span["attributes"]["error.type"] == error_type
    assert outcome.status == "failed" and outcome.error_category == category
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "step",
    [
        Step(200, raw=b"<html>SYNTHETIC gateway page</html>", content_type="text/html"),
        Step(200, raw=b"[1, 2]"),
        Step(200, {"id": 5, "status": 7, "usage": "x", "model": None}),
        Step(200, {}),
    ],
    ids=["html", "json-list", "wrong-types", "empty-object"],
)
def test_malformed_response_is_an_error_outcome(telemetry, stub, step):
    fake = FakeOpenAI([step])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    assert span["status"] == "ERROR"
    assert span["attributes"]["error.type"] == "openai.response.malformed"
    assert span["attributes"]["llminsight.usage_source"] == "unknown"
    assert "gen_ai.response.id" not in span["attributes"]
    assert outcome.status == "malformed" and outcome.error_category == "malformed_response"
    assert outcome.usage is None and outcome.response_id is None


def test_invalid_json_body_is_a_malformed_error_without_its_text(telemetry, stub):
    fake = FakeOpenAI([Step(200, raw=b'{"id": "resp_x", ' + COMPLETION_SENTINEL.encode())])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    (span,) = clients(exported(telemetry, stub))
    assert span["status"] == "ERROR"
    assert outcome.status == "error" and outcome.error_category == "malformed_response"
    assert COMPLETION_SENTINEL not in repr(span)


# ---------------------------------------------------------------- errors and retries
@pytest.mark.parametrize(
    ("step", "error_type", "category"),
    [
        (Step(raise_=httpx2.ReadTimeout), "timeout", "timeout"),
        (Step(raise_=httpx2.ConnectError), "openai.APIConnectionError", "connection_error"),
        (fail(401, "invalid_api_key"), "401", "authentication_failed"),
        (fail(403, "permission_denied"), "403", "permission_denied"),
        (fail(400, "invalid_request_error"), "400", "invalid_request"),
        (fail(404, "not_found"), "404", "not_found"),
        (fail(500, "server_error"), "500", "provider_error"),
        (fail(503, "server_error"), "503", "provider_error"),
        (quota_exhausted(), "429", "quota_exhausted"),
    ],
    ids=["timeout", "connection", "401", "403", "400", "404", "500", "503", "quota"],
)
def test_ambiguous_or_final_failures_are_never_retried(telemetry, stub, step, error_type, category):
    """Even with three attempts allowed, only an unambiguous 429 would be retried."""
    fake = FakeOpenAI([step])
    llm = observed(telemetry, fake)
    adapter = OpenAIResponsesTextAdapter(llm, max_attempts=3)
    outcome = adapter.generate_text(text_request())
    assert len(fake.requests) == 1
    (span,) = clients(exported(telemetry, stub))
    assert span["status"] == "ERROR" and span["status_message"] == "" and span["events"] == 0
    assert span["attributes"]["error.type"] == error_type
    assert span["attributes"]["llminsight.usage_source"] == "unknown"
    assert outcome.status == "error" and outcome.error_category == category
    assert outcome.usage is None and len(outcome.attempts) == 1
    assert outcome.attempts[0].error_category == category


def test_provider_error_reaches_create_response_caller_unchanged(telemetry, stub):
    fake = FakeOpenAI([fail(401, "invalid_api_key")])
    with pytest.raises(openai.AuthenticationError):
        respond(observed(telemetry, fake))
    assert len(fake.requests) == 1


def test_rate_limit_is_not_retried_by_default(telemetry, stub):
    fake = FakeOpenAI([fail(429, "rate_limit_exceeded")])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(text_request())
    assert len(fake.requests) == 1
    assert outcome.error_category == "rate_limited"


def test_rate_limit_retry_is_bounded_and_correlated(telemetry, stub):
    fake = FakeOpenAI([fail(429, "rate_limit_exceeded"), response_ok(response_id="resp_SYN_2")])
    adapter = OpenAIResponsesTextAdapter(observed(telemetry, fake), max_attempts=2)
    outcome = adapter.generate_text(text_request())
    assert len(fake.requests) == 2
    first, second = clients(exported(telemetry, stub))
    assert first["attributes"]["llminsight.call_id"] == second["attributes"]["llminsight.call_id"]
    assert (
        first["attributes"]["llminsight.attempt"],
        second["attributes"]["llminsight.attempt"],
    ) == (
        1,
        2,
    )
    assert first["status"] == "ERROR" and first["attributes"]["error.type"] == "429"
    assert "gen_ai.usage.input_tokens" not in first["attributes"]  # no double counting
    assert second["attributes"]["gen_ai.response.id"] == "resp_SYN_2"
    assert outcome.status == "completed" and [r.attempt for r in outcome.attempts] == [1, 2]
    assert outcome.attempts[0].error_category == "rate_limited"
    assert outcome.attempts[1].usage is not None and outcome.attempts[0].usage is None


def test_rate_limit_retries_stop_at_the_attempt_bound(telemetry, stub):
    fake = FakeOpenAI([fail(429, "rate_limit_exceeded")] * 2)
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake), max_attempts=2).generate_text(
        text_request()
    )
    assert len(fake.requests) == 2 and len(clients(exported(telemetry, stub))) == 2
    assert outcome.status == "error" and len(outcome.attempts) == 2


def test_retry_predicate_and_categories():
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")

    def status_error(cls, status, code):
        response = httpx2.Response(status, request=request)
        return cls("m", response=response, body={"code": code})

    rate = status_error(openai.RateLimitError, 429, "rate_limit_exceeded")
    quota = status_error(openai.RateLimitError, 429, "insufficient_quota")
    assert is_unambiguously_retryable(rate)
    assert not is_unambiguously_retryable(quota)
    for ambiguous in (
        openai.APITimeoutError(request=request),
        openai.APIConnectionError(request=request),
        status_error(openai.InternalServerError, 500, None),
    ):
        assert not is_unambiguously_retryable(ambiguous)
    assert error_category(status_error(openai.ConflictError, 409, None)) == "invalid_request"
    assert error_category(status_error(openai.APIStatusError, 418, None)) == "http_error"
    assert error_category(RuntimeError("x")) == "unexpected_error"
    for exc in (rate, quota, RuntimeError("x")):
        assert error_category(exc) in ERROR_CATEGORIES


# ---------------------------------------------------------------- refusals before sending
@pytest.mark.parametrize(
    "kwargs",
    [
        {"stream": True},
        {"stream": False},
        {"stream_options": {"include_usage": True}},
        {"background": True},
        {"conversation": "conv_SYNTHETIC"},
        {"previous_response_id": "resp_SYNTHETIC"},
        {"tools": [{"type": "web_search"}]},
        {"tool_choice": "auto"},
        {"store": True},
        {"store": None},
        # Phase 6B allowlist: arguments that could add unpriced charges or bypass a refusal.
        {"extra_body": {"tools": [{"type": "web_search"}]}},
        {"extra_body": {}},
        {"extra_headers": {"OpenAI-Project": "proj_SYNTHETIC"}},
        {"extra_query": {"x": "1"}},
        {"prompt": {"id": "pmpt_SYNTHETIC"}},
        {"moderation": {"model": "omni-moderation-latest"}},
        {"context_management": [{"type": "compaction"}]},
        {"access_programs": {"cyber": "standard"}},
        {"max_tool_calls": 1},
        {"parallel_tool_calls": False},
        {"include": ["reasoning.encrypted_content"]},
        {"a_future_parameter": True},
    ],
)
def test_unsupported_requests_are_refused_before_sending(telemetry, stub, kwargs):
    fake = FakeOpenAI([])
    with pytest.raises(UnsupportedRequestError):
        respond(observed(telemetry, fake), **kwargs)
    assert fake.requests == []
    assert clients(exported(telemetry, stub)) == []


def test_allowlisted_text_settings_are_sent(telemetry, stub):
    fake = FakeOpenAI([response_ok()])
    respond(
        observed(telemetry, fake),
        instructions=SYSTEM_SENTINEL,
        max_output_tokens=32,
        reasoning={"effort": "none"},
        truncation="disabled",
        metadata={"suite": "SYNTHETIC"},
        timeout=5.0,
    )
    sent = fake.request_json(0)
    assert sent["reasoning"] == {"effort": "none"} and sent["store"] is False
    assert "tools" not in sent and "prompt" not in sent


# ---------------------------------------------------------------- privacy and robustness
def test_no_content_key_or_error_text_leaves_the_process(telemetry, stub, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeOpenAI([response_ok(), fail(401, "invalid_api_key")])
    llm = observed(telemetry, fake)
    adapter = OpenAIResponsesTextAdapter(llm)
    ok = adapter.generate_text(text_request(input_text=PROMPT_SENTINEL))
    bad = adapter.generate_text(text_request(input_text=SYSTEM_SENTINEL))
    assert exported(telemetry, stub)
    wire = b"".join(r.raw_body + r.body for r in stub.received)
    # The SDK's own log records (the `openai` library's DEBUG log prints the provider's
    # request ID; that is the application's logging choice, not SDK output).
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("llm_insight_sdk"))
    texts = repr(ok) + repr(bad) + ours
    for secret in (
        FAKE_API_KEY,
        PROMPT_SENTINEL,
        SYSTEM_SENTINEL,
        COMPLETION_SENTINEL,
        ERROR_SENTINEL,
        REQUEST_ID_SENTINEL,
    ):
        assert secret.encode() not in wire
        assert secret not in texts
    assert ok.provider_response is not None  # kept for the application, outside repr()


def test_observer_errors_do_not_change_the_outcome(telemetry, stub):
    def broken(attempt, outcome):
        raise RuntimeError("observer bug")

    fake = FakeOpenAI([response_ok()])
    result = respond(observed(telemetry, fake), attempt_observer=broken)
    assert isinstance(result, openai.types.responses.Response)
    assert clients(exported(telemetry, stub))[0]["status"] == "UNSET"


def test_outcome_metadata_and_flush(telemetry, stub):
    fake = FakeOpenAI([response_ok(response_id="resp_SYNTHETIC_outcome")])
    outcome = OpenAIResponsesTextAdapter(observed(telemetry, fake)).generate_text(
        text_request(prompt_name="summarize", prompt_version="v2")
    )
    assert outcome.provider == "openai" and outcome.status == "completed"
    assert (outcome.request_model, outcome.response_model) == (MODEL, SNAPSHOT)
    assert outcome.response_id == "resp_SYNTHETIC_outcome" and outcome.service_tier == "default"
    assert outcome.usage is not None and outcome.usage.input_tokens == 1200
    assert outcome.duration_ms >= 0 and outcome.error_category is None
    assert fake.request_json(0)["service_tier"] == "default"
    assert telemetry.force_flush(5000)
    stats = telemetry.stats()
    assert stats.spans_ended == stats.spans_exported == 1
    assert stats.spans_export_failed == 0 and stats.attributes_withheld == 0
    (span,) = clients(stub.spans())
    assert span["attributes"]["gen_ai.prompt.version"] == "v2"


def test_body_fixture_matches_the_sdk_type():
    parsed = openai.types.responses.Response.model_validate(response_body())
    assert parsed.usage is not None and parsed.usage.input_tokens_details.cached_tokens == 1000
