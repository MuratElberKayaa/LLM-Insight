"""`POST /v1/traces` wire behaviour against a real PostgreSQL (TELEMETRY_CONTRACT.md §1).

T01 (+S1, S2), T06 (+S1–S6), T07, T08 (+S1–S3). All telemetry is SYNTHETIC.
"""

import gzip
import json

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.integration.ingest_helpers import counts, post, status_of, violations_of
from tests.otlp import (
    FIXTURE_ROOT,
    FIXTURE_TRACE_ID,
    PROTOBUF,
    T0,
    body,
    inference_attrs,
    load_fixture,
    request,
    span,
)

pytestmark = pytest.mark.integration


def span_ids(n: int, offset: int = 1) -> list[str]:
    return [f"{i + offset:016x}" for i in range(n)]


# ---------------------------------------------------------------- T01 success


def test_t01_valid_protobuf_returns_export_response_after_commit(client, make_key, db, ingest_url):
    k = make_key()
    r = post(client, body(load_fixture()), k.key)
    assert r.status_code == 200
    assert r.headers["content-type"] == PROTOBUF
    resp = ExportTraceServiceResponse.FromString(r.content)
    assert not resp.HasField("partial_success")  # unset on full success
    assert r.content == b""
    # T01-S1: the rows are visible from a separate connection as soon as the 200 arrives.
    other = create_engine(ingest_url, poolclass=NullPool)
    try:
        with other.connect() as c:
            got = [
                c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()  # noqa: S608
                for t in ("traces", "spans", "usage_records")
            ]
    finally:
        other.dispose()
    assert got == [1, 3, 1]


def test_t01_s2_empty_request_is_200_and_writes_nothing(client, make_key, db):
    k = make_key()
    for msg in (
        ExportTraceServiceRequest(),
        ExportTraceServiceRequest(resource_spans=[ResourceSpans(scope_spans=[ScopeSpans()])]),
    ):
        r = post(client, msg.SerializeToString(), k.key)
        assert r.status_code == 200
        assert not ExportTraceServiceResponse.FromString(r.content).HasField("partial_success")
    assert counts(db) == (0, 0, 0)


def test_content_type_parameters_and_case_are_accepted(client, make_key, db):
    k = make_key()
    r = post(
        client, body(load_fixture()), k.key, Content_Type="Application/X-Protobuf; charset=binary"
    )
    assert r.status_code == 200 and counts(db) == (1, 3, 1)


# ---------------------------------------------------------------- T06 media type / encoding


def test_t06_s3_json_request_gets_json_encoded_status_415(client, make_key, db):
    k = make_key()
    r = post(client, b'{"resourceSpans": []}', k.key, Content_Type="application/json")
    assert r.status_code == 415
    assert r.headers["content-type"] == "application/json"
    assert json.loads(r.content) == {
        "message": "unsupported media type; only application/x-protobuf is accepted"
    }
    assert counts(db) == (0, 0, 0)


@pytest.mark.parametrize("content_type", ["text/plain", "application/protobuf", None])
def test_t06_s4_other_or_missing_media_type_gets_binary_status_415(client, make_key, content_type):
    k = make_key()
    headers = {"X-Project-Key": k.key}
    if content_type:
        headers["Content-Type"] = content_type
    r = client.post("/v1/traces", content=body(load_fixture()), headers=headers)
    assert r.status_code == 415
    assert status_of(r).message.startswith("unsupported media type")


@pytest.mark.parametrize("encoding", ["br", "deflate", "zstd"])
def test_t06_s5_unsupported_content_encoding_is_415(client, make_key, db, encoding):
    k = make_key()
    r = post(client, body(load_fixture()), k.key, Content_Encoding=encoding)
    assert r.status_code == 415
    assert status_of(r).message == "unsupported content encoding; use gzip or identity"
    assert counts(db) == (0, 0, 0)


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
def test_other_methods_are_405_with_binary_status(client, method):
    r = client.request(method, "/v1/traces")
    assert r.status_code == 405
    assert r.headers["allow"] == "POST"
    assert status_of(r).message == "method not allowed; use POST"


