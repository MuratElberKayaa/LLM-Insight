"""Phase 2C: the FastAPI demo's request spans are the parents of its inference spans (T10).

The demo runs in-process under uvicorn and exports to the local OTLP stub. Every LLM call is
SYNTHETIC and offline (mock provider, or the real `openai` client on an in-process fake
transport); `block_network` proves nothing leaves the machine.
"""

from __future__ import annotations

import sys
import time
import urllib.error
from collections.abc import Iterator
from pathlib import Path

import pytest

import llm_insight_sdk as lli
from tests.conftest import free_port
from tests.openai_fixtures import block_network

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import web  # noqa: E402

SECRET_TEXT = "USER-TEXT-SENTINEL-never-exported-41b7"
ROOT_KEYS = {
    "http.request.method",
    "http.route",
    "http.response.status_code",
    "url.scheme",
    "llminsight.provenance",
}


@pytest.fixture
def demo(telemetry) -> Iterator[web.RunningServer]:
    attempts: list[str] = []
    with block_network(attempts):
        running = web.serve_in_thread(web.create_app(telemetry, mock_time_scale=0.0))
        try:
            yield running
        finally:
            running.stop()
    assert attempts == [], f"the demo tried to reach the network: {attempts}"


def exported(stub, telemetry, trace_id: str) -> list[dict]:
    assert telemetry.force_flush(5000)
    return [s for s in stub.spans() if s["trace_id"] == trace_id]


def split(spans: list[dict]) -> tuple[dict, list[dict]]:
    roots = [s for s in spans if s["parent_span_id"] is None]
    assert len(roots) == 1, spans
    return roots[0], sorted(
        (s for s in spans if s["parent_span_id"] is not None),
        key=lambda s: (
            s["attributes"].get("llminsight.call_id", ""),
            s["attributes"].get("llminsight.attempt", 0),
        ),
    )


@pytest.mark.parametrize(
    ("route", "children"),
    [
        ("/v1/summarize", 1),
        ("/v1/summarize/multi", 3),
        ("/v1/summarize/failure", 1),
        ("/v1/summarize/retry", 2),
        ("/v1/summarize/openai", 1),
    ],
)
def test_t10_request_span_is_the_root_of_every_inference_span(
    demo, stub, telemetry, route, children
):
    out = web.call(demo.url, route, SECRET_TEXT)
    assert out["http_status"] == 200 and out["provenance"] == "synthetic"
    root, kids = split(exported(stub, telemetry, out["trace_id"]))
    assert root["kind"] == "SERVER" and root["name"] == f"POST {route}"
    assert root["attributes"] == {
        "http.request.method": "POST",
        "http.route": route,
        "http.response.status_code": 200,
        "url.scheme": "http",
        "llminsight.provenance": "synthetic",
    }
    assert len(kids) == children
    for kid in kids:
        assert kid["kind"] == "CLIENT"
        assert kid["trace_id"] == root["trace_id"] == out["trace_id"]
        assert kid["parent_span_id"] == root["span_id"]  # direct children of the request
        assert kid["attributes"]["llminsight.provenance"] == "synthetic"
        assert root["start_ns"] <= kid["start_ns"] <= kid["end_ns"] <= root["end_ns"]
    assert len({k["span_id"] for k in kids} | {root["span_id"]}) == children + 1


def test_failure_is_an_error_child_and_a_handled_answer(demo, stub, telemetry):
    out = web.call(demo.url, "/v1/summarize/failure")
    assert out["fallback"] is True and out["summary"] is None
    root, (kid,) = split(exported(stub, telemetry, out["trace_id"]))
    assert kid["status"] == "ERROR" and kid["attributes"]["error.type"] == "500"
    assert root["status"] == "UNSET"  # the application answered 200


def test_recovered_retry_is_two_physical_attempts_of_one_call(demo, stub, telemetry):
    out = web.call(demo.url, "/v1/summarize/retry")
    _, kids = split(exported(stub, telemetry, out["trace_id"]))
    first, second = kids
    assert first["attributes"]["llminsight.call_id"] == second["attributes"]["llminsight.call_id"]
    assert (
        first["attributes"]["llminsight.attempt"],
        second["attributes"]["llminsight.attempt"],
    ) == (1, 2)
    assert first["status"] == "ERROR" and first["attributes"]["error.type"] == "429"
    assert second["status"] != "ERROR"
    assert "gen_ai.usage.input_tokens" not in first["attributes"]  # failed: no usage
    assert second["attributes"]["gen_ai.usage.input_tokens"] > 0


