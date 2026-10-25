"""Phase 2C round trip: FastAPI demo → SDK → OTLP/HTTP protobuf → live `python -m app` →
PostgreSQL → `/api/v1/traces` and `/api/v1/overview` (T10 completion, T32 against real data).

SYNTHETIC and offline: mock provider plus the real `openai` client on an in-process fake
transport; `block_network` fails the test if anything non-loopback is contacted. The
verified catalogue is seeded, so the fake OpenAI calls get a computed cost while the mock
provider (never priced) stays unknown.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import pytest

import llm_insight_sdk as lli
from tests.integration.conftest import LiveBackend, _run_backend
from tests.openai_fixtures import block_network

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from demo_app import web  # noqa: E402

pytestmark = pytest.mark.integration

SECRET_TEXT = "USER-TEXT-SENTINEL-live-demo-9c3e"
# docs/PRICING.md §4: the fake answer is the T19 example (1200 in / 1000 cached, 300 out)
OPENAI_COST = Decimal("0.0015750000")
EXPECTED = {  # route -> (inference attempts, failed attempts, trace status)
    "/v1/summarize": (1, 0, "ok"),
    "/v1/summarize/multi": (3, 0, "ok"),
    "/v1/summarize/failure": (1, 1, "error"),
    "/v1/summarize/retry": (2, 1, "error"),  # recovered retry still marks the trace (R27)
    "/v1/summarize/openai": (1, 0, "ok"),
}


@pytest.fixture(scope="module")
def run(live_backend: LiveBackend) -> Iterator[dict]:
    out = _run_backend(live_backend.python, live_backend.env, ["-m", "app.cli", "seed-prices"])
    assert "(catalogue openai-2026-10-24)" in out  # idempotent: inserts or reports unchanged
    key, prefix = live_backend.create_key("sdk-demo-web-it")
    (row,) = live_backend.query(
        "SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,)
    )
    lli.shutdown(timeout_millis=3000)
    telemetry = lli.init(
        lli.LLMInsightConfig(
            endpoint=live_backend.ingest_endpoint,
            project_key=key,
            service_name="demo-web-live-it",
            service_version="0.2.0",
            deployment_environment="test",
            schedule_delay_millis=100,
            export_timeout_seconds=3.0,
        )
    )
    started = datetime.now(UTC)
    attempts: list[str] = []
    with block_network(attempts):
        demo = web.serve_in_thread(web.create_app(telemetry, mock_time_scale=0.1))
        try:
            answers = {route: web.call(demo.url, route, SECRET_TEXT) for route in EXPECTED}
        finally:
            demo.stop()
        delivered = telemetry.force_flush(10_000)
    telemetry.shutdown(3000)
    assert attempts == [], f"the demo tried to reach the network: {attempts}"
    assert delivered
    yield {
        "project": int(row[0]),
        "key": key,
        "answers": answers,
        "window": {
            "from": (started - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (datetime.now(UTC) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


def api(live: LiveBackend, path: str, **params) -> dict:
    return live.api(f"{path}?{urlencode(params)}")


@pytest.mark.parametrize("route", list(EXPECTED))
def test_each_request_is_one_trace_rooted_at_its_server_span(live_backend, run, route):
    pid, answer = run["project"], run["answers"][route]
    assert answer["http_status"] == 200
    tid = answer["trace_id"]
    attempts, failed, status = EXPECTED[route]
    spans = live_backend.query(
        "SELECT span_id, parent_span_id, kind, name, status, provenance,"
        " server_dropped_attributes_count FROM spans WHERE project_id = %s AND trace_id = %s",
        (pid, tid),
    )
    roots = [s for s in spans if s[1] is None]
    assert len(roots) == 1
    root = roots[0]
    assert (root[2], root[3]) == ("SERVER", f"POST {route}")
    kids = [s for s in spans if s[1] is not None]
    assert len(kids) == attempts
    assert all(k[1] == root[0] and k[2] == "CLIENT" for k in kids)  # parent = the request span
    assert [s[6] for s in spans] == [0] * len(spans)  # every attribute allowlisted, none dropped
    # Span-level provenance exists only for inference spans (TELEMETRY_CONTRACT.md §5): the
    # request span is `unknown`; the trace is `synthetic`, derived from its inference spans.
    assert root[5] == "unknown"
    assert all(k[5] == "synthetic" for k in kids)
    assert sum(k[4] == "ERROR" for k in kids) == failed
    ((trace_status, provenance, span_count),) = live_backend.query(
        "SELECT status, provenance, span_count FROM traces WHERE project_id = %s AND trace_id = %s",
        (pid, tid),
    )
    assert (trace_status, provenance, span_count) == (status, "synthetic", attempts + 1)
    # usage: one row per physical attempt, none for the request span (no parent double count)
    usage = live_backend.query(
        "SELECT span_id FROM usage_records WHERE project_id = %s AND trace_id = %s", (pid, tid)
    )
    assert sorted(u[0] for u in usage) == sorted(k[0] for k in kids)
    detail = api(live_backend, f"/api/v1/traces/{tid}", project=pid)
    roles = {s["span_id"]: s["role"] for s in detail["spans"]}
    assert roles[root[0]] == "root" and all(roles[k[0]] == "child" for k in kids)
    api_root = next(s for s in detail["spans"] if s["role"] == "root")
    assert sorted(api_root["children"]) == sorted(k[0] for k in kids)
    assert detail["trace"]["usage"]["inference_calls"] == attempts


def test_costs_are_computed_for_openai_and_unknown_for_the_mock(live_backend, run):
    rows = live_backend.query(
        "SELECT provider, cost_status, cost_unknown_reason, cost_usd, price_version, usage_source"
        " FROM usage_records WHERE project_id = %s",
        (run["project"],),
    )
    openai_rows = [r for r in rows if r[0] == "openai"]
    mock_rows = [r for r in rows if r[0] == "llminsight.mock"]
    assert len(openai_rows) == 2 and len(mock_rows) == 6
    assert all(
        r[1:] == ("computed", None, OPENAI_COST, "openai-2026-10-24", "provider_reported")
        for r in openai_rows
    )
    assert all(r[1] == "unknown" and r[3] is None for r in mock_rows)  # never 0
    reasons = sorted(r[2] for r in mock_rows)
    assert reasons == ["price_missing"] * 4 + ["usage_missing"] * 2  # failed attempts: no usage


def test_the_metrics_api_reflects_the_demo_traffic(live_backend, run):
    o = api(live_backend, "/api/v1/overview", project=run["project"], **run["window"])
    assert o["traces"]["count"]["total"] == 5
    assert o["traces"]["count"]["by_provenance"]["synthetic"] == 5
    assert o["traces"]["error_rate"]["total"]["error_traces"] == 2
    inf = o["inference"]
    assert inf["attempts"]["total"] == 8  # 1 + 3 + 1 + 2 + 1 physical attempts
    assert inf["attempts"]["by_provenance"] == {"real": 0, "synthetic": 8, "unknown": 0}
    assert inf["logical_calls"]["total"]["value"] == 7  # the retry's two attempts are one call
    assert inf["errors"]["total"]["failed"] == 2
    assert sorted((e["error_type"], e["count"]) for e in inf["errors"]["by_error_type"]) == [
        ("429", 1),
        ("500", 1),
    ]
    cost = inf["cost"]["total"]
    assert cost["known_cost_usd"] == str(2 * OPENAI_COST)
    assert (cost["computed_cost_calls"], cost["unknown_cost_calls"]) == (2, 6)
    assert cost["unknown_cost_by_reason"] == {
        "price_missing": 4,
        "usage_missing": 2,
        "usage_inconsistent": 0,
    }
    assert cost["cost_complete"] is False
    assert inf["latency"]["total"]["n"] == 8 and inf["latency"]["total"]["p50_ms"] is not None
    assert o["traces"]["latency"]["total"]["n"] == 5  # one SERVER root per request
    assert o["traces"]["latency"]["traces_without_root"] == 0
    assert inf["time_to_first_chunk"]["available"] is False
    versions = inf["price_versions"]["items"]
    assert {(v["model"], v["price_version"], v["calls"]) for v in versions} == {
        ("gpt-5.4-mini-2026-03-17", "openai-2026-10-24", 2)
    }
    groups = api(live_backend, "/api/v1/models/compare", project=run["project"], **run["window"])
    by_provider: dict[str, int] = {}
    for g in groups["groups"]:
        by_provider[g["provider"]] = by_provider.get(g["provider"], 0) + g["attempts"]
    assert by_provider == {"llminsight.mock": 6, "openai": 2}


def test_no_content_or_credentials_reach_the_db_api_or_log(live_backend, run):
    pid = run["project"]
    dump = json.dumps(
        live_backend.query(
            "SELECT row_to_json(s) FROM spans s WHERE project_id = %s"
            " UNION ALL SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s",
            (pid, pid),
        ),
        default=str,
    )
    api_text = json.dumps(api(live_backend, "/api/v1/overview", project=pid, **run["window"]))
    for answer in run["answers"].values():
        api_text += json.dumps(
            api(live_backend, f"/api/v1/traces/{answer['trace_id']}", project=pid)
        )
    log = live_backend.log_text()
    assert live_backend.log_tracebacks() == []
    for text in (dump, api_text, log):
        for forbidden in (
            SECRET_TEXT,
            web.SYSTEM_PROMPT,
            web.OFFLINE_KEY,
            run["key"],
            "SYNTHETIC fake completion",
            "SYNTHETIC mock completion",
        ):
            assert forbidden not in text
