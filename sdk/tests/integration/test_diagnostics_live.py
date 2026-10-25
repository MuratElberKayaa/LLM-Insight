"""Phase 4A end to end: SDK -> OTLP/HTTP protobuf -> live `python -m app` -> PostgreSQL ->
trace read API -> diagnostics engine -> `GET /api/v1/traces/{id}/diagnostics`.

All traffic is SYNTHETIC (deterministic mock provider, `time_scale=0`); no real provider is
called and no credential is used. Every diagnostic is verified against the rows actually
stored for the trace: each evidence field is compared with its `spans` / `usage_records`
column, and the summary counts are recomputed from the database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

import llm_insight_sdk as lli
from llm_insight_sdk.mock import (
    MockLLMProvider,
    MockServerError,
    MockTimeoutError,
    ObservedMockClient,
    Scenario,
)
from tests.conftest import PROMPT_SENTINEL, SYSTEM_SENTINEL, messages
from tests.integration.conftest import LiveBackend

pytestmark = pytest.mark.integration

MODEL = "mock-chat-small"


@pytest.fixture(autouse=True)
def _clean_sdk() -> Iterator[None]:
    lli.shutdown(timeout_millis=3000)
    yield
    lli.shutdown(timeout_millis=3000)


@pytest.fixture
def telemetry(live_backend: LiveBackend) -> Iterator[lli.Telemetry]:
    t = lli.init(
        lli.LLMInsightConfig(
            endpoint=live_backend.ingest_endpoint,
            project_key=live_backend.key,
            service_name="sdk-diagnostics-it",
            schedule_delay_millis=100,
            export_timeout_seconds=3.0,
        )
    )
    yield t
    t.shutdown()


@pytest.fixture
def client(telemetry: lli.Telemetry) -> ObservedMockClient:
    return ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0), max_attempts=3)


def hex_id(value: int, width: int) -> str:
    return format(value, f"0{width}x")


def run_in_trace(telemetry: lli.Telemetry, name: str, fn: Any) -> tuple[str, str]:
    """Run `fn` under a root span, flush, return (trace id, root span id)."""
    with telemetry.tracer.start_as_current_span(name) as root:
        fn()
    assert telemetry.force_flush(10_000) is True
    ctx = root.get_span_context()
    return hex_id(ctx.trace_id, 32), hex_id(ctx.span_id, 16)


def stored(live: LiveBackend, trace_id: str) -> tuple[dict[str, dict], dict[str, dict], dict]:
    spans = {
        r[0]["span_id"]: r[0]
        for r in live.query(
            "SELECT row_to_json(s) FROM spans s WHERE project_id = %s AND trace_id = %s",
            (live.project_id, trace_id),
        )
    }
    usage = {
        r[0]["span_id"]: r[0]
        for r in live.query(
            "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s",
            (live.project_id, trace_id),
        )
    }
    ((trace,),) = live.query(
        "SELECT row_to_json(t) FROM traces t WHERE project_id = %s AND trace_id = %s",
        (live.project_id, trace_id),
    )
    return spans, usage, trace


def diagnostics(live: LiveBackend, trace_id: str) -> dict:
    return live.api(f"/api/v1/traces/{trace_id}/diagnostics?project={live.project_id}")


def by_category(result: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in result["findings"]:
        out.setdefault(f["category"], []).append(f)
    return out


def facts(finding: dict) -> dict:
    return {f["name"]: f["value"] for f in finding["observed_facts"]}


def assert_matches_storage(live: LiveBackend, trace_id: str, result: dict) -> None:
    """Every evidence field equals its stored column; the summary equals SQL recounts."""
    spans, usage, trace = stored(live, trace_id)
    assert result["trace"] == {
        "trace_id": trace_id,
        "status": trace["status"],
        "provenance": trace["provenance"],
        "provenance_verified": False,
        "span_count": trace["span_count"],
    }
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["spans_analyzed"] == len(spans) == trace["span_count"]
    for finding in result["findings"]:
        assert finding["evidence"], finding["id"]  # never a finding without evidence
        for e in finding["evidence"]:
            s = spans[e["span_id"]]  # the evidence span is a stored span of this trace
            assert (e["status"], e["error_type"], e["kind"]) == (
                s["status"],
                s["error_type"],
                s["kind"],
            )
            assert e["parent_span_id"] == s["parent_span_id"]
            assert e["provenance"] == s["provenance"] and e["provenance_verified"] is False
            assert int(e["start_time_unix_nano"]) == s["start_ns"]
            assert int(e["duration_ns"]) == s["duration_ns"]
            a = s["attributes"]
            if e["inference"] is not None:
                i = e["inference"]
                assert i["call_id"] == a.get("llminsight.call_id")
                assert i["attempt"] == a.get("llminsight.attempt")
                assert i["provider"] == a.get("gen_ai.provider.name")
                assert i["has_response_id"] == ("gen_ai.response.id" in a)
            u = usage.get(e["span_id"])
            if u is None:
                assert e["usage"] is None
            else:
                for col in (
                    "usage_source",
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "reasoning_output_tokens",
                    "cost_status",
                    "cost_unknown_reason",
                ):
                    assert e["usage"][col] == u[col], col
    inference = [s for s in spans.values() if "gen_ai.operation.name" in s["attributes"]]
    call_ids = {s["attributes"]["llminsight.call_id"] for s in inference}
    summary = result["summary"]
    assert summary["inference_attempts"] == len(inference) == len(usage)
    assert summary["failed_inference_attempts"] == sum(s["status"] == "ERROR" for s in inference)
    assert summary["logical_calls"] == len(call_ids)
    assert summary["error_spans"] == sum(s["status"] == "ERROR" for s in spans.values())
    assert summary["cost_computed"] == sum(u["cost_status"] == "computed" for u in usage.values())
    for reason, n in summary["cost_unknown"].items():
        assert n == sum(u["cost_unknown_reason"] == reason for u in usage.values()), reason


# ---------------------------------------------------------------- scenarios
def test_retry_then_success_is_a_recovered_call(live_backend, telemetry, client):
    tid, _ = run_in_trace(
        telemetry,
        "diag.retry",
        lambda: client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS),
    )
    d = diagnostics(live_backend, tid)
    assert_matches_storage(live_backend, tid, d)
    spans, usage, trace = stored(live_backend, tid)
    assert trace["status"] == "error"  # by contract, because attempt 1 is ERROR (R27)
    (recovered,) = by_category(d)["inference_call_recovered_after_retry"]
    attempts = sorted(
        (s for s in spans.values() if s["kind"] == "CLIENT"),
        key=lambda s: s["attributes"]["llminsight.attempt"],
    )
    assert [e["span_id"] for e in recovered["evidence"]] == [s["span_id"] for s in attempts]
    assert facts(recovered)["error_types"] == [attempts[0]["error_type"]] == ["429"]
    assert facts(recovered)["other_error_spans"] == 0
    assert recovered["hypothesis"]["error_interpretation"]["code"] == "rate_limit_status_recorded"
    assert recovered["subject"]["call_id"] == attempts[0]["attributes"]["llminsight.call_id"]
    # the successful attempt has provider-reported usage; the mock is never priced
    (unpriced,) = by_category(d)["cost_unknown_price_missing"]
    assert [e["span_id"] for e in unpriced["evidence"]] == [
        sid for sid, u in usage.items() if u["cost_unknown_reason"] == "price_missing"
    ]
    assert set(by_category(d)) == {
        "inference_call_recovered_after_retry",
        "cost_unknown_price_missing",
    }
    assert "synthetic_telemetry" in {lim["code"] for lim in recovered["limitations"]}


def test_exhausted_retries_and_the_failing_root(live_backend, telemetry, client):
    def call() -> None:
        with pytest.raises(MockServerError):
            client.chat(model=MODEL, messages=messages(), scenario=Scenario.SERVER_ERROR)

    tid, root = run_in_trace(telemetry, "diag.failure", call)
    d = diagnostics(live_backend, tid)
    assert_matches_storage(live_backend, tid, d)
    cat = by_category(d)
    (failed,) = cat["inference_call_failed_after_retries"]
    assert facts(failed)["attempts_observed"] == 3
    assert facts(failed)["attempt_numbers"] == [1, 2, 3]
    assert facts(failed)["error_types"] == ["500"]
    assert failed["hypothesis"]["error_interpretation"]["code"] == "server_error_status_recorded"
    assert "inference_call_recovered_after_retry" not in cat
    # The application handled the provider error inside the root span, so the root is not
    # ERROR and no root-level error is invented.
    spans, _, _ = stored(live_backend, tid)
    assert spans[root]["status"] == "UNSET"
    assert "non_inference_span_error" not in cat
    assert d["summary"]["cost_unknown"]["usage_missing"] == 3  # failed attempts: unknown, not 0


def test_unhandled_failure_marks_the_root_and_is_related_not_independent(
    live_backend, telemetry, client
):
    trace_ids: list[tuple[str, str]] = []
    # The error escapes the root span (pytest.raises is outside it): the OTel SDK marks it ERROR.
    with (
        pytest.raises(MockServerError),
        telemetry.tracer.start_as_current_span("diag.unhandled") as root,
    ):
        ctx = root.get_span_context()
        trace_ids.append((hex_id(ctx.trace_id, 32), hex_id(ctx.span_id, 16)))
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.SERVER_ERROR)
    assert telemetry.force_flush(10_000) is True
    ((tid, root_id),) = trace_ids
    d = diagnostics(live_backend, tid)
    assert_matches_storage(live_backend, tid, d)
    spans, _, _ = stored(live_backend, tid)
    assert spans[root_id]["status"] == "ERROR"
    (root_error,) = by_category(d)["non_inference_span_error"]
    assert root_error["subject"] == {"type": "span", "span_id": root_id}
    assert root_error["hypothesis"]["code"] == "related_to_failed_inference_below"
    assert root_error["hypothesis"]["error_interpretation"] is None  # not a provider status
    assert facts(root_error)["failed_inference_attempts_in_subtree"] == 3
    assert facts(root_error)["error_type"] == spans[root_id]["error_type"]


def test_single_timeout_is_one_failed_attempt(live_backend, telemetry, client):
    def call() -> None:
        with pytest.raises(MockTimeoutError):
            client.chat(model=MODEL, messages=messages(), scenario=Scenario.TIMEOUT, max_attempts=1)

    tid, _ = run_in_trace(telemetry, "diag.timeout", call)
    d = diagnostics(live_backend, tid)
    assert_matches_storage(live_backend, tid, d)
    (failed,) = by_category(d)["inference_call_failed"]
    assert facts(failed)["attempts_observed"] == 1
    assert failed["hypothesis"]["error_interpretation"]["code"] == "timeout_recorded"
    assert "inference_call_failed_after_retries" not in by_category(d)


def test_success_has_only_the_unknown_cost_observation(live_backend, telemetry, client):
    tid, _ = run_in_trace(
        telemetry, "diag.success", lambda: client.chat(model=MODEL, messages=messages())
    )
    d = diagnostics(live_backend, tid)
    assert_matches_storage(live_backend, tid, d)
    assert [f["category"] for f in d["findings"]] == ["cost_unknown_price_missing"]
    assert d["method"]["language_model_used"] is False


def test_diagnostics_match_the_detail_api_and_reads_are_idempotent(live_backend, telemetry, client):
    tid, _ = run_in_trace(
        telemetry,
        "diag.detail",
        lambda: client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS),
    )
    first, second = diagnostics(live_backend, tid), diagnostics(live_backend, tid)
    assert json.dumps(first) == json.dumps(second)
    detail = live_backend.api(f"/api/v1/traces/{tid}?project={live_backend.project_id}")
    roles = {s["span_id"]: s["role"] for s in detail["spans"]}
    for f in first["findings"]:
        for e in f["evidence"]:
            assert e["role"] == roles[e["span_id"]]
    assert first["trace"]["status"] == detail["trace"]["status"]
    assert first["trace"]["provenance"] == detail["trace"]["provenance"] == "synthetic"


def test_no_content_or_secret_in_diagnostics(live_backend, telemetry, client):
    def call() -> None:
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS)

    tid, _ = run_in_trace(telemetry, "diag.privacy", call)
    raw = json.dumps(diagnostics(live_backend, tid))
    for value in (PROMPT_SENTINEL, SYSTEM_SENTINEL, live_backend.key, live_backend.admin_token):
        assert value not in raw
    assert "diag.privacy" not in raw  # span names are never echoed
