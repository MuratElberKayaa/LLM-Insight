"""google.rpc.Status / BadRequest encoding (TELEMETRY_CONTRACT.md §1.2–1.3)."""

import json

from google.rpc import error_details_pb2, status_pb2

from app.services.ingestion import errors
from app.services.ingestion.errors import Violation


def test_binary_status_with_bad_request_details():
    vs = [
        Violation(
            f"resource_spans[0].scope_spans[0].spans[{i}].trace_id", "INVALID_TRACE_ID", "rule"
        )
        for i in range(25)
    ]
    raw = errors.status_body(errors.invalid_spans(vs), errors.PROTOBUF)
    status = status_pb2.Status.FromString(raw)
    assert status.code == 0  # omitted
    assert status.message == "25 invalid span field(s); the batch was rejected (showing 20)"
    assert len(status.details) == 1
    assert status.details[0].type_url == "type.googleapis.com/google.rpc.BadRequest"
    bad = error_details_pb2.BadRequest()
    assert status.details[0].Unpack(bad)
    assert len(bad.field_violations) == errors.MAX_VIOLATIONS
    assert bad.field_violations[0].reason == "INVALID_TRACE_ID"
    assert bad.field_violations[0].field == "resource_spans[0].scope_spans[0].spans[0].trace_id"


def test_binary_status_without_details():
    status = status_pb2.Status.FromString(
        errors.status_body(errors.unauthorized(), errors.PROTOBUF)
    )
    assert status.message == "missing or invalid project key" and not status.details


def test_json_status_is_otlp_json_with_code_omitted():
    raw = errors.status_body(errors.unsupported_media_type(), errors.JSON)
    assert json.loads(raw) == {
        "message": "unsupported media type; only application/x-protobuf is accepted"
    }


def test_retry_and_allow_metadata():
    assert errors.too_many_requests(3).retry_after == 3
    assert errors.storage_unavailable().retry_after == 5
    assert errors.method_not_allowed().headers == {"Allow": "POST"}
