"""Phase 10F (ADR-59): alert rules evaluated through the read API. SYNTHETIC spans only.

25 inference attempts, 5 of them failed: error rate 0.2 >= 0.05 fires (n 25 >= 20); latency
p95 is 100 ms < 10 s (ok); no streaming (TTFC insufficient); cost unknown for the mock provider
(insufficient: no priced call, never "0 <= 1.00").
"""

from datetime import UTC, datetime

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
START = datetime(2026, 11, 20, 13, 0, tzinfo=UTC)
BASE = int((START - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * SEC
WINDOW = {"from": "2026-11-20T00:00:00Z", "to": "2026-11-21T00:00:00Z"}


@pytest.fixture
def seeded(api_client, make_key):
    k = make_key("p10f")
    spans = []
    for i in range(25):
        start = BASE + i * SEC
        spans.append(
            span(
                trace_id=f"10f{i:029x}",
                span_id=f"{i + 1:016x}",
                name="chat gpt-4o-mini",
                kind=Span.SPAN_KIND_CLIENT,
                start=start,
                end=start + 100 * MS,
                status_code=2 if i < 5 else 0,
                attrs=inference_attrs(
                    gen_ai__response__id=f"chatcmpl-SYNTHETIC-10f-{i}",
                    llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{50_000 + i:012d}",
                ),
            )
        )
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    return k


def test_rules_fire_only_on_enough_data(seeded, api_client):
    r = api_client.get(
        "/api/v1/alerts", params={"project": seeded.project_id, **WINDOW}, headers=ADMIN_AUTH
    )
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    doc = r.json()
    assert doc["delivery"] == "none" and doc["contract_version"] == "llminsight.alert_rules.v1"
    rules = {x["id"]: x for x in doc["rules"]}
    assert (rules["inference_error_rate"]["state"], rules["inference_error_rate"]["value"]) == (
        "firing",
        "0.2",
    )
    assert rules["inference_latency_p95"]["state"] == "ok"
    assert rules["time_to_first_chunk_p95"]["state"] == "insufficient_data"
    assert rules["known_cost_window"]["state"] == "insufficient_data"
    assert rules["known_cost_window"]["value"] is None
    assert doc["firing"] == 1


def test_an_empty_window_fires_nothing(seeded, api_client):
    r = api_client.get(
        "/api/v1/alerts",
        params={
            "project": seeded.project_id,
            "from": "2026-11-18T00:00:00Z",
            "to": "2026-11-19T00:00:00Z",
        },
        headers=ADMIN_AUTH,
    )
    doc = r.json()
    assert doc["firing"] == 0
    assert {x["state"] for x in doc["rules"]} == {"insufficient_data"}


def test_scope_and_auth(seeded, api_client):
    assert (
        api_client.get(
            "/api/v1/alerts", params={"project": 999_999}, headers=ADMIN_AUTH
        ).status_code
        == 404
    )
    assert (
        api_client.get("/api/v1/alerts", params={"project": seeded.project_id}).status_code == 401
    )
