"""Phase 10B (ADR-55): `gen_ai.request.stream` and `gen_ai.response.time_to_first_chunk` from the
SDK, checked at a local OTLP stub. SYNTHETIC; no provider is called and no chunk is recorded."""

from __future__ import annotations

import time

from llm_insight_sdk import instrumentation
from llm_insight_sdk.instrumentation import RequestParams


def exported(telemetry, stub):
    assert telemetry.force_flush(5000)
    return stub.spans()


def attempt(
    telemetry, *, stream=None, chunks=0, wait_s=0.0, call_id="5f0c6e1a-3b7d-4c2e-9a41-000000000101"
):
    with telemetry.inference_span(
        operation="chat",
        provider="llminsight.mock",
        request_model="mock-chat-small",
        call_id=call_id,
        attempt=1,
        params=RequestParams(stream=stream),
    ) as rec:
        time.sleep(wait_s)
        for _ in range(chunks):
            rec.record_first_chunk()


def test_streaming_attempt_records_stream_and_the_first_chunk_once(telemetry, stub):
    attempt(telemetry, stream=True, chunks=3, wait_s=0.05)
    (span,) = exported(telemetry, stub)
    a = span["attributes"]
    assert a["gen_ai.request.stream"] is True
    ttfc = a["gen_ai.response.time_to_first_chunk"]
    # Windows timers tick at about 15.6 ms, so the lower bound leaves one tick of slack.
    assert isinstance(ttfc, float) and 0.03 <= ttfc < 5
    duration_s = (span["end_ns"] - span["start_ns"]) / 1e9
    assert ttfc <= duration_s  # measured inside the attempt


def test_no_chunk_means_no_attribute(telemetry, stub):
    attempt(telemetry, stream=True, chunks=0)
    (span,) = exported(telemetry, stub)
    assert span["attributes"]["gen_ai.request.stream"] is True
    assert "gen_ai.response.time_to_first_chunk" not in span["attributes"]


def test_non_streaming_requests_do_not_set_stream(telemetry, stub):
    attempt(telemetry, stream=False)
    attempt(telemetry, stream=None, call_id="5f0c6e1a-3b7d-4c2e-9a41-000000000102")
    for span in exported(telemetry, stub):
        assert "gen_ai.request.stream" not in span["attributes"]


def test_an_implausible_duration_is_withheld_and_counted(telemetry, stub, monkeypatch):
    before = telemetry.stats().attributes_withheld
    monkeypatch.setattr(
        instrumentation, "MAX_TTFC_SECONDS", -1
    )  # any real duration is out of range
    attempt(telemetry, stream=True, chunks=1)
    (span,) = exported(telemetry, stub)
    assert "gen_ai.response.time_to_first_chunk" not in span["attributes"]
    assert telemetry.stats().attributes_withheld == before + 1
