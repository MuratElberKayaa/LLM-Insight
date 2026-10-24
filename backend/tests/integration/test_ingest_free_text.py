"""R21: free-text status and exception descriptions never reach the database, the logs or
an API response. All telemetry is SYNTHETIC; every "secret" below is fake and assembled at
runtime so no credential-shaped literal sits in the source or in pytest node IDs.

The check is a full dump: every column of every row of every table, as JSON text, plus every
log record at DEBUG level from every logger.
"""

import logging

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Status as SpanStatus

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import counts, post, rows, status_of, violations_of
from tests.otlp import FIXTURE_ROOT, MS, T0, body, inference_attrs, kvs, request, span

pytestmark = pytest.mark.integration

FAKE_OPENAI_KEY = "sk-" + "proj" + "Z9" * 12
FAKE_INGEST_KEY = "lli_" + "Q1w2E3r4" + "_" + "m" * 43
MARKERS = {
    "openai_key": f"Error code: 401 - Incorrect API key provided: {FAKE_OPENAI_KEY}",
    "bearer": "upstream rejected Authorization: Bearer " + "eyJ" + "hbGciOi" * 4 + ".x.y",
    "ingest_key": f"export failed with X-Project-Key={FAKE_INGEST_KEY}",
    "password": "connect failed: postgres://app:" + "Hunter2" + "Hunter2@db/prod",
    "email_pii": "customer ayse.yilmaz" + "@" + "example.com asked about her refund",
    "phone_pii": "callback +90 5" + "32 111 22 33 requested",
    "iban_pii": "TR33 0006 1005 1978 6457 8413 26 bounced",
    "multiline": (
        "Traceback (most recent call last):\r\n  File x\n    prompt='SECRET-PROMPT-LINE'\n"
    ),
    "rtl_zero_width": "safe‮TERCES​‍-hidden-RTL-MARKER",
    "combining_emoji": "é́ \U0001f511 KEY-EMOJI-MARKER ﻿",
    "turkish": "Müşteri şikâyeti: İSTANBUL-ĞÜŞ-MARKER",
    "nul_byte": "before\x00after-NUL-MARKER",
    # Prose, like a real exception text. (An identifier-shaped value such as "LONG-MARKER-AAA"
    # would pass the error.type identifier rule; that residual risk is in reports/phase-1c.md.)
    "very_long": "LONG-MARKER " + "the model said something private " * 3000,
}
# Distinctive substrings that must never appear anywhere afterwards.
NEEDLES = [
    "Z9Z9Z9Z9",
    "hbGciOihbGciOi",
    "mmmmmmmmmm",
    "Hunter2Hunter2",
    "ayse.yilmaz",
    "532 111",
    "1978 6457",
    "SECRET-PROMPT-LINE",
    "TERCES",
    "KEY-EMOJI-MARKER",
    "ĞÜŞ-MARKER",
    "NUL-MARKER",
    "LONG-MARKER",
]


def dump_database(db) -> str:
    tables = ("projects", "project_api_keys", "traces", "spans", "usage_records")
    out = []
    for t in tables:
        for r in rows(db, f"SELECT row_to_json(x)::text AS j FROM {t} AS x"):  # noqa: S608 - constant names
            out.append(r["j"])
    return "\n".join(out)


def adversarial_request():
    spans = []
    for i, (name, message) in enumerate(MARKERS.items()):
        s = span(
            span_id=f"{i + 1:016x}",
            parent=FIXTURE_ROOT if i else None,
            name=f"step-{name}",
            start=T0 + i * MS,
            status_code=SpanStatus.STATUS_CODE_ERROR,
            status_message=message,
            attrs={"error.type": message[:200]} if i % 2 else inference_attrs(),
        )
        ev = s.events.add(name="exception", time_unix_nano=T0 + i * MS)
        ev.attributes.extend(
            kvs(
                {
                    "exception.type": message[:100],
                    "exception.message": message,
                    "exception.stacktrace": message,
                }
            )
        )
        spans.append(s)
    return request(*spans)


def test_r21_adversarial_status_messages_never_reach_db_logs_or_api(
    api_client, make_key, db, caplog
):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    k = make_key()
    r = post(api_client, body(adversarial_request()), k.key)
    assert r.status_code == 200
    assert counts(db)[1] == len(MARKERS)

    stored = rows(db, "SELECT status, status_message, error_type FROM spans")
    assert all(s["status"] == "ERROR" for s in stored)
    assert all(s["status_message"] is None for s in stored)  # column kept, never written
    assert all(s["error_type"] is None for s in stored)  # descriptions are not identifiers

    dump = dump_database(db)
    (trace,) = rows(db, "SELECT trace_id FROM traces")
    detail = api_client.get(
        f"/api/v1/traces/{trace['trace_id']}",
        params={"project": k.project_id},
        headers=ADMIN_AUTH,
    )
    assert detail.status_code == 200
    assert "status_message" not in detail.text
    for needle in NEEDLES:
        assert needle not in dump, needle
        assert needle not in caplog.text, needle
        assert needle not in detail.text, needle
        assert needle not in r.text, needle


def test_r21_reasonable_error_type_identifiers_are_still_stored(client, make_key, db):
    k = make_key()
    s = span(
        status_code=SpanStatus.STATUS_CODE_ERROR,
        status_message="rate limited",
        attrs={"error.type": "openai.RateLimitError"},
    )
    assert post(client, body(request(s)), k.key).status_code == 200
    (row,) = rows(db, "SELECT status, status_message, error_type FROM spans")
    assert row == {
        "status": "ERROR",
        "status_message": None,
        "error_type": "openai.RateLimitError",
    }


def test_r21_invalid_utf8_status_message_is_rejected_without_echo(client, make_key, db, caplog):
    """Protobuf `string` fields must be UTF-8: a payload carrying raw bytes in the status
    message is a 400 INVALID_PROTOBUF, nothing is written and the bytes are not echoed."""
    caplog.set_level(logging.DEBUG)
    k = make_key()
    raw = body(request(span(status_code=2, status_message="QQQQ-UTF8-PROBE")))
    assert raw.count(b"QQQQ") == 1
    tampered = raw.replace(b"QQQQ", b"\xff\xfe\xc3\x28")  # same length: framing stays valid
    r = post(client, tampered, k.key)
    assert r.status_code == 400
    assert [v.reason for v in violations_of(r)] == ["INVALID_PROTOBUF"]
    assert b"UTF8-PROBE" not in r.content and b"\xff\xfe" not in r.content
    assert "UTF8-PROBE" not in caplog.text
    assert counts(db) == (0, 0, 0)
    assert status_of(r).message == "request body is not a valid ExportTraceServiceRequest"