def test_offline_openai_call_carries_provider_usage(demo, stub, telemetry):
    out = web.call(demo.url, "/v1/summarize/openai")
    _, (kid,) = split(exported(stub, telemetry, out["trace_id"]))
    a = kid["attributes"]
    assert kid["name"] == "chat gpt-5.4-mini"
    assert a["gen_ai.provider.name"] == "openai"
    assert a["gen_ai.response.model"] == web.OPENAI_SNAPSHOT
    assert a["openai.response.service_tier"] == "default"
    assert a["server.address"] == "api.openai.com"
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (1200, 300)
    assert a["gen_ai.usage.cache_read.input_tokens"] == 1000
    assert a["gen_ai.usage.reasoning.output_tokens"] == 200


def test_incoming_traceparent_continues_the_callers_trace(demo, stub, telemetry):
    import json
    import urllib.request

    caller_trace, caller_span = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"
    req = urllib.request.Request(  # noqa: S310 - loopback
        demo.url + "/v1/summarize",
        data=json.dumps({"text": "SYNTHETIC"}).encode(),
        headers={
            "Content-Type": "application/json",
            "traceparent": f"00-{caller_trace}-{caller_span}-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        body = json.loads(resp.read())
    assert body["trace_id"] == caller_trace
    spans = exported(stub, telemetry, caller_trace)
    server = next(s for s in spans if s["kind"] == "SERVER")
    assert server["parent_span_id"] == caller_span  # continues the remote parent
    (kid,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert kid["parent_span_id"] == server["span_id"]


def test_no_content_credentials_or_exception_text_is_exported(demo, stub, telemetry):
    for route in web.ROUTES:
        web.call(demo.url, route, SECRET_TEXT)
    assert telemetry.force_flush(5000)
    raw = b"".join(r.body for r in stub.received)
    for forbidden in (
        SECRET_TEXT,
        web.SYSTEM_PROMPT,
        web.OFFLINE_KEY,
        "SYNTHETIC fake completion",
        "SYNTHETIC mock completion",
        "synthetic 500 from the mock provider",
    ):
        assert forbidden.encode() not in raw
    spans = stub.spans()
    assert all(s["events"] == 0 and s["status_message"] == "" for s in spans)
    for root in (s for s in spans if s["kind"] == "SERVER"):
        assert set(root["attributes"]) <= ROOT_KEYS


def test_invalid_input_is_a_traced_422_without_the_input(demo, stub, telemetry):
    with pytest.raises(urllib.error.HTTPError) as exc:
        web.call(demo.url, "/v1/summarize", "")  # min_length=1
    assert exc.value.code == 422
    assert telemetry.force_flush(5000)
    (root,) = [s for s in stub.spans() if s["kind"] == "SERVER"]
    assert root["attributes"]["http.response.status_code"] == 422
    assert root["status"] == "UNSET"  # a client error is not a server error
    assert not [s for s in stub.spans() if s["kind"] == "CLIENT"]


def test_health_is_not_traced(demo, stub, telemetry):
    import urllib.request

    with urllib.request.urlopen(demo.url + "/health", timeout=5) as resp:  # noqa: S310
        assert resp.status == 200
    telemetry.force_flush(5000)
    assert stub.spans() == []


def test_telemetry_outage_never_breaks_the_application(make_config):
    """Backend unreachable: every route still answers, without waiting on export (ADR-09)."""
    telemetry = lli.init(
        make_config(f"http://127.0.0.1:{free_port()}/v1/traces", export_timeout_seconds=0.5)
    )
    attempts: list[str] = []
    with block_network(attempts):
        running = web.serve_in_thread(web.create_app(telemetry, mock_time_scale=0.0))
        try:
            for route in web.ROUTES:
                started = time.monotonic()
                out = web.call(running.url, route)
                assert out["http_status"] == 200
                assert time.monotonic() - started < 2.0  # the request never waits on export
        finally:
            running.stop()
    assert attempts == []
    assert telemetry.force_flush(3000) is False  # the spans could not be delivered
    assert telemetry.stats().spans_export_failed > 0
    telemetry.shutdown(2000)
