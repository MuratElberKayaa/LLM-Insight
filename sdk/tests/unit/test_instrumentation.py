"""Mock LLM instrumentation: exported protobuf spans, checked at a local OTLP stub.

Every span here is SYNTHETIC; nothing calls a real provider.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest

import llm_insight_sdk as lli
from llm_insight_sdk.instrumentation import RequestParams, TokenUsage, classify_error
from llm_insight_sdk.mock import (
    MOCK_PROVIDER,
    MockLLMProvider,
    MockProviderError,
    MockRateLimitError,
    MockServerError,
    MockTimeoutError,
    ObservedMockClient,
    Scenario,
)
from tests.conftest import PROMPT_SENTINEL, SYSTEM_SENTINEL, messages

MODEL = "mock-chat-small"
RESPONSE_MODEL = "mock-chat-small-2026-10-01"
ALLOWED_INFERENCE_KEYS = {
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.response.id",
    "gen_ai.response.finish_reasons",
    "gen_ai.request.max_tokens",
    "gen_ai.request.temperature",
    "gen_ai.request.top_p",
    "gen_ai.request.seed",
    "gen_ai.prompt.name",
    "gen_ai.prompt.version",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_write.input_tokens",
    "gen_ai.usage.reasoning.output_tokens",
    "error.type",
    "llminsight.call_id",
    "llminsight.attempt",
    "llminsight.provenance",
    "llminsight.usage_source",
}


def exported(telemetry, stub):
    assert telemetry.force_flush(5000)
    return stub.spans()


def inference(spans):
    return [s for s in spans if s["kind"] == "CLIENT"]


def test_success_span_has_the_locked_genai_shape(telemetry, client, stub):
    with telemetry.tracer.start_as_current_span("demo.request"):
        result = client.chat(
            model=MODEL,
            messages=messages(),
            prompt_name="summarize",
            prompt_version="v1",
            params=RequestParams(max_tokens=256, temperature=0.2, top_p=1.0, seed=7),
        )
    assert result.text == "SYNTHETIC mock completion"
    (span,) = inference(exported(telemetry, stub))
    assert span["name"] == f"chat {MODEL}"
    assert span["kind"] == "CLIENT"
    assert span["status"] == "UNSET"
    assert span["events"] == 0
    a = span["attributes"]
    uuid.UUID(a.pop("llminsight.call_id"), version=4)
    assert a.pop("gen_ai.response.id").startswith("mockresp_")
    assert a == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": MOCK_PROVIDER,
        "gen_ai.request.model": MODEL,
        "gen_ai.response.model": RESPONSE_MODEL,
        "gen_ai.response.finish_reasons": ["stop"],
        "gen_ai.request.max_tokens": 256,
        "gen_ai.request.temperature": 0.2,
        "gen_ai.request.top_p": 1.0,
        "gen_ai.request.seed": 7,
        "gen_ai.prompt.name": "summarize",
        "gen_ai.prompt.version": "v1",
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 48,
        "gen_ai.usage.cache_read.input_tokens": 32,
        "gen_ai.usage.cache_write.input_tokens": 0,  # provider-reported 0 is explicit
        "gen_ai.usage.reasoning.output_tokens": 0,
        "llminsight.attempt": 1,
        "llminsight.provenance": "synthetic",
        "llminsight.usage_source": "provider_reported",
    }
    assert span["scope"] == ("llm_insight_sdk", lli.__version__)


def test_resource_is_explicit_and_ignores_otel_resource_env(stub, make_config, monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "user.id=alice,host.name=secret-host")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "env-service-name")
    t = lli.init(make_config(stub.endpoint))
    with t.tracer.start_as_current_span("root"):
        pass
    (span,) = exported(t, stub)
    assert span["resource"] == {
        "service.name": "sdk-unit-test",
        "service.version": "0.1.0",
        "deployment.environment.name": "test",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": "1.45.1",
        "llminsight.sdk.version": lli.__version__,
    }


def test_parent_child_ids_and_multiple_children(telemetry, client, stub):
    with telemetry.tracer.start_as_current_span("demo.request") as root:
        for _ in range(3):
            client.chat(model=MODEL, messages=messages())
    spans = exported(telemetry, stub)
    root_ctx = root.get_span_context()
    (root_span,) = [s for s in spans if s["parent_span_id"] is None]
    children = inference(spans)
    assert len(children) == 3
    assert root_span["trace_id"] == format(root_ctx.trace_id, "032x")
    assert root_span["span_id"] == format(root_ctx.span_id, "016x")
    assert {c["trace_id"] for c in children} == {root_span["trace_id"]}
    assert {c["parent_span_id"] for c in children} == {root_span["span_id"]}
    assert len({c["span_id"] for c in children}) == 3
    assert len({c["attributes"]["llminsight.call_id"] for c in children}) == 3
    for s in spans:
        assert len(s["trace_id"]) == 32 and int(s["trace_id"], 16) != 0
        assert len(s["span_id"]) == 16 and int(s["span_id"], 16) != 0


def test_calls_without_a_parent_start_their_own_trace(telemetry, client, stub):
    client.chat(model=MODEL, messages=messages())
    client.chat(model=MODEL, messages=messages())
    spans = inference(exported(telemetry, stub))
    assert all(s["parent_span_id"] is None for s in spans)
    assert len({s["trace_id"] for s in spans}) == 2


@pytest.mark.parametrize(
    ("scenario", "error", "error_type"),
    [
        (Scenario.RATE_LIMITED, MockRateLimitError, "429"),
        (Scenario.SERVER_ERROR, MockServerError, "500"),
        (Scenario.TIMEOUT, MockTimeoutError, "timeout"),
    ],
)
def test_failed_inference(telemetry, stub, scenario, error, error_type):
    client = ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0), timeout_seconds=0.01)
    with telemetry.tracer.start_as_current_span("demo.request"), pytest.raises(error):
        client.chat(model=MODEL, messages=messages(), scenario=scenario)
    spans = exported(telemetry, stub)
    (span,) = inference(spans)
    assert span["status"] == "ERROR"
    assert span["status_message"] == ""  # no description: it could quote content (R21)
    assert span["events"] == 0  # the exception (message, stacktrace) is not recorded
    a = span["attributes"]
    assert a["error.type"] == error_type
    assert a["llminsight.usage_source"] == "unknown"
    assert not any(k.startswith("gen_ai.usage.") for k in a)
    assert "gen_ai.response.id" not in a
    assert a["llminsight.provenance"] == "synthetic"
    root = next(s for s in spans if s["kind"] != "CLIENT")
    assert root["status"] == "UNSET"  # the app's span is the app's business


def test_retry_is_a_separate_physical_attempt(telemetry, client, stub):
    with telemetry.tracer.start_as_current_span("demo.request") as root:
        result = client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS)
    assert result.response_model == RESPONSE_MODEL
    first, second = sorted(
        inference(exported(telemetry, stub)), key=lambda s: s["attributes"]["llminsight.attempt"]
    )
    a1, a2 = first["attributes"], second["attributes"]
    assert a1["llminsight.call_id"] == a2["llminsight.call_id"]
    assert (a1["llminsight.attempt"], a2["llminsight.attempt"]) == (1, 2)
    assert first["span_id"] != second["span_id"]
    assert {first["parent_span_id"], second["parent_span_id"]} == {
        format(root.get_span_context().span_id, "016x")
    }
    assert first["status"] == "ERROR" and a1["error.type"] == "429"
    assert "gen_ai.response.id" not in a1
    assert second["status"] == "UNSET" and a2["gen_ai.response.id"].startswith("mockresp_")
    assert second["start_ns"] >= first["end_ns"]


def test_retries_exhausted_raise_after_the_last_attempt(telemetry, client, stub):
    with pytest.raises(MockRateLimitError):
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.RATE_LIMITED)
    spans = inference(exported(telemetry, stub))
    assert sorted(s["attributes"]["llminsight.attempt"] for s in spans) == [1, 2, 3]
    assert len({s["attributes"]["llminsight.call_id"] for s in spans}) == 1
    assert len({s["span_id"] for s in spans}) == 3


def test_max_attempts_one_means_no_retry(telemetry, stub):
    client = ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0))
    with pytest.raises(MockRateLimitError):
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS)
    assert len(inference(exported(telemetry, stub))) == 1


def test_non_retryable_errors_are_not_retried(telemetry, stub):
    def attempt(n: int) -> None:
        with telemetry.inference_span(
            operation="chat",
            provider=MOCK_PROVIDER,
            request_model=MODEL,
            call_id=lli.new_call_id(),
            attempt=n,
        ):
            raise ValueError(PROMPT_SENTINEL)  # message must not be exported

    with pytest.raises(ValueError, match=PROMPT_SENTINEL):
        lli.call_with_retries(attempt, max_attempts=3, is_retryable=lambda e: False)
    (span,) = inference(exported(telemetry, stub))
    assert span["attributes"]["error.type"] == "ValueError"
    assert span["events"] == 0
    assert all(PROMPT_SENTINEL.encode() not in r.body for r in stub.received)


def test_response_and_call_ids_are_unique(telemetry, client, stub):
    for _ in range(5):
        client.chat(model=MODEL, messages=messages())
    spans = inference(exported(telemetry, stub))
    assert len({s["attributes"]["gen_ai.response.id"] for s in spans}) == 5
    assert len({s["attributes"]["llminsight.call_id"] for s in spans}) == 5


def test_no_content_is_exported(telemetry, client, stub):
    with telemetry.tracer.start_as_current_span("demo.request"):
        client.chat(model=MODEL, messages=messages())
        with pytest.raises(MockRateLimitError):
            client.chat(model=MODEL, messages=messages(), scenario=Scenario.RATE_LIMITED)
    spans = exported(telemetry, stub)
    for request in stub.received:
        for sentinel in (PROMPT_SENTINEL, SYSTEM_SENTINEL, "SYNTHETIC mock completion"):
            assert sentinel.encode() not in request.body
    for s in inference(spans):
        assert set(s["attributes"]) <= ALLOWED_INFERENCE_KEYS
        for denied in (
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gen_ai.system_instructions",
            "gen_ai.tool.definitions",
        ):
            assert denied not in s["attributes"]


def test_every_mock_span_is_synthetic_and_never_real(telemetry, client, stub):
    for scenario in Scenario:
        with contextlib.suppress(MockProviderError):  # failure scenarios raise
            client.chat(model=MODEL, messages=messages(), scenario=scenario)
    spans = inference(exported(telemetry, stub))
    assert len(spans) >= len(Scenario)
    assert {s["attributes"]["llminsight.provenance"] for s in spans} == {"synthetic"}
    assert {s["attributes"]["gen_ai.provider.name"] for s in spans} == {MOCK_PROVIDER}
    for s in spans:
        assert not s["attributes"].get("gen_ai.response.id", "mockresp_").startswith("chatcmpl")
        assert "real" not in s["attributes"].values()
        assert not s["attributes"]["gen_ai.request.model"].startswith(("gpt", "o1", "o3"))


def test_no_cost_attribute_is_ever_emitted(telemetry, client, stub):
    client.chat(model=MODEL, messages=messages(), scenario=Scenario.DELAYED)
    (span,) = inference(exported(telemetry, stub))
    assert not any("cost" in k or "price" in k for k in span["attributes"])


def test_invalid_metadata_is_withheld_and_counted(telemetry, stub):
    client = ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0))
    client.chat(
        model="mock chat with spaces",
        messages=messages(),
        prompt_name="contact alice@example.com",
        prompt_version="sk-" + "a" * 24,
    )
    (span,) = inference(exported(telemetry, stub))
    a = span["attributes"]
    assert span["name"] == "chat"  # semconv fallback without a usable model
    assert "gen_ai.request.model" not in a
    assert "gen_ai.prompt.name" not in a
    assert "gen_ai.prompt.version" not in a
    assert "gen_ai.response.model" not in a  # derived from the invalid model name
    assert telemetry.stats().attributes_withheld == 4
    assert "alice" not in str(stub.received[0].body)


def test_recorder_never_raises_on_bad_values(telemetry, stub):
    with telemetry.inference_span(
        operation="chat",
        provider=MOCK_PROVIDER,
        request_model=MODEL,
        call_id="not-a-uuid",
        attempt=0,
    ) as rec:
        rec.record_response(
            response_model=123,  # type: ignore[arg-type]
            response_id="has space",
            finish_reasons=["stop", "bad reason"],
            usage=TokenUsage(input_tokens=-1, output_tokens="12"),  # type: ignore[arg-type]
        )
    (span,) = inference(exported(telemetry, stub))
    a = span["attributes"]
    for key in (
        "llminsight.call_id",
        "llminsight.attempt",
        "gen_ai.response.model",
        "gen_ai.response.id",
        "gen_ai.response.finish_reasons",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
    ):
        assert key not in a
    assert a["llminsight.usage_source"] == "unknown"


def test_unknown_operation_is_not_labelled_as_inference(telemetry, stub):
    with telemetry.inference_span(
        operation="summarize",
        provider=MOCK_PROVIDER,
        request_model=MODEL,
        call_id=lli.new_call_id(),
        attempt=1,
    ):
        pass
    (span,) = exported(telemetry, stub)
    assert "gen_ai.operation.name" not in span["attributes"]
    assert span["name"] == "inference"


def test_async_calls_keep_their_parent(telemetry, stub):
    client = ObservedMockClient(telemetry, MockLLMProvider(time_scale=1.0), max_attempts=3)

    async def run():
        with telemetry.tracer.start_as_current_span("demo.async-request") as root:
            results = await asyncio.gather(
                client.achat(model=MODEL, messages=messages()),
                client.achat(model=MODEL, messages=messages(), scenario=Scenario.DELAYED),
                client.achat(
                    model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS
                ),
            )
        return root, results

    root, results = asyncio.run(run())
    assert len(results) == 3
    spans = inference(exported(telemetry, stub))
    assert len(spans) == 4  # retry scenario has two attempts
    assert {s["parent_span_id"] for s in spans} == {format(root.get_span_context().span_id, "016x")}
    delayed = next(s for s in spans if s["attributes"].get("gen_ai.usage.input_tokens") == 480)
    assert delayed["end_ns"] - delayed["start_ns"] >= 250_000_000  # deterministic 300 ms latency
    assert delayed["attributes"]["gen_ai.usage.reasoning.output_tokens"] == 96


class AppDefinedError(Exception):
    pass


class BadCodeError(Exception):
    error_type = "has spaces in it"  # not an identifier: falls back to the class name


def test_classify_error():
    assert classify_error(MockRateLimitError("x")) == "429"
    assert classify_error(TimeoutError("x")) == "TimeoutError"
    assert classify_error(asyncio.CancelledError()) == "cancelled"
    assert classify_error(AppDefinedError()) == "tests.unit.test_instrumentation.AppDefinedError"
    assert classify_error(BadCodeError()) == "tests.unit.test_instrumentation.BadCodeError"

    class Local(Exception):  # qualname contains "<locals>": not an identifier
        pass

    assert classify_error(Local()) == "_OTHER"


def test_mock_provider_is_deterministic():
    p = MockLLMProvider(time_scale=0.0)
    a = p.complete(model=MODEL, scenario=Scenario.SUCCESS, attempt=1, timeout_s=1)
    b = p.complete(model=MODEL, scenario=Scenario.SUCCESS, attempt=1, timeout_s=1)
    assert (a.usage, a.response_model, a.text) == (b.usage, b.response_model, b.text)
    assert a.response_id != b.response_id  # unique per physical attempt
    with pytest.raises(MockRateLimitError):
        p.complete(model=MODEL, scenario=Scenario.RETRY_THEN_SUCCESS, attempt=1, timeout_s=1)
    assert p.complete(model=MODEL, scenario=Scenario.RETRY_THEN_SUCCESS, attempt=2, timeout_s=1)


def test_retry_helper_bounds():
    with pytest.raises(ValueError, match="max_attempts"):
        lli.call_with_retries(lambda n: n, max_attempts=0, is_retryable=lambda e: True)
    with pytest.raises(ValueError, match="max_attempts"):
        lli.call_with_retries(lambda n: n, max_attempts=11, is_retryable=lambda e: True)
