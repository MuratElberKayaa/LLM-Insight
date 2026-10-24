"""Builders for real OTLP protobuf messages. All telemetry built here is SYNTHETIC."""

import base64
import copy
import gzip
import json
from typing import Any

from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    ArrayValue,
    InstrumentationScope,
    KeyValue,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from tests.helpers import REPO_ROOT

FIXTURE = REPO_ROOT / "docs" / "fixtures" / "otlp_trace_3span.json"
FIXTURE_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
FIXTURE_ROOT, FIXTURE_LLM, FIXTURE_VALIDATE = (
    "b7ad6b7169203331",
    "00f067aa0ba902b7",
    "53995c3f42cd8ad8",
)

T0 = 1_792_836_000_000_000_000  # 2026-10-24T10:00:00Z in ns
MS = 1_000_000

PROTOBUF = "application/x-protobuf"


def any_value(v: Any) -> AnyValue:
    if isinstance(v, AnyValue):
        return v
    if isinstance(v, bool):
        return AnyValue(bool_value=v)
    if isinstance(v, int):
        return AnyValue(int_value=v)
    if isinstance(v, float):
        return AnyValue(double_value=v)
    if isinstance(v, str):
        return AnyValue(string_value=v)
    if isinstance(v, bytes):
        return AnyValue(bytes_value=v)
    if isinstance(v, list):
        return AnyValue(array_value=ArrayValue(values=[any_value(i) for i in v]))
    raise TypeError(type(v))


def kvs(attrs: dict[str, Any] | list[tuple[str, Any]]) -> list[KeyValue]:
    items = attrs.items() if isinstance(attrs, dict) else attrs
    return [KeyValue(key=k, value=any_value(v)) for k, v in items]


def span(
    trace_id: str = FIXTURE_TRACE_ID,
    span_id: str = FIXTURE_ROOT,
    parent: str | None = None,
    name: str = "span",
    kind: int = Span.SPAN_KIND_INTERNAL,
    start: int = T0,
    end: int | None = None,
    attrs: dict[str, Any] | list[tuple[str, Any]] | None = None,
    status_code: int = 0,
    status_message: str = "",
    raw_ids: dict[str, bytes] | None = None,
) -> Span:
    s = Span(
        trace_id=bytes.fromhex(trace_id),
        span_id=bytes.fromhex(span_id),
        parent_span_id=bytes.fromhex(parent) if parent else b"",
        name=name,
        kind=kind,
        start_time_unix_nano=start,
        end_time_unix_nano=start + 10 * MS if end is None else end,
        attributes=kvs(attrs or {}),
        status=Status(code=status_code, message=status_message),
    )
    for field, value in (raw_ids or {}).items():
        setattr(s, field, value)
    return s


def request(
    *spans: Span,
    resource: dict[str, Any] | None = None,
    scope: tuple[str, str] = ("tests.synthetic", "0.0.0"),
) -> ExportTraceServiceRequest:
    res = {"service.name": "synthetic-test"} if resource is None else resource
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=kvs(res)),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(name=scope[0], version=scope[1]), spans=spans
                    )
                ],
            )
        ]
    )


def multi(*resources: tuple[dict[str, Any], list[tuple[tuple[str, str], list[Span]]]]):
    """resources = (resource_attrs, [((scope_name, scope_version), [spans]), ...])"""
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=kvs(res)),
                scope_spans=[
                    ScopeSpans(scope=InstrumentationScope(name=n, version=v), spans=sp)
                    for (n, v), sp in scopes
                ],
            )
            for res, scopes in resources
        ]
    )


def inference_attrs(**overrides: Any) -> dict[str, Any]:
    """A synthetic, complete `chat` inference span; pass key=None to remove a key."""
    base: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-4o-mini",
        "gen_ai.response.model": "gpt-4o-mini-2024-07-18",
        "gen_ai.response.id": "chatcmpl-SYNTHETIC-T1",
        "gen_ai.usage.input_tokens": 100,
        "gen_ai.usage.output_tokens": 20,
        "llminsight.call_id": "5f0c6e1a-3b7d-4c2e-9a41-000000000001",
        "llminsight.attempt": 1,
        "llminsight.provenance": "synthetic",
        "llminsight.usage_source": "provider_reported",
    }
    for k, v in overrides.items():
        key = k.replace("__", ".")
        if v is None:
            base.pop(key, None)
        else:
            base[key] = v
    return base


def _hex_ids_to_base64(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: (
                base64.b64encode(bytes.fromhex(v)).decode()
                if k in ("traceId", "spanId", "parentSpanId") and isinstance(v, str)
                else _hex_ids_to_base64(v)
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_hex_ids_to_base64(i) for i in node]
    return node


def load_fixture() -> ExportTraceServiceRequest:
    """docs/fixtures/otlp_trace_3span.json -> binary-protobuf message. OTLP/JSON IDs are hex,
    while the generic protobuf JSON parser expects base64 for bytes (R13)."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return json_format.ParseDict(
        _hex_ids_to_base64(copy.deepcopy(data)), ExportTraceServiceRequest()
    )


def body(msg: ExportTraceServiceRequest, *, gzipped: bool = False) -> bytes:
    raw = msg.SerializeToString()
    return gzip.compress(raw) if gzipped else raw