# ---------------------------------------------------------------- T06 bad protobuf / bad data


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xff\xff\xff",
        body(load_fixture())[:-7],  # truncated inside a length-delimited field
        b"\x0a\x05\x0a\x03abc",  # length runs past the end
    ],
)
def test_t06_corrupt_protobuf_is_400_with_bad_request_and_nothing_stored(
    client, make_key, db, payload
):
    k = make_key()
    r = post(client, payload, k.key)
    assert r.status_code == 400
    v = violations_of(r)
    assert [(x.field, x.reason) for x in v] == [("body", "INVALID_PROTOBUF")]
    assert counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    "raw,reason",
    [
        ({"trace_id": b"\x00" * 16}, "INVALID_TRACE_ID"),
        ({"trace_id": b"\x0a" * 8}, "INVALID_TRACE_ID"),
        ({"span_id": b"\x00" * 8}, "INVALID_SPAN_ID"),
        ({"span_id": b"\x0b" * 16}, "INVALID_SPAN_ID"),
        ({"parent_span_id": bytes.fromhex(FIXTURE_ROOT)}, "INVALID_PARENT_SPAN_ID"),
    ],
)
def test_t06_s1_invalid_ids_400_without_echoing_values(client, make_key, db, raw, reason):
    k = make_key()
    r = post(client, body(request(span(raw_ids=raw))), k.key)
    assert r.status_code == 400
    v = violations_of(r)
    assert [x.reason for x in v] == [reason]
    assert v[0].field.startswith("resource_spans[0].scope_spans[0].spans[0].")
    for needle in (FIXTURE_TRACE_ID, FIXTURE_ROOT, "0a0a0a0a", "0b0b0b0b", k.key, k.prefix):
        assert needle.encode() not in r.content
    assert counts(db) == (0, 0, 0)


@pytest.mark.parametrize(
    "start,end,reasons",
    [
        (T0, T0 - 1, ["END_BEFORE_START"]),
        (0, T0, ["INVALID_TIMESTAMP"]),
        (2**63, 2**63, ["TIMESTAMP_OUT_OF_RANGE", "TIMESTAMP_OUT_OF_RANGE"]),
    ],
)
def test_t06_s2_bad_timestamps(client, make_key, db, start, end, reasons):
    k = make_key()
    r = post(client, body(request(span(start=start, end=end))), k.key)
    assert r.status_code == 400
    assert [x.reason for x in violations_of(r)] == reasons
    assert counts(db) == (0, 0, 0)


def test_t06_s6_one_invalid_span_among_ten_valid_writes_nothing(client, make_key, db):
    k = make_key()
    good = [span(span_id=s) for s in span_ids(10)]
    bad = span(span_id="00000000000000ff", start=T0, end=T0 - 1)
    msg = request(*good[:5], bad, *good[5:])
    r = post(client, body(msg), k.key)
    assert r.status_code == 400
    v = violations_of(r)
    assert [(x.field, x.reason) for x in v] == [
        ("resource_spans[0].scope_spans[0].spans[5].end_time_unix_nano", "END_BEFORE_START")
    ]
    assert status_of(r).message == "1 invalid span field(s); the batch was rejected (showing 1)"
    assert counts(db) == (0, 0, 0)  # atomic: not even the 10 valid spans


def test_bad_request_details_are_capped_at_20(client, make_key, db):
    k = make_key()
    bad = [span(span_id=s, raw_ids={"trace_id": b"\x00" * 16}) for s in span_ids(30)]
    r = post(client, body(request(*bad)), k.key)
    assert r.status_code == 400
    assert len(violations_of(r)) == 20
    assert status_of(r).message.startswith("30 invalid span field(s)")


# ---------------------------------------------------------------- T07 gzip


def test_t07_gzip_request_is_decoded(client, make_key, db):
    k = make_key()
    r = post(client, body(load_fixture(), gzipped=True), k.key, Content_Encoding="gzip")
    assert r.status_code == 200 and counts(db) == (1, 3, 1)


