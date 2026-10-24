"""Concurrent duplicate submissions over real HTTP (uvicorn) and real PostgreSQL (T04, T23)."""

import threading

import httpx
import pytest

from tests.integration.ingest_helpers import counts, rows
from tests.otlp import PROTOBUF, body, inference_attrs, load_fixture, request, span

pytestmark = pytest.mark.integration

TRACE_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TRACE_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _fire(url: str, key: str, payloads: list[bytes]) -> list[int]:
    """Send every payload from its own thread, released at the same instant."""
    barrier = threading.Barrier(len(payloads))
    results: list[int | None] = [None] * len(payloads)

    def worker(i: int) -> None:
        with httpx.Client(timeout=30) as c:
            barrier.wait()
            r = c.post(
                f"{url}/v1/traces",
                content=payloads[i],
                headers={"Content-Type": PROTOBUF, "X-Project-Key": key},
            )
            results[i] = r.status_code

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(payloads))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results


def test_concurrent_identical_exports_store_exactly_once(live_server, make_key, db):
    k = make_key()
    statuses = _fire(live_server.url, k.key, [body(load_fixture())] * 12)
    assert statuses == [200] * 12
    assert counts(db) == (1, 3, 1)
    (t,) = rows(db, "SELECT span_count, provenance, service_name FROM traces")
    assert t == {"span_count": 3, "provenance": "synthetic", "service_name": "demo-summarizer"}
    assert live_server.app.state.ingestor.stats.usage_key_collisions == 0


def test_concurrent_overlapping_batches_in_opposite_order_do_not_deadlock(
    live_server, make_key, db
):
    """Two batches touching the same two traces, listed in opposite order."""
    k = make_key()
    spans_a = [span(trace_id=TRACE_A, span_id=f"{i + 1:016x}") for i in range(50)]
    spans_b = [span(trace_id=TRACE_B, span_id=f"{i + 1:016x}") for i in range(50)]
    forward = body(request(*spans_a, *spans_b))
    backward = body(request(*reversed(spans_b), *reversed(spans_a)))
    statuses = _fire(live_server.url, k.key, [forward, backward] * 4)
    assert statuses == [200] * 8
    assert counts(db) == (2, 100, 0)
    assert {r["span_count"] for r in rows(db, "SELECT span_count FROM traces")} == {50}


def test_concurrent_double_instrumentation_keeps_one_usage_row(live_server, make_key, db):
    """Distinct spans, same provider response ID, racing: one usage row survives (T23-S1)."""
    k = make_key()
    payloads = [
        body(request(span(span_id=f"{i + 1:016x}", kind=3, attrs=inference_attrs())))
        for i in range(6)
    ]
    statuses = _fire(live_server.url, k.key, payloads)
    assert statuses == [200] * 6
    assert counts(db) == (1, 6, 1)
    assert live_server.app.state.ingestor.stats.usage_key_collisions == 5
