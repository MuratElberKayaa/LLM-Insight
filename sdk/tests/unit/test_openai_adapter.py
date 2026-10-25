"""OpenAI adapter, fully offline: injected fake transports, exported protobuf checked at the
local OTLP stub. Every response is a SYNTHETIC fixture (tests/openai_fixtures.py) and every
span is labelled `synthetic`. No request can leave the machine (`offline` fixture)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import httpx2
import openai
import pytest

import llm_insight_sdk as lli
from llm_insight_sdk import instrumentation, openai_adapter
from llm_insight_sdk.openai_adapter import (
    ObservedAsyncOpenAIClient,
    ObservedOpenAIClient,
    UnsupportedRequestError,
    classify_openai_error,
    is_retryable,
)
from tests.conftest import PROMPT_SENTINEL, SYSTEM_SENTINEL, free_port, messages
from tests.openai_fixtures import (
    COMPLETION_SENTINEL,
    ERROR_SENTINEL,
    FAKE_API_KEY,
    REQUEST_ID_SENTINEL,
    TOOL_SENTINEL,
    FakeOpenAI,
    NetworkBlocked,
    Step,
    block_network,
    completion_body,
    fail,
    ok,
)
from tests.unit.test_instrumentation import ALLOWED_INFERENCE_KEYS, exported

MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
OPENAI_KEYS = {
    "openai.api.type",
    "openai.request.service_tier",
    "openai.response.service_tier",
    "openai.response.system_fingerprint",
    "server.address",
    "server.port",
    "gen_ai.output.type",
}
TOOLS = [
    {
        "type": "function",
        "function": {"name": "lookup", "description": TOOL_SENTINEL, "parameters": {}},
    }
]


@pytest.fixture(autouse=True)
def offline() -> Iterator[list[str]]:
    attempts: list[str] = []
    with block_network(attempts):
        yield attempts
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def observed(telemetry, fake: FakeOpenAI, **kw) -> ObservedOpenAIClient:
    kw.setdefault("backoff_seconds", 0.0)
    return ObservedOpenAIClient(telemetry, fake.client(), provenance="synthetic", **kw)


def call(llm, **kw):
    kw.setdefault("model", MODEL)
    kw.setdefault("messages", messages())
    kw.setdefault("service_tier", "default")
    return llm.create_chat_completion(**kw)


def attempts_of(spans):
    return sorted(
        (s for s in spans if s["kind"] == "CLIENT"),
        key=lambda s: s["attributes"]["llminsight.attempt"],
    )


# ---------------------------------------------------------------- T09 / T11: success
def test_successful_call_span_shape_and_usage(telemetry, stub):
    fake = FakeOpenAI([ok(response_id="chatcmpl-SYNTHETIC-shape")])
    result = call(
        observed(telemetry, fake),
        prompt_name="summarize",
        prompt_version="v1",
        temperature=0.2,
        max_completion_tokens=400,
        seed=7,
    )
    assert isinstance(result, openai.types.chat.ChatCompletion)
    assert result.choices[0].message.content == COMPLETION_SENTINEL  # returned, not exported

    (span,) = exported(telemetry, stub)
    assert span["name"] == f"chat {MODEL}"
    assert span["kind"] == "CLIENT" and span["status"] == "UNSET" and span["events"] == 0
    a = span["attributes"]
    assert a == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": MODEL,
        "gen_ai.response.model": SNAPSHOT,
        "gen_ai.response.id": "chatcmpl-SYNTHETIC-shape",
        "gen_ai.response.finish_reasons": ["stop"],
        "gen_ai.request.max_tokens": 400,
        "gen_ai.request.temperature": 0.2,
        "gen_ai.request.seed": 7,
        "gen_ai.prompt.name": "summarize",
        "gen_ai.prompt.version": "v1",
        "gen_ai.usage.input_tokens": 1200,
        "gen_ai.usage.output_tokens": 300,
        "gen_ai.usage.cache_read.input_tokens": 1000,
        "gen_ai.usage.reasoning.output_tokens": 200,
        "llminsight.usage_source": "provider_reported",
        "llminsight.call_id": a["llminsight.call_id"],
        "llminsight.attempt": 1,
        "llminsight.provenance": "synthetic",
        "openai.api.type": "chat_completions",
        "openai.request.service_tier": "default",
        "openai.response.service_tier": "default",
        "openai.response.system_fingerprint": "fp_synthetic01",
        "server.address": "api.openai.com",
        "server.port": 443,
    }
    assert uuid.UUID(a["llminsight.call_id"]).version == 4
    assert set(a) <= ALLOWED_INFERENCE_KEYS | OPENAI_KEYS


def test_request_is_passed_through_unchanged_and_sent_once(telemetry, stub):
    fake = FakeOpenAI([ok()])
    call(observed(telemetry, fake), tools=TOOLS, response_format={"type": "json_object"})
    assert len(fake.requests) == 1
    sent = fake.request_json(0)
    assert sent["model"] == MODEL and sent["messages"] == messages()
    assert sent["tools"] == TOOLS and sent["service_tier"] == "default"
    assert str(fake.requests[0].url) == "https://api.openai.com/v1/chat/completions"
    assert fake.requests[0].headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["gen_ai.output.type"] == "json"


def test_auto_service_tier_is_not_recorded_as_a_request_tier(telemetry, stub):
    call(observed(telemetry, FakeOpenAI([ok()])), service_tier="auto")
    (span,) = exported(telemetry, stub)
    assert "openai.request.service_tier" not in span["attributes"]
    assert span["attributes"]["openai.response.service_tier"] == "default"


def test_missing_usage_is_absent_never_estimated(telemetry, stub):
    """T18: no usage object -> no token attribute, usage_source unknown; the long completion
    text is never used to estimate anything."""
    call(observed(telemetry, FakeOpenAI([ok(usage=None)])))
    (span,) = exported(telemetry, stub)
    a = span["attributes"]
    assert not any(k.startswith("gen_ai.usage.") for k in a)
    assert a["llminsight.usage_source"] == "unknown"
    assert "gen_ai.response.id" in a  # the call itself succeeded


def test_unreported_subsets_stay_absent_and_reported_zeros_are_kept(telemetry, stub):
    usage = {
        "prompt_tokens": 50,
        "completion_tokens": 10,
        "total_tokens": 60,
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    call(observed(telemetry, FakeOpenAI([ok(usage=usage)])))
    (span,) = exported(telemetry, stub)
    a = span["attributes"]
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (50, 10)
    assert a["gen_ai.usage.reasoning.output_tokens"] == 0  # reported 0 is kept
    assert "gen_ai.usage.cache_read.input_tokens" not in a  # no prompt details: not reported
    assert "gen_ai.usage.cache_write.input_tokens" not in a


def test_cache_write_tokens_are_mapped_when_reported(telemetry, stub):
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "total_tokens": 105,
        "prompt_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 30},
    }
    call(observed(telemetry, FakeOpenAI([ok(usage=usage)])))
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["gen_ai.usage.cache_read.input_tokens"] == 20
    assert span["attributes"]["gen_ai.usage.cache_write.input_tokens"] == 30


def test_response_without_service_tier_records_none(telemetry, stub):
    call(observed(telemetry, FakeOpenAI([ok(service_tier=None)])), service_tier=None)
    (span,) = exported(telemetry, stub)
    assert "openai.response.service_tier" not in span["attributes"]


# ---------------------------------------------------------------- T12 / T14: failures, retries
def test_rate_limit_then_success_is_two_physical_attempts(telemetry, stub):
    fake = FakeOpenAI([fail(429, "rate_limit_error"), ok(response_id="chatcmpl-SYNTHETIC-2nd")])
    result = call(observed(telemetry, fake, max_attempts=3))
    assert result.id == "chatcmpl-SYNTHETIC-2nd"
    assert len(fake.requests) == 2  # the OpenAI client's own retries are off

    first, second = attempts_of(exported(telemetry, stub))
    assert first["attributes"]["llminsight.call_id"] == second["attributes"]["llminsight.call_id"]
    assert [
        first["attributes"]["llminsight.attempt"],
        second["attributes"]["llminsight.attempt"],
    ] == [1, 2]
    assert first["status"] == "ERROR" and first["status_message"] == ""
    assert first["attributes"]["error.type"] == "429"
    assert "gen_ai.response.id" not in first["attributes"]  # no response, no response ID
    assert first["attributes"]["llminsight.usage_source"] == "unknown"
    assert second["status"] == "UNSET"
    assert second["attributes"]["gen_ai.response.id"] == "chatcmpl-SYNTHETIC-2nd"
    assert second["parent_span_id"] == first["parent_span_id"]


def test_provider_errors_reach_the_caller_after_the_last_attempt(telemetry, stub):
    fake = FakeOpenAI([fail(500), fail(502), fail(503)])
    with pytest.raises(openai.InternalServerError) as exc:
        call(observed(telemetry, fake, max_attempts=3))
    assert exc.value.status_code == 503  # the last error, unchanged
    spans = attempts_of(exported(telemetry, stub))
    assert [s["attributes"]["error.type"] for s in spans] == ["500", "502", "503"]
    assert all(s["status"] == "ERROR" for s in spans)


@pytest.mark.parametrize(
    "status,error",
    [
        (400, openai.BadRequestError),
        (401, openai.AuthenticationError),
        (403, openai.PermissionDeniedError),
        (404, openai.NotFoundError),
        (422, openai.UnprocessableEntityError),
    ],
)
def test_non_retryable_errors_are_one_attempt(telemetry, stub, status, error):
    fake = FakeOpenAI([fail(status)])
    with pytest.raises(error):
        call(observed(telemetry, fake, max_attempts=3))
    assert len(fake.requests) == 1
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["error.type"] == str(status)


def test_timeout_is_classified_retried_and_raised(telemetry, stub):
    fake = FakeOpenAI([Step(raise_=httpx2.ReadTimeout), Step(raise_=httpx2.ReadTimeout)])
    with pytest.raises(openai.APITimeoutError):
        call(observed(telemetry, fake, max_attempts=2), timeout=0.5)
    spans = attempts_of(exported(telemetry, stub))
    assert [s["attributes"]["error.type"] for s in spans] == ["timeout", "timeout"]
    assert len(fake.requests) == 2


def test_timeout_then_success(telemetry, stub):
    fake = FakeOpenAI([Step(raise_=httpx2.ConnectTimeout), ok()])
    assert call(observed(telemetry, fake, max_attempts=2)).model == SNAPSHOT
    first, second = attempts_of(exported(telemetry, stub))
    assert first["attributes"]["error.type"] == "timeout"
    assert second["attributes"]["gen_ai.usage.input_tokens"] == 1200


def test_connection_error_type(telemetry, stub):
    with pytest.raises(openai.APIConnectionError):
        call(observed(telemetry, FakeOpenAI([Step(raise_=httpx2.ConnectError)]), max_attempts=1))
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["error.type"] == "openai.APIConnectionError"


def test_each_logical_call_gets_a_fresh_call_id(telemetry, stub):
    llm = observed(telemetry, FakeOpenAI([ok(), ok()]))
    call(llm)
    call(llm)
    ids = {s["attributes"]["llminsight.call_id"] for s in exported(telemetry, stub)}
    assert len(ids) == 2


def test_wrapped_client_keeps_its_own_retry_setting(telemetry):
    fake = FakeOpenAI([])
    client = fake.client()
    ObservedOpenAIClient(telemetry, client, provenance="synthetic")
    assert client.max_retries == 2  # the caller's client object is not modified


@pytest.mark.parametrize(
    "exc,expected",
    [
        (
            openai.APITimeoutError(request=httpx2.Request("POST", "https://api.openai.com")),
            "timeout",
        ),
        (
            openai.APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com")),
            "openai.APIConnectionError",
        ),
        (openai.OpenAIError("x"), "openai.OpenAIError"),
        (ValueError("x"), "ValueError"),
    ],
)
def test_classify_openai_error(exc, expected):
    assert classify_openai_error(exc) == expected


@pytest.mark.parametrize(
    "status,retry",
    [
        (408, True),
        (409, True),
        (429, True),
        (500, True),
        (599, True),
        (400, False),
        (401, False),
        (404, False),
    ],
)
def test_retry_rule_matches_the_openai_sdk(status, retry):
    request = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")
    err = openai.APIStatusError("x", response=httpx2.Response(status, request=request), body=None)
    assert is_retryable(err) is retry
    assert is_retryable(ValueError()) is False


# ---------------------------------------------------------------- T10: context
def test_attempt_spans_are_children_of_the_current_span(telemetry, stub):
    llm = observed(telemetry, FakeOpenAI([fail(429), ok()]))
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        call(llm)
    spans = exported(telemetry, stub)
    root_id = format(root.get_span_context().span_id, "016x")
    trace_id = format(root.get_span_context().trace_id, "032x")
    children = [s for s in spans if s["kind"] == "CLIENT"]
    assert len(children) == 2
    assert all(s["parent_span_id"] == root_id and s["trace_id"] == trace_id for s in children)


def test_async_calls_keep_their_own_context_and_identity(telemetry, stub):
    fake = FakeOpenAI([ok(), ok(), fail(429), ok()])
    llm = ObservedAsyncOpenAIClient(
        telemetry, fake.async_client(), provenance="synthetic", max_attempts=2, backoff_seconds=0.0
    )

    async def main():
        with telemetry.tracer.start_as_current_span("root") as root:
            await asyncio.gather(
                *(
                    llm.create_chat_completion(
                        model=MODEL, messages=messages(), service_tier="default"
                    )
                    for _ in range(3)
                )
            )
        return root

    root = asyncio.run(main())
    spans = [s for s in exported(telemetry, stub) if s["kind"] == "CLIENT"]
    assert len(spans) == 4 and len(fake.requests) == 4
    root_id = format(root.get_span_context().span_id, "016x")
    assert all(s["parent_span_id"] == root_id for s in spans)
    by_call: dict[str, list[int]] = {}
    for s in spans:
        by_call.setdefault(s["attributes"]["llminsight.call_id"], []).append(
            s["attributes"]["llminsight.attempt"]
        )
    assert sorted(sorted(v) for v in by_call.values()) == [[1], [1], [1, 2]]


def test_async_errors_reach_the_caller(telemetry, stub):
    llm = ObservedAsyncOpenAIClient(
        telemetry, FakeOpenAI([fail(400)]).async_client(), provenance="synthetic"
    )
    with pytest.raises(openai.BadRequestError):
        asyncio.run(llm.create_chat_completion(model=MODEL, messages=messages()))
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["error.type"] == "400"


# ---------------------------------------------------------------- T15 / T16: no secrets, no content
def test_no_content_key_header_or_error_text_leaves_the_process(telemetry, stub, caplog):
    caplog.set_level("DEBUG")
    fake = FakeOpenAI([fail(429), ok(), fail(400)])
    llm = observed(telemetry, fake, max_attempts=2)
    call(
        llm,
        tools=TOOLS,
        user="user-SENTINEL@example.com",
        extra_headers={"X-Custom-Secret": "hdr-SENTINEL-value"},
    )
    with pytest.raises(openai.BadRequestError):
        call(llm, max_attempts=1)
    assert telemetry.force_flush(5000)
    raw = b"".join(r.body for r in stub.received)
    # Our own logger only: the `openai` library's DEBUG logs are the application's concern
    # (they print OpenAI request IDs; sdk/README.md says so).
    sdk_logs = " ".join(
        r.getMessage() for r in caplog.records if r.name.startswith("llm_insight_sdk")
    )
    openai_logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("openai"))
    assert "lli_" not in openai_logs  # the LLM Insight project key never reaches OpenAI
    assert raw  # something was exported
    for secret in (
        PROMPT_SENTINEL,
        SYSTEM_SENTINEL,
        COMPLETION_SENTINEL,
        TOOL_SENTINEL,
        ERROR_SENTINEL,
        REQUEST_ID_SENTINEL,
        FAKE_API_KEY,
        "user-SENTINEL",
        "hdr-SENTINEL",
        "Bearer",
        "authorization",
    ):
        assert secret.encode() not in raw, secret
        assert secret not in sdk_logs, secret
    headers = " ".join(f"{k}: {v}" for r in stub.received for k, v in r.headers.items())
    assert FAKE_API_KEY not in headers  # only the LLM Insight project key goes to the backend


def test_provenance_is_the_declared_one(telemetry, stub):
    call(observed(telemetry, FakeOpenAI([ok()])))
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["llminsight.provenance"] == "synthetic"


# ---------------------------------------------------------------- refusals (nothing sent)
def test_streaming_is_refused_before_any_request(telemetry, stub):
    fake = FakeOpenAI([ok()])
    llm = observed(telemetry, fake)
    with pytest.raises(UnsupportedRequestError):
        call(llm, stream=True)
    with pytest.raises(UnsupportedRequestError):
        call(llm, stream_options={"include_usage": True})
    assert fake.requests == []


def test_real_provenance_is_refused_with_a_mock_transport(telemetry):
    with pytest.raises(lli.ConfigError, match="mock transport"):
        ObservedOpenAIClient(telemetry, FakeOpenAI([]).client(), provenance="real")
    with pytest.raises(lli.ConfigError, match="mock transport"):
        ObservedAsyncOpenAIClient(telemetry, FakeOpenAI([]).async_client(), provenance="real")


@pytest.mark.parametrize("provenance", ["unknown", "REAL", "", None])
def test_provenance_must_be_declared_explicitly(telemetry, provenance):
    with pytest.raises(lli.ConfigError, match="provenance"):
        ObservedOpenAIClient(telemetry, FakeOpenAI([]).client(), provenance=provenance)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://eu.api.openai.com/v1",  # regional endpoint (different price)
        "http://api.openai.com/v1",
        "https://api.openai.com:8443/v1",
        "https://proxy.example.invalid/v1",
        "https://user:pw@api.openai.com/v1",
    ],
)
def test_only_the_global_openai_endpoint_is_supported(telemetry, base_url):
    client = openai.OpenAI(
        api_key=FAKE_API_KEY,
        base_url=base_url,
        http_client=httpx2.Client(transport=httpx2.MockTransport(lambda r: None)),
    )
    with pytest.raises(lli.ConfigError, match="client"):
        ObservedOpenAIClient(telemetry, client, provenance="synthetic")


def test_azure_and_wrong_client_types_are_refused(telemetry):
    azure = openai.AzureOpenAI(
        api_key=FAKE_API_KEY, api_version="2024-10-21", azure_endpoint="https://example.invalid"
    )
    with pytest.raises(lli.ConfigError):
        ObservedOpenAIClient(telemetry, azure, provenance="synthetic")
    with pytest.raises(lli.ConfigError):
        ObservedOpenAIClient(telemetry, FakeOpenAI([]).async_client(), provenance="synthetic")
    with pytest.raises(lli.ConfigError):
        ObservedAsyncOpenAIClient(telemetry, FakeOpenAI([]).client(), provenance="synthetic")


@pytest.mark.parametrize("max_attempts", [0, 11, True])
def test_attempt_bounds(telemetry, max_attempts):
    with pytest.raises(ValueError):
        ObservedOpenAIClient(
            telemetry, FakeOpenAI([]).client(), provenance="synthetic", max_attempts=max_attempts
        )


# ---------------------------------------------------------------- fail-open (T13 for the adapter)
def test_broken_span_creation_never_breaks_the_call(telemetry, stub, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("telemetry bug")

    monkeypatch.setattr(instrumentation, "inference_span", broken)
    fake = FakeOpenAI([fail(429), ok()])
    assert call(observed(telemetry, fake, max_attempts=2)).model == SNAPSHOT
    assert len(fake.requests) == 2  # retries still work without spans


def test_broken_response_recording_never_breaks_the_call(telemetry, stub, monkeypatch):
    def broken(completion):
        raise RuntimeError("telemetry bug")

    monkeypatch.setattr(openai_adapter, "usage_from", broken)
    assert call(observed(telemetry, FakeOpenAI([ok()]))).model == SNAPSHOT
    (span,) = exported(telemetry, stub)
    assert span["status"] == "UNSET"  # the call succeeded; telemetry is just incomplete


def test_provider_error_survives_broken_span_finishing(telemetry, stub, monkeypatch):
    def broken(self, exc):
        raise RuntimeError("telemetry bug while finishing")

    monkeypatch.setattr(instrumentation.InferenceRecorder, "_finish", broken)
    with pytest.raises(openai.RateLimitError):  # the provider error, not the telemetry bug
        call(observed(telemetry, FakeOpenAI([fail(429)]), max_attempts=1))


def test_backend_down_does_not_affect_the_call(make_config):
    # Long schedule delay: the failed export happens inside force_flush, not before it.
    t = lli.init(
        make_config(
            f"http://127.0.0.1:{free_port()}/v1/traces",
            export_timeout_seconds=0.5,
            schedule_delay_millis=60_000,
        )
    )
    try:
        result = call(observed(t, FakeOpenAI([ok()])))
        assert result.model == SNAPSHOT
        assert t.force_flush(3000) is False  # delivery failed, the application did not
    finally:
        t.shutdown(timeout_millis=2000)


# ---------------------------------------------------------------- network prohibition
def test_the_offline_guard_really_blocks_a_real_client(telemetry, offline):
    """Without the fake transport the official client would go to api.openai.com; the guard
    makes that impossible, so no test can spend money or leak a request."""
    real_client = openai.OpenAI(api_key=FAKE_API_KEY, max_retries=0)
    with pytest.raises(openai.APIConnectionError):
        real_client.chat.completions.create(model=MODEL, messages=messages())
    assert offline == ["api.openai.com"]
    offline.clear()  # expected; the fixture's final check covers everything else


def test_the_guard_blocks_raw_sockets_too(offline):
    import socket

    with pytest.raises(NetworkBlocked), socket.socket() as s:
        s.connect(("203.0.113.1", 443))  # TEST-NET-3: never routable
    assert offline == ["203.0.113.1"]
    offline.clear()


def test_fixtures_are_labelled_synthetic():
    body = completion_body()
    assert body["id"].startswith("chatcmpl-SYNTHETIC-")
    assert "SYNTHETIC" in ok().body["id"]
