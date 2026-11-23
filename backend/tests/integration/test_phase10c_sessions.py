"""Phase 10C (ADR-56): pseudonymous sessions through the real ingest path and the read API.

SYNTHETIC spans only. Project A has two sessions (alpha: 2 traces, one failed; beta: 1 trace) and
one trace without a session; project B reuses alpha's raw conversation ID. The raw IDs are
sentinels that must never appear in any stored row or API response.
"""

from datetime import UTC, datetime

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from app.services.ingestion import sessions
from tests.helpers import DUMMY_PEPPER
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
BASE = (
    int(
        (
            datetime(2026, 11, 20, 11, 0, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        ).total_seconds()
    )
    * SEC
)
WINDOW = {"from": "2026-11-20T00:00:00Z", "to": "2026-11-21T00:00:00Z"}
ALPHA = "conv-SESSION-SENTINEL-alpha-user@example.com"  # deliberately personal-looking
BETA = "conv-SESSION-SENTINEL-beta"


def trace(i: int, conversation: str | None, failed: bool = False) -> Span:
    extra = {} if conversation is None else {"gen_ai__conversation__id": conversation}
    start = BASE + i * SEC
    return span(
        trace_id=f"10c{i:029x}",
        span_id=f"{i:016x}",
        name="chat gpt-4o-mini",
        kind=Span.SPAN_KIND_CLIENT,
        start=start,
        end=start + 100 * MS,
        status_code=2 if failed else 0,
        attrs=inference_attrs(
            gen_ai__response__id=f"chatcmpl-SYNTHETIC-10c-{i}",
            llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{30_000 + i:012d}",
            **extra,
        ),
    )


@pytest.fixture
def seeded(api_client, make_key):
    a, b = make_key("p10c-a"), make_key("p10c-b")
    batch_a = [trace(1, ALPHA), trace(2, ALPHA, failed=True), trace(3, BETA), trace(4, None)]
    assert post(api_client, body(request(*batch_a)), a.key).status_code == 200
    assert post(api_client, body(request(trace(5, ALPHA))), b.key).status_code == 200
    return a, b


def hash_for(project_id: int, raw: str) -> str:
    return sessions.pseudonymizer(DUMMY_PEPPER.encode("utf-8"), project_id)(raw)


def get(client, path, project, **params):
    r = client.get(path, params={"project": project, **WINDOW, **params}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r


def test_only_the_pseudonym_is_stored(seeded, db):
    a, b = seeded
    stored = rows(
        db,
        "SELECT project_id, trace_id, attributes, server_dropped_attributes_count FROM spans"
        " WHERE project_id IN (:a, :b) ORDER BY trace_id",
        a=a.project_id,
        b=b.project_id,
    )
    hashes = {r["trace_id"][-1]: r["attributes"].get("llminsight.session.hash") for r in stored}
    assert hashes == {
        "1": hash_for(a.project_id, ALPHA),
        "2": hash_for(a.project_id, ALPHA),
        "3": hash_for(a.project_id, BETA),
        "4": None,
        "5": hash_for(b.project_id, ALPHA),
    }
    assert hashes["5"] != hashes["1"]  # no cross-project linkage
    assert all(r["server_dropped_attributes_count"] == 0 for r in stored)
    dump = repr(rows(db, "SELECT * FROM spans")) + repr(rows(db, "SELECT * FROM traces"))
    dump += repr(rows(db, "SELECT * FROM usage_records"))
    assert "SENTINEL" not in dump and "example.com" not in dump


def test_sessions_endpoint_aggregates_by_pseudonym(seeded, api_client):
    a, _ = seeded
    r = get(api_client, "/api/v1/sessions", a.project_id)
    assert r.headers["cache-control"] == "no-store"
    doc = r.json()
    assert "SENTINEL" not in r.text
    assert doc["pseudonymous"] is True and doc["truncated"] is False
    alpha, beta = doc["items"]
    assert alpha["session"] == hash_for(a.project_id, ALPHA)
    assert (alpha["traces"], alpha["error_traces"], alpha["attempts"]) == (2, 1, 2)
    assert (alpha["input_tokens"], alpha["output_tokens"]) == (200, 40)
    assert alpha["known_cost_usd"] is None and alpha["unknown_cost_calls"] == 2
    assert alpha["cost_complete"] is False
    assert alpha["first_seen"] < alpha["last_seen"]
    assert (beta["session"], beta["traces"]) == (hash_for(a.project_id, BETA), 1)


def test_the_trace_list_filters_by_session(seeded, api_client):
    a, _ = seeded
    h = hash_for(a.project_id, ALPHA)
    items = get(api_client, "/api/v1/traces", a.project_id, session=h).json()["items"]
    assert sorted(i["trace_id"][-1] for i in items) == ["1", "2"]
    # A raw ID or any other format is refused, never echoed.
    r = api_client.get(
        "/api/v1/traces", params={"project": a.project_id, "session": ALPHA}, headers=ADMIN_AUTH
    )
    assert r.status_code == 422 and "SENTINEL" not in r.text


def test_sessions_are_project_scoped_and_bounded(seeded, api_client):
    _, b = seeded
    doc = get(api_client, "/api/v1/sessions", b.project_id).json()
    assert [i["session"] for i in doc["items"]] == [hash_for(b.project_id, ALPHA)]
    one = get(api_client, "/api/v1/sessions", seeded[0].project_id, limit=1).json()
    assert len(one["items"]) == 1 and one["truncated"] is True and one["limit"] == 1
    r = api_client.get("/api/v1/sessions", params={"project": 999_999}, headers=ADMIN_AUTH)
    assert r.status_code == 404
    assert api_client.get("/api/v1/sessions", params={"project": b.project_id}).status_code == 401
