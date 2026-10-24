"""T01-S3: the official Python OTLP/HTTP protobuf exporter -> live backend -> PostgreSQL.

Telemetry is SYNTHETIC (`llminsight.provenance=synthetic`); no provider API is called.
"""

import pytest
from opentelemetry.exporter.otlp.proto.http import Compression
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from tests.integration.ingest_helpers import counts, rows
from tests.otlp import inference_attrs

pytestmark = pytest.mark.integration

RESOURCE = {
    "service.name": "exporter-smoke",
    "service.version": "0.0.0",
    "deployment.environment.name": "test",
    "host.name": "dropped-by-allowlist",
}


def _exporter(url: str, key: str, compression=Compression.Gzip) -> OTLPSpanExporter:
    return OTLPSpanExporter(
        endpoint=f"{url}/v1/traces",
        headers={"X-Project-Key": key},
        compression=compression,
        timeout=10,
    )


def _emit(provider: TracerProvider) -> None:
    tracer = provider.get_tracer("llminsight.tests.exporter_smoke", "0.0.0")
    with (
        tracer.start_as_current_span(
            "POST /v1/summarize",
            kind=SpanKind.SERVER,
            attributes={"http.request.method": "POST", "http.route": "/v1/summarize"},
        ),
        tracer.start_as_current_span(
            "chat gpt-4o-mini",
            kind=SpanKind.CLIENT,
            attributes={
                **inference_attrs(gen_ai__response__id="chatcmpl-SYNTHETIC-exporter"),
                "gen_ai.input.messages": "SECRET-PROMPT-must-be-dropped",
            },
        ) as llm,
    ):
        llm.set_status(Status(StatusCode.OK))


@pytest.mark.parametrize("compression", [Compression.Gzip, Compression.NoCompression])
def test_t01_s3_standard_exporter_round_trip_and_reexport(live_server, make_key, db, compression):
    k = make_key()
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create(RESOURCE))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    _emit(provider)
    finished = memory.get_finished_spans()
    assert len(finished) == 2

    exporter = _exporter(live_server.url, k.key, compression)
    try:
        assert exporter.export(finished) == SpanExportResult.SUCCESS
        assert counts(db) == (1, 2, 1)
        # the same spans exported again (exporter/collector resend): nothing changes
        assert exporter.export(finished) == SpanExportResult.SUCCESS
        assert counts(db) == (1, 2, 1)
    finally:
        exporter.shutdown()
        provider.shutdown()

    llm_ctx = next(s for s in finished if s.kind is SpanKind.CLIENT)
    root_ctx = next(s for s in finished if s.kind is SpanKind.SERVER)
    stored = {
        r["span_id"]: r
        for r in rows(
            db,
            "SELECT span_id, trace_id, parent_span_id, kind, status, scope_name, provenance,"
            " resource_attributes, attributes, server_dropped_attributes_count FROM spans",
        )
    }
    trace_hex = format(llm_ctx.context.trace_id, "032x")
    llm = stored[format(llm_ctx.context.span_id, "016x")]
    root = stored[format(root_ctx.context.span_id, "016x")]
    assert llm["trace_id"] == root["trace_id"] == trace_hex
    assert llm["parent_span_id"] == root["span_id"] and root["parent_span_id"] is None
    assert (llm["kind"], llm["status"], root["kind"]) == ("CLIENT", "OK", "SERVER")
    assert llm["scope_name"] == "llminsight.tests.exporter_smoke"
    assert llm["provenance"] == "synthetic"
    assert "gen_ai.input.messages" not in llm["attributes"]
    assert llm["resource_attributes"]["service.name"] == "exporter-smoke"
    assert llm["resource_attributes"]["telemetry.sdk.language"] == "python"
    assert "host.name" not in llm["resource_attributes"]
    assert llm["server_dropped_attributes_count"] >= 2  # content attr + host.name
    (u,) = rows(db, "SELECT canonical_call_key, provenance, input_tokens FROM usage_records")
    assert u == {
        "canonical_call_key": "resp:openai:chatcmpl-SYNTHETIC-exporter",
        "provenance": "synthetic",
        "input_tokens": 100,
    }


def test_batch_span_processor_flush_reaches_the_database(live_server, make_key, db):
    k = make_key()
    provider = TracerProvider(resource=Resource.create(RESOURCE))
    provider.add_span_processor(BatchSpanProcessor(_exporter(live_server.url, k.key)))
    _emit(provider)
    assert provider.force_flush(10_000)
    provider.shutdown()
    assert counts(db) == (1, 2, 1)


def test_exporter_with_wrong_key_fails_and_stores_nothing(live_server, make_key, db):
    make_key()
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create(RESOURCE))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    _emit(provider)
    exporter = _exporter(live_server.url, "lli_ZZZZ9999_" + "x" * 43)
    try:
        assert exporter.export(memory.get_finished_spans()) == SpanExportResult.FAILURE
    finally:
        exporter.shutdown()
        provider.shutdown()
    assert counts(db) == (0, 0, 0)


def test_exporter_configured_only_by_the_documented_env_vars(
    live_server, make_key, db, monkeypatch
):
    """The .env.example configuration path: endpoint, header and gzip from OTEL_* variables."""
    k = make_key()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"{live_server.url}/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", f"X-Project-Key={k.key}")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_COMPRESSION", "gzip")
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create(RESOURCE))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    _emit(provider)
    exporter = OTLPSpanExporter()
    try:
        assert exporter.export(memory.get_finished_spans()) == SpanExportResult.SUCCESS
    finally:
        exporter.shutdown()
        provider.shutdown()
    assert counts(db) == (1, 2, 1)
