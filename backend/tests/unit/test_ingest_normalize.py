"""Span validation (§1.3), traversal (§2) and provenance (§5) on real protobuf messages."""

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from opentelemetry.proto.trace.v1.trace_pb2 import Status as SpanStatus

from app.services.ingestion import attrmap
from app.services.ingestion.normalize import count_spans, normalize
from tests.otlp import (
    FIXTURE_LLM,
    FIXTURE_ROOT,
    FIXTURE_TRACE_ID,
    FIXTURE_VALIDATE,
    MS,
    T0,
    inference_attrs,
    kvs,
    load_fixture,
    multi,
    request,
    span,
)

PATH = "resource_spans[0].scope_spans[0].spans[0]"


def one(**kw):
    spans, violations = normalize(request(span(**kw)))
    return spans, violations


def test_fixture_normalizes_to_three_spans_with_expected_fields():
    spans, violations = normalize(load_fixture())
    assert violations == []
    by_id = {s.span_id: s for s in spans}
    assert set(by_id) == {FIXTURE_ROOT, FIXTURE_LLM, FIXTURE_VALIDATE}
    root, llm, val = by_id[FIXTURE_ROOT], by_id[FIXTURE_LLM], by_id[FIXTURE_VALIDATE]
    assert all(s.trace_id == FIXTURE_TRACE_ID for s in spans)
    assert root.parent_span_id is None and root.kind == "SERVER"
    assert llm.parent_span_id == FIXTURE_ROOT and llm.kind == "CLIENT"
    assert val.parent_span_id == FIXTURE_ROOT and val.kind == "INTERNAL"
    assert llm.end_ns - llm.start_ns == 1_200_000_000
    assert llm.scope_name == "llm_insight_sdk" and llm.scope_version == "0.1.0"
    assert root.scope_name == "opentelemetry.instrumentation.fastapi"
    assert all(s.server_dropped_attributes_count == 0 for s in spans)
    assert (llm.provenance, root.provenance, val.provenance) == ("synthetic", "unknown", "unknown")
    assert llm.resource_attributes["service.name"] == "demo-summarizer"
    assert all(s.status == "UNSET" for s in spans)


def test_ids_are_lowercase_hex():
    spans, _ = one(
        trace_id="0AF7651916CD43DD8448EB211C80319C".lower(), span_id="ABCDEF0123456789".lower()
    )
    assert spans[0].trace_id == "0af7651916cd43dd8448eb211c80319c"
    assert spans[0].span_id == "abcdef0123456789"


@pytest.mark.parametrize(
    "raw,field,reason",
    [
        ({"trace_id": b"\x01" * 15}, "trace_id", "INVALID_TRACE_ID"),
        ({"trace_id": b"\x00" * 16}, "trace_id", "INVALID_TRACE_ID"),
        ({"trace_id": b""}, "trace_id", "INVALID_TRACE_ID"),
        ({"span_id": b"\x01" * 9}, "span_id", "INVALID_SPAN_ID"),
        ({"span_id": b"\x00" * 8}, "span_id", "INVALID_SPAN_ID"),
        ({"parent_span_id": b"\x00" * 8}, "parent_span_id", "INVALID_PARENT_SPAN_ID"),
        ({"parent_span_id": b"\x01" * 4}, "parent_span_id", "INVALID_PARENT_SPAN_ID"),
        (
            {"parent_span_id": bytes.fromhex(FIXTURE_ROOT)},
            "parent_span_id",
            "INVALID_PARENT_SPAN_ID",
        ),
    ],
)
def test_invalid_ids_rejected_with_reason_and_no_value_echo(raw, field, reason):
    spans, violations = one(raw_ids=raw)
    assert spans == []
    assert [(v.field, v.reason) for v in violations] == [(f"{PATH}.{field}", reason)]
    for v in violations:
        assert FIXTURE_ROOT not in v.description and FIXTURE_TRACE_ID not in v.description


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (0, 10, [("start_time_unix_nano", "INVALID_TIMESTAMP")]),
        (T0, T0 - 1, [("end_time_unix_nano", "END_BEFORE_START")]),
        (
            2**63,
            2**63 + 5,
            [
                ("start_time_unix_nano", "TIMESTAMP_OUT_OF_RANGE"),
                ("end_time_unix_nano", "TIMESTAMP_OUT_OF_RANGE"),
            ],
        ),
        (T0, 2**63, [("end_time_unix_nano", "TIMESTAMP_OUT_OF_RANGE")]),
        (T0, 2**64 - 1, [("end_time_unix_nano", "TIMESTAMP_OUT_OF_RANGE")]),
    ],
)
def test_bad_timestamps(start, end, expected):
    spans, violations = one(start=start, end=end)
    assert spans == []
    assert [(v.field.removeprefix(PATH + "."), v.reason) for v in violations] == expected


def test_largest_storable_timestamp_and_zero_duration_are_valid():
    spans, violations = one(start=2**63 - 1, end=2**63 - 1)
    assert violations == [] and spans[0].start_ns == 2**63 - 1


