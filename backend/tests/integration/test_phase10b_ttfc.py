"""Phase 10B (ADR-55): M20 time to first chunk through the real ingest path and the read API.

SYNTHETIC spans only. Three streaming attempts record 0.1 s, 0.2 s and 0.4 s; one streaming
attempt records nothing; one non-streaming attempt records nothing. Percentiles are
PostgreSQL `percentile_cont` (linear): p50 = 200 ms, p95 = 200 + 0.9 · 200 = 380 ms.
"""

from datetime import UTC, datetime

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
START = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
BASE = int((START - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * SEC
WINDOW = {"from": "2026-11-20T00:00:00Z", "to": "2026-11-21T00:00:00Z"}
ATTEMPTS = [  # (stream, ttfc seconds or None)
    (True, 0.1),
    (True, 0.2),
    (True, 0.4),
    (True, None),
    (False, None),
]


def spans() -> list[Span]:
    out = []
    for i, (stream, ttfc) in enumerate(ATTEMPTS, start=1):
        extra = {"gen_ai__request__stream": stream}
        if ttfc is not None:
            extra["gen_ai__response__time_to_first_chunk"] = ttfc
        attrs = inference_attrs(
            gen_ai__response__id=f"chatcmpl-SYNTHETIC-10b-{i}",
            llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{20_000 + i:012d}",
            **extra,
        )
        start = BASE + i * SEC
        out.append(
            span(
                trace_id=f"10b{i:029x}",
                span_id=f"{i:016x}",
                name="chat gpt-4o-mini",
                kind=Span.SPAN_KIND_CLIENT,
                start=start,
                end=start + 900 * MS,
                attrs=attrs,
            )
        )
    return out


@pytest.fixture
def seeded(api_client, make_key):
    k = make_key("p10b")
    assert post(api_client, body(request(*spans())), k.key).status_code == 200
    return k


def get(client, path, project, **params):
    r = client.get(path, params={"project": project, **WINDOW, **params}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_overview_reports_m20_from_recorded_values_only(seeded, api_client):
    m20 = get(api_client, "/api/v1/overview", seeded.project_id)["inference"]["time_to_first_chunk"]
    total = m20["total"]
    assert (total["available"], total["reason"]) == (True, None)
    assert (total["streaming_attempts"], total["n"]) == (4, 3)
    assert total["p50_ms"] == pytest.approx(200.0) and total["p95_ms"] == pytest.approx(380.0)
    assert total["low_sample"] == {"p95": True}
    assert m20["by_provenance"]["synthetic"] == total
    assert m20["by_provenance"]["real"]["reason"] == "no_streaming_attempts"


def test_compare_groups_carry_m20(seeded, api_client):
    (group,) = get(api_client, "/api/v1/models/compare", seeded.project_id)["groups"]
    m20 = group["time_to_first_chunk"]
    assert m20["n"] == 3 and m20["streaming_attempts"] == 4
    assert group["time_to_first_chunk"]["p50_ms"] == pytest.approx(200.0)


def test_streaming_without_values_is_not_recorded(api_client, make_key):
    k = make_key("p10b-none")
    only = [s for s in spans() if s.span_id == bytes.fromhex(f"{4:016x}")]
    assert post(api_client, body(request(*only)), k.key).status_code == 200
    overview = get(api_client, "/api/v1/overview", k.project_id)
    total = overview["inference"]["time_to_first_chunk"]["total"]
    assert (total["available"], total["reason"], total["p50_ms"]) == (False, "not_recorded", None)