def test_identity_encoding_is_accepted(client, make_key, db):
    k = make_key()
    r = post(client, body(load_fixture()), k.key, Content_Encoding="identity")
    assert r.status_code == 200 and counts(db) == (1, 3, 1)


@pytest.mark.parametrize(
    "payload",
    [b"definitely not gzip", gzip.compress(body(load_fixture()))[:-10], b""],
)
def test_t07_corrupt_gzip_is_400_invalid_gzip(client, make_key, db, payload):
    k = make_key()
    r = post(client, payload, k.key, Content_Encoding="gzip")
    assert r.status_code == 400
    assert [(x.field, x.reason) for x in violations_of(r)] == [("body", "INVALID_GZIP")]
    assert counts(db) == (0, 0, 0)


def test_gzip_of_non_protobuf_is_invalid_protobuf(client, make_key):
    k = make_key()
    r = post(client, gzip.compress(b"\xff\xff\xff"), k.key, Content_Encoding="gzip")
    assert r.status_code == 400
    assert [x.reason for x in violations_of(r)] == ["INVALID_PROTOBUF"]


# ---------------------------------------------------------------- T08 limits (each separately)


def test_t08_s1_wire_body_over_4_mib_is_413(client, make_key, db):
    k = make_key()
    big = b"\x00" * (4 * 1024 * 1024 + 1)
    r = post(client, big, k.key)
    assert r.status_code == 413
    assert status_of(r).message == "request body exceeds the size limit"
    assert counts(db) == (0, 0, 0)


def test_t08_wire_body_of_exactly_4_mib_passes_the_size_step(client, make_key):
    """Exactly at the limit is allowed; the zero-filled body then fails to decode (400)."""
    k = make_key()
    r = post(client, b"\x00" * (4 * 1024 * 1024), k.key)
    assert r.status_code == 400


def test_t08_s2_gzip_bomb_over_16_mib_decompressed_is_413(client, make_key, db):
    k = make_key()
    bomb = gzip.compress(b"\x00" * (32 * 1024 * 1024), compresslevel=9)
    assert len(bomb) < 4 * 1024 * 1024  # passes the wire limit, fails the inflated one
    r = post(client, bomb, k.key, Content_Encoding="gzip")
    assert r.status_code == 413
    assert status_of(r).message == "decompressed request body exceeds the size limit"
    assert counts(db) == (0, 0, 0)


def test_t08_s3_2049_spans_is_413_and_2048_is_accepted(client, make_key, db):
    k = make_key()
    over = request(*[span(span_id=s) for s in span_ids(2049)])
    r = post(client, body(over, gzipped=True), k.key, Content_Encoding="gzip")
    assert r.status_code == 413
    assert status_of(r).message == "request exceeds the span count limit"
    assert counts(db) == (0, 0, 0)

    at = request(*[span(span_id=s) for s in span_ids(2048)])
    r = post(client, body(at, gzipped=True), k.key, Content_Encoding="gzip")
    assert r.status_code == 200
    assert counts(db) == (1, 2048, 0)


def test_span_count_limit_counts_across_resources_and_scopes(client, make_key, db):
    k = make_key()
    half = [span(span_id=s) for s in span_ids(1025)]
    other = [span(span_id=s) for s in span_ids(1025, offset=5000)]
    msg = request(*half)
    msg.resource_spans.append(request(*other).resource_spans[0])
    r = post(client, body(msg, gzipped=True), k.key, Content_Encoding="gzip")
    assert r.status_code == 413 and counts(db) == (0, 0, 0)


def test_large_inference_batch_persists_one_usage_row_per_span(client, make_key, db):
    k = make_key()
    spans = [
        span(
            span_id=s,
            attrs=inference_attrs(gen_ai__response__id=f"chatcmpl-SYN-{i}"),
            kind=3,
        )
        for i, s in enumerate(span_ids(1500))
    ]
    r = post(client, body(request(*spans), gzipped=True), k.key, Content_Encoding="gzip")
    assert r.status_code == 200
    assert counts(db) == (1, 1500, 1500)
