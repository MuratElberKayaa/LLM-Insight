"""Phase 10D (ADR-57): deterministic error patterns through the real ingest path and the API.

SYNTHETIC spans only, all in one project:

| Trace | Span | Pattern |
|---|---|---|
| 1 | chat, ERROR, error.type 429 | inference · openai · gpt-4o-mini-2024-07-18 · 429 |
| 1 | chat, ERROR, error.type 429 (a retry) | same |
| 2 | chat, ERROR, error.type 429 | same (3 spans, 2 traces) |
| 2 | chat, ERROR, error.type timeout | inference · … · timeout |
| 3 | execute_tool search_kb, ERROR, no error.type | tool · search_kb · NULL (2 spans) |
| 3 | execute_tool search_kb, ERROR | same |
| 3 | invoke_agent triage, ERROR, error.type _OTHER | agent · openai · _OTHER |
| 4 | chat, OK | not an error |
| 5 | chat, ERROR, 429, trace outside the window | not counted |
"""

from datetime import UTC, datetime

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
START = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
BASE = int((START - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * SEC
WINDOW = {"from": "2026-11-20T00:00:00Z", "to": "2026-11-21T00:00:00Z"}
MODEL = "gpt-4o-mini-2024-07-18"
n = 0


def mk(trace: int, attrs: dict, error: bool, offset_s: int = 0) -> Span:
    global n
    n += 1
    start = BASE + trace * SEC + offset_s * SEC + n * MS
    return span(
        trace_id=f"10d{trace:029x}",
        span_id=f"{n:016x}",
        name="span",
        kind=Span.SPAN_KIND_INTERNAL,
        start=start,
        end=start + MS,
        status_code=2 if error else 0,
        attrs=attrs,
    )


def chat(i: int, error_type: str | None) -> dict:
    a = inference_attrs(
        gen_ai__response__id=f"chatcmpl-SYNTHETIC-10d-{i}",
        llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{40_000 + i:012d}",
    )
    if error_type is not None:
        a["error.type"] = error_type
    return a


TOOL = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search_kb"}
AGENT = {
    "gen_ai.operation.name": "invoke_agent",
    "gen_ai.provider.name": "openai",
    "gen_ai.agent.name": "triage",
    "error.type": "_OTHER",
}


@pytest.fixture
def seeded(api_client, make_key):
    k = make_key("p10d")
    spans = [
        mk(1, chat(1, "429"), True),
        mk(1, chat(2, "429"), True),
        mk(2, chat(3, "429"), True),
        mk(2, chat(4, "timeout"), True),
        mk(3, TOOL, True),
        mk(3, TOOL, True),
        mk(3, AGENT, True),
        mk(4, chat(5, None), False),
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    late = mk(5, chat(6, "429"), True, offset_s=86_400)  # the next day: outside WINDOW
    assert post(api_client, body(request(late)), k.key).status_code == 200
    return k


def get(client, project, **params):
    r = client.get(
        "/api/v1/error-patterns",
        params={"project": project, **WINDOW, **params},
        headers=ADMIN_AUTH,
    )
    assert r.status_code == 200, r.text
    return r


def test_patterns_group_by_exact_fingerprint(seeded, api_client):
    r = get(api_client, seeded.project_id)
    assert r.headers["cache-control"] == "no-store"
    doc = r.json()
    assert doc["method"] == "exact_fingerprint" and doc["error_spans"] == 7
    got = [
        (
            p["source"],
            p["provider"],
            p["model"],
            p["tool_name"],
            p["error_type"],
            p["spans"],
            p["traces"],
        )
        for p in doc["patterns"]
    ]
    assert got == [
        ("inference", "openai", MODEL, None, "429", 3, 2),
        ("tool", None, None, "search_kb", None, 2, 1),
        ("agent", "openai", None, None, "_OTHER", 1, 1),
        ("inference", "openai", MODEL, None, "timeout", 1, 1),
    ]
    first = doc["patterns"][0]
    assert first["error_class"] == "http_429"  # the diagnostics reading of error.type
    assert doc["patterns"][3]["error_class"] == "timeout"
    assert first["latest_trace_id"] == "10d" + f"{2:029x}"  # newest trace of the pattern
    assert first["first_seen"] < first["last_seen"]
    assert doc["patterns"][1]["error_class"] == "not_recorded"


def test_limit_and_truncation(seeded, api_client):
    doc = get(api_client, seeded.project_id, limit=2).json()
    assert len(doc["patterns"]) == 2 and doc["truncated"] is True and doc["error_spans"] == 7


def test_scope_and_auth(seeded, api_client):
    assert (
        api_client.get(
            "/api/v1/error-patterns", params={"project": 999_999}, headers=ADMIN_AUTH
        ).status_code
        == 404
    )
    assert (
        api_client.get("/api/v1/error-patterns", params={"project": seeded.project_id}).status_code
        == 401
    )
