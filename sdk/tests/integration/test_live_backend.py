"""SDK -> official OTLP/HTTP protobuf exporter -> live `python -m app` -> PostgreSQL.

No mocked ingestion: spans travel over loopback HTTP to the real backend process, and the
results are read back from the database and from the read API. All telemetry is SYNTHETIC
(deterministic mock provider); no real provider is called and no cost is computed.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import llm_insight_sdk as lli
from llm_insight_sdk.mock import (
    MOCK_PROVIDER,
    MockLLMProvider,
    MockServerError,
    ObservedMockClient,
    Scenario,
)
from llm_insight_sdk.telemetry import build_span_exporter
from tests.conftest import PROMPT_SENTINEL, SYSTEM_SENTINEL, messages
from tests.integration.conftest import REPO, LiveBackend

pytestmark = pytest.mark.integration

MODEL = "mock-chat-small"
RESPONSE_MODEL = "mock-chat-small-2026-10-01"


def config(live: LiveBackend, **overrides: object) -> lli.LLMInsightConfig:
    values: dict[str, object] = {
        "endpoint": live.ingest_endpoint,
        "project_key": live.key,
        "service_name": "sdk-live-it",
        "service_version": "0.1.0",
        "deployment_environment": "test",
        "schedule_delay_millis": 100,
        "export_timeout_seconds": 3.0,
    }
    values.update(overrides)
    return lli.LLMInsightConfig(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_sdk() -> Iterator[None]:
    lli.shutdown(timeout_millis=3000)
    yield
    lli.shutdown(timeout_millis=3000)


@pytest.fixture
def telemetry(live_backend) -> Iterator[lli.Telemetry]:
    t = lli.init(config(live_backend))
    yield t
    t.shutdown()


@pytest.fixture
def client(telemetry) -> ObservedMockClient:
    return ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0), max_attempts=3)


def hex_trace(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


def hex_span(span) -> str:
    return format(span.get_span_context().span_id, "016x")


def counts(live: LiveBackend, trace_id: str) -> tuple[int, int, int]:
    (row,) = live.query(
        "SELECT (SELECT count(*) FROM traces WHERE project_id = %s AND trace_id = %s),"
        " (SELECT count(*) FROM spans WHERE project_id = %s AND trace_id = %s),"
        " (SELECT count(*) FROM usage_records WHERE project_id = %s AND trace_id = %s)",
        (live.project_id, trace_id) * 3,
    )
    return int(row[0]), int(row[1]), int(row[2])


def spans_of(live: LiveBackend, trace_id: str) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(s) FROM spans s WHERE project_id = %s AND trace_id = %s "
        "ORDER BY start_ns, span_id",
        (live.project_id, trace_id),
    )
    return [r[0] for r in rows]


def usage_of(live: LiveBackend, trace_id: str) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s "
        "ORDER BY attempt_id, started_at, span_id",  # timestamps can tie at zero latency
        (live.project_id, trace_id),
    )
    return [r[0] for r in rows]


def trace_row(live: LiveBackend, trace_id: str) -> dict:
    (row,) = live.query(
        "SELECT row_to_json(t) FROM traces t WHERE project_id = %s AND trace_id = %s",
        (live.project_id, trace_id),
    )
    return row[0]


# ---------------------------------------------------------------- A: successful inference
def test_a_success_persists_trace_spans_and_usage(live_backend, telemetry, client):
    with telemetry.tracer.start_as_current_span("demo.success") as root:
        client.chat(model=MODEL, messages=messages(), prompt_name="summarize", prompt_version="v1")
    assert telemetry.force_flush(10_000) is True
    tid = hex_trace(root)

    assert counts(live_backend, tid) == (1, 2, 1)
    t = trace_row(live_backend, tid)
    assert (t["status"], t["provenance"], t["span_count"]) == ("ok", "synthetic", 2)
    assert (t["service_name"], t["prompt_version"]) == ("sdk-live-it", "v1")

    rows = spans_of(live_backend, tid)
    (root_row,) = [r for r in rows if r["parent_span_id"] is None]
    (child,) = [r for r in rows if r["parent_span_id"] is not None]
    assert root_row["span_id"] == hex_span(root)
    assert child["parent_span_id"] == hex_span(root)
    assert (child["name"], child["kind"], child["status"]) == (f"chat {MODEL}", "CLIENT", "UNSET")
    assert child["provenance"] == "synthetic"
    assert child["status_message"] is None
    assert child["attributes_schema_version"] == "llminsight.attrmap.v1"
    for row in (root_row, child):
        assert row["server_dropped_attributes_count"] == 0  # SDK output passes every R08 rule
    assert child["resource_attributes"] == {
        "service.name": "sdk-live-it",
        "service.version": "0.1.0",
        "deployment.environment.name": "test",
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": "1.45.1",
        "llminsight.sdk.version": lli.__version__,
    }
    a = child["attributes"]
    assert a["gen_ai.provider.name"] == MOCK_PROVIDER
    assert (a["gen_ai.request.model"], a["gen_ai.response.model"]) == (MODEL, RESPONSE_MODEL)
    assert a["llminsight.provenance"] == "synthetic"

    (u,) = usage_of(live_backend, tid)
    assert u["call_key_source"] == "provider_response_id"
    assert u["canonical_call_key"] == f"resp:{MOCK_PROVIDER}:{a['gen_ai.response.id']}"
    assert u["call_id"] == a["llminsight.call_id"] and u["attempt_id"] == 1
    assert (u["provider"], u["operation_name"]) == (MOCK_PROVIDER, "chat")
    assert (u["requested_model"], u["response_model"]) == (MODEL, RESPONSE_MODEL)
    assert (u["input_tokens"], u["output_tokens"]) == (120, 48)
    assert (u["cached_input_tokens"], u["cache_write_input_tokens"]) == (32, 0)
    assert u["reasoning_output_tokens"] == 0
    assert (u["usage_source"], u["provenance"]) == ("provider_reported", "synthetic")
    # no price catalogue and no cost calculation in Phase 2A: unknown stays NULL, never 0
    assert u["cost_usd"] is None
    assert (u["cost_status"], u["cost_unknown_reason"]) == ("unknown", "price_missing")
    assert u["price_version"] is None and u["model_price_id"] is None


def test_a_success_is_visible_through_the_read_api(live_backend, telemetry, client):
    with telemetry.tracer.start_as_current_span("demo.success") as root:
        client.chat(model=MODEL, messages=messages())
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)

    listing = live_backend.api(f"/api/v1/traces?project={live_backend.project_id}&limit=200")
    item = next(i for i in listing["items"] if i["trace_id"] == tid)
    assert (item["provenance"], item["provenance_verified"]) == ("synthetic", False)
    assert item["usage"]["inference_calls"] == 1
    assert (item["usage"]["input_tokens"], item["usage"]["output_tokens"]) == (120, 48)
    assert item["usage"]["known_cost_usd"] is None  # unknown cost is null, not "0"
    assert item["usage"]["unknown_cost_calls"] == 1

    detail = live_backend.api(f"/api/v1/traces/{tid}?project={live_backend.project_id}")
    roles = {s["span_id"]: s["role"] for s in detail["spans"]}
    assert roles[hex_span(root)] == "root"
    assert sorted(roles.values()) == ["child", "root"]
    (usage,) = detail["usage"]
    assert usage["cost_usd"] is None and usage["cost_status"] == "unknown"


# ---------------------------------------------------------------- B: several children
def test_b_multiple_child_inference_spans_under_one_trace(live_backend, telemetry, client):
    async def fan_out():
        await asyncio.gather(
            client.achat(model=MODEL, messages=messages()),
            client.achat(model=MODEL, messages=messages(), scenario=Scenario.DELAYED),
        )

    with telemetry.tracer.start_as_current_span("demo.multi-call") as root:
        client.chat(model=MODEL, messages=messages())
        client.chat(model=MODEL, messages=messages())
        asyncio.run(fan_out())
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)

    assert counts(live_backend, tid) == (1, 5, 4)
    children = [s for s in spans_of(live_backend, tid) if s["kind"] == "CLIENT"]
    assert {c["parent_span_id"] for c in children} == {hex_span(root)}
    usage = usage_of(live_backend, tid)
    assert len({u["call_id"] for u in usage}) == 4
    assert sorted(u["input_tokens"] for u in usage) == [120, 120, 120, 480]
    assert trace_row(live_backend, tid)["span_count"] == 5


# ---------------------------------------------------------------- C: failure
def test_c_failed_inference(live_backend, telemetry, client):
    with (
        telemetry.tracer.start_as_current_span("demo.failure") as root,
        pytest.raises(MockServerError),
    ):
        client.chat(
            model=MODEL, messages=messages(), scenario=Scenario.SERVER_ERROR, max_attempts=1
        )
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)

    assert counts(live_backend, tid) == (1, 2, 1)
    assert trace_row(live_backend, tid)["status"] == "error"
    child = next(s for s in spans_of(live_backend, tid) if s["kind"] == "CLIENT")
    assert (child["status"], child["error_type"], child["status_message"]) == ("ERROR", "500", None)
    (u,) = usage_of(live_backend, tid)
    assert u["input_tokens"] is None and u["output_tokens"] is None
    assert u["usage_source"] == "unknown"
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "usage_missing",
    )
    assert u["call_key_source"] == "sdk_attempt"
    assert u["canonical_call_key"] == f"sdk:{u['call_id']}:1"


# ---------------------------------------------------------------- D: retry attempts
def test_d_retry_is_a_separate_physical_attempt(live_backend, telemetry, client):
    with telemetry.tracer.start_as_current_span("demo.retry") as root:
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS)
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)

    assert counts(live_backend, tid) == (1, 3, 2)
    first, second = usage_of(live_backend, tid)
    assert first["call_id"] == second["call_id"]
    uuid.UUID(first["call_id"], version=4)
    assert (first["attempt_id"], second["attempt_id"]) == (1, 2)
    assert first["span_id"] != second["span_id"]
    assert first["canonical_call_key"] == f"sdk:{first['call_id']}:1"
    assert second["call_key_source"] == "provider_response_id"
    assert first["input_tokens"] is None and second["input_tokens"] == 120
    # M02 (physical attempts) vs M03 (logical calls)
    (row,) = live_backend.query(
        "SELECT count(DISTINCT canonical_call_key), count(DISTINCT call_id) FROM usage_records "
        "WHERE project_id = %s AND trace_id = %s",
        (live_backend.project_id, tid),
    )
    assert row == (2, 1)


# ---------------------------------------------------------------- idempotent re-export
def test_repeated_export_does_not_duplicate_spans_or_usage(live_backend, telemetry, client):
    memory = InMemorySpanExporter()
    telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(memory))
    with telemetry.tracer.start_as_current_span("demo.reexport") as root:
        client.chat(model=MODEL, messages=messages(), scenario=Scenario.RETRY_THEN_SUCCESS)
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)
    before = (counts(live_backend, tid), usage_of(live_backend, tid))
    assert before[0] == (1, 3, 2)

    finished = memory.get_finished_spans()
    assert len(finished) == 3
    exporter = build_span_exporter(config(live_backend))
    try:
        for _ in range(2):  # an exporter or collector resending the same spans
            assert exporter.export(finished) == SpanExportResult.SUCCESS
    finally:
        exporter.shutdown()
    assert (counts(live_backend, tid), usage_of(live_backend, tid)) == before


# ---------------------------------------------------------------- invalid keys (live)
def _call_with_key(live: LiveBackend, key: str) -> tuple[str, bool, lli.TelemetryStats]:
    t = lli.init(config(live, project_key=key))
    client = ObservedMockClient(t, MockLLMProvider(time_scale=0.0))
    with t.tracer.start_as_current_span("demo.bad-key") as root:
        result = client.chat(model=MODEL, messages=messages())
    assert result.text  # the application is unaffected
    delivered = t.force_flush(10_000)
    t.shutdown()
    return hex_trace(root), delivered, t.stats()


def test_unknown_and_revoked_keys_are_rejected_without_breaking_the_app(live_backend):
    unknown = "lli_Zz9Zz9Zz_" + "u" * 43
    tid, delivered, stats = _call_with_key(live_backend, unknown)
    assert delivered is False and stats.spans_exported == 0
    assert counts(live_backend, tid) == (0, 0, 0)

    revoked, prefix = live_backend.create_key("sdk-it")
    live_backend.revoke_key(prefix)
    tid, delivered, stats = _call_with_key(live_backend, revoked)
    assert delivered is False and stats.export_batches_failed >= 1
    assert counts(live_backend, tid) == (0, 0, 0)

    log = live_backend.log_text()
    for secret in (unknown, revoked, live_backend.key):
        assert secret not in log
    assert "401" in log  # the access log shows the rejection, not the key


# ---------------------------------------------------------------- R08 compatibility
def test_sdk_metadata_values_survive_backend_validation_unchanged(live_backend, telemetry):
    client = ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0))
    edge_models = ["mock-chat-small", "ft:mock-chat:org:x:id1", "org/mock-model@v1+build.7"]
    with telemetry.tracer.start_as_current_span("demo.r08") as root:
        for model in edge_models:
            client.chat(
                model=model,
                messages=messages(),
                prompt_name="summarize.v2:en",
                prompt_version="2026-10-01:rc+1/a",
            )
        # values the backend would drop are withheld by the SDK before sending
        client.chat(model="mock chat", messages=messages(), prompt_version="contact a@b.example")
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)
    rows = spans_of(live_backend, tid)
    assert len(rows) == 5
    assert all(r["server_dropped_attributes_count"] == 0 for r in rows)
    assert all(r["name"] not in ("redacted_span", "unknown_span") for r in rows)
    stored = {r["attributes"].get("gen_ai.request.model") for r in rows if r["kind"] == "CLIENT"}
    assert stored == set(edge_models) | {None}
    versions = [r["attributes"].get("gen_ai.prompt.version") for r in rows if r["kind"] == "CLIENT"]
    assert sorted(v for v in versions if v) == ["2026-10-01:rc+1/a"] * 3
    assert telemetry.stats().attributes_withheld == 3  # request model, response model, version


# ---------------------------------------------------------------- E2E demo application
def test_demo_application_end_to_end(live_backend):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLMI_", "OTEL_"))}
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": live_backend.ingest_endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={live_backend.key}",
            "OTEL_SERVICE_NAME": "demo-summarizer",
            "LLMI_PROVIDER_MODE": "mock",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "200",
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "demo_app"],  # the SDK venv running these tests
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert live_backend.key not in result.stdout + result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["provenance"] == "synthetic"
    assert summary["export"]["delivered"] is True
    s = summary["scenarios"]

    expected = {  # (traces, spans, usage rows)
        "A_success": (1, 2, 1),
        "B_multi": (1, 5, 4),
        "C_failure": (1, 2, 1),
        "D_retry": (1, 3, 2),
    }
    for name, shape in expected.items():
        assert counts(live_backend, s[name]["trace_id"]) == shape, name
    assert s["A_success"]["app"] == "answered"
    assert s["C_failure"]["app"] == "fallback"  # handled provider error
    assert s["D_retry"]["app"] == "answered"
    assert trace_row(live_backend, s["C_failure"]["trace_id"])["status"] == "error"
    for name in expected:
        assert trace_row(live_backend, s[name]["trace_id"])["provenance"] == "synthetic"

    for variant in ("unreachable_backend", "rejected_key"):
        e = summary["E_export_failure"][variant]
        assert e["app"] == "answered"  # the application kept working
        assert e["delivered"] is False
        assert e["stats"]["spans_exported"] == 0
        assert e["stats"]["spans_export_failed"] == 2
        assert counts(live_backend, e["trace_id"]) == (0, 0, 0)


# ---------------------------------------------------------------- no leaks anywhere
def test_no_content_or_secret_in_database_logs_or_api(live_backend, telemetry, client):
    with telemetry.tracer.start_as_current_span("demo.leak-check") as root:
        client.chat(model=MODEL, messages=messages())
        with pytest.raises(MockServerError):
            client.chat(
                model=MODEL, messages=messages(), scenario=Scenario.SERVER_ERROR, max_attempts=1
            )
    assert telemetry.force_flush(10_000)
    tid = hex_trace(root)
    assert counts(live_backend, tid) == (1, 3, 2)

    dump = "\n".join(
        json.dumps(r[0])
        for table in ("projects", "project_api_keys", "traces", "spans", "usage_records")
        for r in live_backend.query(f"SELECT row_to_json(x) FROM {table} x")  # noqa: S608
    )
    api = json.dumps(live_backend.api(f"/api/v1/traces?project={live_backend.project_id}"))
    api += json.dumps(live_backend.api(f"/api/v1/traces/{tid}?project={live_backend.project_id}"))
    log = live_backend.log_text()
    for text in (dump, api, log):
        for forbidden in (
            live_backend.key,
            live_backend.key.split("_", 2)[2],  # the secret part alone
            PROMPT_SENTINEL,
            SYSTEM_SENTINEL,
            "SYNTHETIC mock completion",
            "synthetic 500 from the mock provider",  # exception message
            "Traceback",
        ):
            assert forbidden not in text
    # every SDK-produced inference row in the database is labelled synthetic, never real
    (row,) = live_backend.query(
        "SELECT count(*) FILTER (WHERE provenance <> 'synthetic'), count(*) FROM usage_records "
        "WHERE project_id = %s",
        (live_backend.project_id,),
    )
    assert row[0] == 0 and row[1] > 0
    (real,) = live_backend.query("SELECT count(*) FROM traces WHERE provenance = 'real'")
    assert real == (0,)
    (cost,) = live_backend.query(
        "SELECT count(*) FROM usage_records WHERE cost_usd IS NOT NULL OR cost_status <> 'unknown'"
    )
    assert cost == (0,)