def test_unknown_kind_and_status_code():
    s = span()
    s.kind = 9
    s.status.code = 7
    spans, violations = normalize(request(s))
    assert spans == []
    assert {v.reason for v in violations} == {"INVALID_SPAN_KIND", "INVALID_STATUS_CODE"}


def test_violations_from_many_spans_carry_their_own_paths():
    good = span(span_id="1111111111111111")
    bad1 = span(span_id="2222222222222222", raw_ids={"trace_id": b"\x00" * 16})
    bad2 = span(span_id="3333333333333333", start=T0, end=T0 - 5)
    req = multi(({"service.name": "a"}, [(("s1", "1"), [good, bad1]), (("s2", "1"), [bad2])]))
    spans, violations = normalize(req)
    assert spans == []
    assert [v.field for v in violations] == [
        "resource_spans[0].scope_spans[0].spans[1].trace_id",
        "resource_spans[0].scope_spans[1].spans[0].end_time_unix_nano",
    ]


def test_multiple_resources_and_scopes_keep_their_own_resource_and_scope():
    a = span(span_id="1111111111111111")
    b = span(span_id="2222222222222222")
    c = span(span_id="3333333333333333")
    req = multi(
        ({"service.name": "svc-a"}, [(("scope-1", "1.0"), [a]), (("scope-2", "2.0"), [b])]),
        ({}, [(("scope-3", ""), [c])]),
    )
    spans, violations = normalize(req)
    assert violations == [] and count_spans(req) == 3
    got = {s.span_id: (s.resource_attributes, s.scope_name, s.scope_version) for s in spans}
    assert got == {
        "1111111111111111": ({"service.name": "svc-a"}, "scope-1", "1.0"),
        "2222222222222222": ({"service.name": "svc-a"}, "scope-2", "2.0"),
        "3333333333333333": ({}, "scope-3", None),
    }


def test_resource_drops_are_counted_on_every_span_of_that_resource():
    req = request(
        span(span_id="1111111111111111", attrs={"custom.attr": 1}),
        span(span_id="2222222222222222"),
        resource={"service.name": "svc", "host.name": "h", "process.command_line": "x --key=1"},
    )
    spans, _ = normalize(req)
    counts = {s.span_id: s.server_dropped_attributes_count for s in spans}
    assert counts == {"1111111111111111": 3, "2222222222222222": 2}
    assert all(s.resource_attributes == {"service.name": "svc"} for s in spans)


@pytest.mark.parametrize(
    "attrs,status,expected",
    [
        (inference_attrs(), 0, "synthetic"),
        (inference_attrs(llminsight__provenance="real"), 0, "real"),
        # T48-S2: a successful call claiming real without a response ID is downgraded
        (inference_attrs(llminsight__provenance="real", gen_ai__response__id=None), 0, "unknown"),
        (inference_attrs(llminsight__provenance="real", gen_ai__response__id=None), 1, "unknown"),
        # a failed attempt has no response ID; its real claim stands
        (inference_attrs(llminsight__provenance="real", gen_ai__response__id=None), 2, "real"),
        (inference_attrs(llminsight__provenance=None), 0, "unknown"),
        ({"llminsight.provenance": "real", "http.route": "/x"}, 0, "unknown"),  # not inference
    ],
)
def test_span_provenance(attrs, status, expected):
    spans, _ = one(attrs=attrs, status_code=status)
    assert spans[0].provenance == expected


def test_error_type_prefers_error_type_attribute_then_exception_event():
    s = span(status_code=SpanStatus.STATUS_CODE_ERROR)
    ev = s.events.add(name="exception", time_unix_nano=T0 + MS)
    ev.attributes.extend(
        kvs(
            {
                "exception.type": "openai.RateLimitError",
                "exception.message": "SECRET-PROMPT-TEXT",
                "exception.stacktrace": "Traceback ... SECRET",
            }
        )
    )
    spans, _ = normalize(request(s))
    assert spans[0].error_type == "openai.RateLimitError"
    dumped = repr(spans[0])
    assert "SECRET" not in dumped  # events are not stored; only exception.type is used

    s.attributes.extend(kvs({"error.type": "timeout"}))
    spans, _ = normalize(request(s))
    assert spans[0].error_type == "timeout"


def test_status_message_is_not_carried_past_normalization():
    """R21: the status message is dropped entirely (no scrubbing, no truncation)."""
    spans, _ = one(status_code=2, status_message="401 bad key sk-abcdefghijklmnop " + "x" * 400)
    assert not hasattr(spans[0], "status_message")
    assert spans[0].status == "ERROR"
    assert "abcdefghijklmnop" not in repr(spans[0])
    assert attrmap.MAX_STRING == 256


def test_empty_name_and_scope_become_markers_or_null():
    spans, _ = normalize(request(span(name=""), scope=("", "")))
    assert spans[0].name == "unknown_span"
    assert spans[0].scope_name is None and spans[0].scope_version is None


def test_empty_request_has_no_spans_and_no_violations():
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    assert normalize(ExportTraceServiceRequest()) == ([], [])


def test_span_kind_names():
    spans, _ = one(kind=Span.SPAN_KIND_CONSUMER)
    assert spans[0].kind == "CONSUMER"
