"""Validate and normalize an `ExportTraceServiceRequest` (TELEMETRY_CONTRACT.md §1.3, §2, §5).

Pure functions, no I/O. Validation collects every violation of the whole batch before
anything is written (ADR-18: one invalid span rejects the batch).
"""

from dataclasses import dataclass
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span as SpanPb

from app.services.ingestion import attrmap
from app.services.ingestion.errors import Violation

TS_LIMIT = 2**63  # stored as BIGINT
KINDS = {0: "UNSPECIFIED", 1: "INTERNAL", 2: "SERVER", 3: "CLIENT", 4: "PRODUCER", 5: "CONSUMER"}
STATUS_CODES = {0: "UNSET", 1: "OK", 2: "ERROR"}
# spans.name is LEN(256) (non-empty); OTLP calls an empty name "unknown", so we store that marker.
EMPTY_NAME = "unknown_span"
MAX_SCOPE_VERSION = 64


@dataclass(frozen=True)
class NormalizedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_ns: int
    end_ns: int
    status: str
    status_message: str | None
    error_type: str | None
    scope_name: str | None
    scope_version: str | None
    resource_attributes: dict[str, Any]
    attributes: dict[str, Any]
    provenance: str
    server_dropped_attributes_count: int

    @property
    def is_inference(self) -> bool:
        return attrmap.is_inference(self.attributes)


def count_spans(req: ExportTraceServiceRequest) -> int:
    return sum(len(ss.spans) for rs in req.resource_spans for ss in rs.scope_spans)


def _id_ok(raw: bytes, size: int) -> bool:
    return len(raw) == size and any(raw)


def validate_span(span: SpanPb, path: str) -> list[Violation]:
    v: list[Violation] = []

    def bad(field: str, reason: str, rule: str) -> None:
        v.append(Violation(f"{path}.{field}", reason, rule))

    if not _id_ok(span.trace_id, 16):
        bad("trace_id", "INVALID_TRACE_ID", "must be 16 bytes and not all zero")
    if not _id_ok(span.span_id, 8):
        bad("span_id", "INVALID_SPAN_ID", "must be 8 bytes and not all zero")
    if span.parent_span_id and (
        not _id_ok(span.parent_span_id, 8) or span.parent_span_id == span.span_id
    ):
        bad(
            "parent_span_id",
            "INVALID_PARENT_SPAN_ID",
            "must be empty, or 8 bytes, not all zero and different from span_id",
        )
    start, end = span.start_time_unix_nano, span.end_time_unix_nano
    start_ok = end_ok = True
    if start >= TS_LIMIT:
        start_ok = False
        bad("start_time_unix_nano", "TIMESTAMP_OUT_OF_RANGE", "must be < 2^63")
    elif start == 0:
        start_ok = False
        bad("start_time_unix_nano", "INVALID_TIMESTAMP", "must be > 0")
    if end >= TS_LIMIT:
        end_ok = False
        bad("end_time_unix_nano", "TIMESTAMP_OUT_OF_RANGE", "must be < 2^63")
    if start_ok and end_ok and end < start:
        bad("end_time_unix_nano", "END_BEFORE_START", "must be >= start_time_unix_nano")
    if span.kind not in KINDS:
        bad("kind", "INVALID_SPAN_KIND", "must be a known SpanKind value")
    if span.status.code not in STATUS_CODES:
        bad("status.code", "INVALID_STATUS_CODE", "must be a known StatusCode value")
    return v


def _exception_type(span: SpanPb) -> str | None:
    """§2: an `exception` event contributes only `exception.type`."""
    for ev in span.events:
        if ev.name != "exception":
            continue
        for kv in ev.attributes:
            if kv.key == "exception.type" and kv.value.WhichOneof("value") == "string_value":
                return attrmap.clean_str(kv.value.string_value) or None
    return None


def _span_provenance(attributes: dict[str, Any], status: str) -> str:
    """§5: only inference spans carry provenance. A successful inference span claiming `real`
    without a provider response ID is downgraded to `unknown`."""
    if not attrmap.is_inference(attributes):
        return "unknown"
    claimed = attributes.get("llminsight.provenance", "unknown")
    if claimed == "real" and status != "ERROR" and "gen_ai.response.id" not in attributes:
        return "unknown"
    return claimed


def _normalize_span(
    span: SpanPb, scope_name: str | None, scope_version: str | None, resource: attrmap.Mapped
) -> NormalizedSpan:
    mapped = attrmap.map_span(span.attributes)
    status = STATUS_CODES[span.status.code]
    message = attrmap.scrub_free_text(span.status.message) if span.status.message else None
    error_type = mapped.values.get("error.type") or _exception_type(span)
    return NormalizedSpan(
        trace_id=span.trace_id.hex(),
        span_id=span.span_id.hex(),
        parent_span_id=span.parent_span_id.hex() if span.parent_span_id else None,
        name=attrmap.clean_str(span.name) or EMPTY_NAME,
        kind=KINDS[span.kind],
        start_ns=span.start_time_unix_nano,
        end_ns=span.end_time_unix_nano,
        status=status,
        status_message=message or None,
        error_type=error_type,
        scope_name=scope_name,
        scope_version=scope_version,
        resource_attributes=resource.values,
        attributes=mapped.values,
        provenance=_span_provenance(mapped.values, status),
        # Resource attributes are stored per span, so resource drops count on every span.
        server_dropped_attributes_count=mapped.dropped + resource.dropped,
    )


def normalize(req: ExportTraceServiceRequest) -> tuple[list[NormalizedSpan], list[Violation]]:
    """All spans in request order, plus every violation. Spans are only usable when the
    violation list is empty."""
    spans: list[NormalizedSpan] = []
    violations: list[Violation] = []
    for i, rs in enumerate(req.resource_spans):
        resource = attrmap.map_resource(rs.resource.attributes)
        for j, ss in enumerate(rs.scope_spans):
            scope_name = attrmap.clean_str(ss.scope.name) or None
            scope_version = attrmap.clean_str(ss.scope.version, MAX_SCOPE_VERSION) or None
            for k, span in enumerate(ss.spans):
                found = validate_span(span, f"resource_spans[{i}].scope_spans[{j}].spans[{k}]")
                if found:
                    violations += found
                elif not violations:  # once invalid, skip the mapping work
                    spans.append(_normalize_span(span, scope_name, scope_version, resource))
    return (spans if not violations else []), violations
