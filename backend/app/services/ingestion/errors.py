"""OTLP error responses (TELEMETRY_CONTRACT.md §1.2–1.3).

Every error body is a `google.rpc.Status` with `code` omitted and a short, generic English
message. `BadRequest` violations carry a proto field path, rule text and reason code only,
never the offending value.
"""

import json
from dataclasses import dataclass, field

from google.protobuf.any_pb2 import Any as AnyPb
from google.rpc import error_details_pb2, status_pb2

PROTOBUF = "application/x-protobuf"
JSON = "application/json"
MAX_VIOLATIONS = 20  # keeps every error body far below the spec's 4 MiB response limit


@dataclass(frozen=True)
class Violation:
    field: str
    reason: str
    description: str


@dataclass(eq=False)  # exceptions keep identity semantics and stay hashable
class IngestError(Exception):
    status_code: int
    message: str
    violations: list[Violation] = field(default_factory=list)
    retry_after: int | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.status_code, self.message)


def status_body(err: IngestError, media_type: str) -> bytes:
    if media_type == JSON:
        # OTLP/JSON encoding of google.rpc.Status with `code` omitted (§1.2).
        return json.dumps({"message": err.message}).encode("utf-8")
    status = status_pb2.Status(message=err.message)
    if err.violations:
        bad = error_details_pb2.BadRequest(
            field_violations=[
                error_details_pb2.BadRequest.FieldViolation(
                    field=v.field, description=v.description, reason=v.reason
                )
                for v in err.violations[:MAX_VIOLATIONS]
            ]
        )
        detail = AnyPb()
        detail.Pack(bad)  # type URL type.googleapis.com/google.rpc.BadRequest
        status.details.append(detail)
    return status.SerializeToString()


# Fixed, value-free messages (never contain keys, header or attribute values, names or IDs).
def method_not_allowed() -> IngestError:
    return IngestError(405, "method not allowed; use POST", headers={"Allow": "POST"})


def unsupported_media_type() -> IngestError:
    return IngestError(415, "unsupported media type; only application/x-protobuf is accepted")


def unsupported_encoding() -> IngestError:
    return IngestError(415, "unsupported content encoding; use gzip or identity")


def unauthorized() -> IngestError:
    # Missing, malformed, unknown, mismatched and revoked keys all get this same response.
    return IngestError(401, "missing or invalid project key")


def too_many_requests(retry_after: int) -> IngestError:
    return IngestError(429, "too many requests; retry later", retry_after=retry_after)


def body_too_large() -> IngestError:
    return IngestError(413, "request body exceeds the size limit")


def decompressed_too_large() -> IngestError:
    return IngestError(413, "decompressed request body exceeds the size limit")


def too_many_spans() -> IngestError:
    return IngestError(413, "request exceeds the span count limit")


def invalid_gzip() -> IngestError:
    return IngestError(
        400,
        "request body is not valid gzip",
        [Violation("body", "INVALID_GZIP", "must be a valid gzip stream")],
    )


def invalid_protobuf() -> IngestError:
    return IngestError(
        400,
        "request body is not a valid ExportTraceServiceRequest",
        [
            Violation(
                "body", "INVALID_PROTOBUF", "must be a binary protobuf ExportTraceServiceRequest"
            )
        ],
    )


def invalid_spans(violations: list[Violation]) -> IngestError:
    n = len(violations)
    shown = min(n, MAX_VIOLATIONS)
    return IngestError(
        400,
        f"{n} invalid span field(s); the batch was rejected (showing {shown})",
        violations,
    )


def storage_unavailable(retry_after: int = 5) -> IngestError:
    return IngestError(503, "storage unavailable; retry later", retry_after=retry_after)


def internal_error() -> IngestError:
    return IngestError(500, "internal error")
