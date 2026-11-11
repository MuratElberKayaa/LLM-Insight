"""Phase 8A-2 data path: fake Groq response → SDK adapter → official OTLP exporter → live
`python -m app` → PostgreSQL → `/api/v1` (Overview, Traces, Model Comparison).

The Groq side is OFFLINE (SYNTHETIC fixtures behind `httpx.MockTransport`, every non-loopback
connection blocked, `synthetic` provenance). The backend side is real and uses the session's
disposable database only. A matching Groq price is seeded, to show it is never applied.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import pytest

import llm_insight_sdk as lli
from llm_insight_sdk.groq_adapter import GroqTextAdapter, ObservedGroqClient
from llm_insight_sdk.providers import TextRequest
from tests.conftest import PROMPT_SENTINEL
from tests.groq_fixtures import (
    ERROR_SENTINEL,
    FAKE_GROQ_KEY,
    GENERATION_SENTINEL,
    MODEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    FakeGroq,
    Step,
    fail,
    ok,
)
from tests.integration.conftest import LiveBackend
from tests.openai_fixtures import block_network

pytestmark = pytest.mark.integration

SECRETS = (
    FAKE_GROQ_KEY,
    PROMPT_SENTINEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    ERROR_SENTINEL,
    GENERATION_SENTINEL,
)


@pytest.fixture(scope="module")
def priced_backend(groq_price: LiveBackend) -> LiveBackend:
    """The session's one canonical groq price (`conftest.groq_price`), shared with every module."""
    return groq_price


@pytest.fixture(scope="module")
def project(priced_backend: LiveBackend) -> tuple[int, str]:
    key, prefix = priced_backend.create_key("sdk-live-groq-it")
    (row,) = priced_backend.query(
        "SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,)
    )
    return int(row[0]), key


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    lli.shutdown(timeout_millis=3000)
    with block_network(attempts):
        yield
    lli.shutdown(timeout_millis=3000)
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture
def telemetry(priced_backend, project) -> Iterator[lli.Telemetry]:
    t = lli.init(
        lli.LLMInsightConfig(
            endpoint=priced_backend.ingest_endpoint,
            project_key=project[1],
            service_name="sdk-live-groq-it",
            schedule_delay_millis=100,
            export_timeout_seconds=3.0,
        )
    )
    yield t
    t.shutdown()


def run(telemetry, fake: FakeGroq, root: str = "POST /v1/answer") -> str:
    adapter = GroqTextAdapter(
        ObservedGroqClient(
            telemetry, FAKE_GROQ_KEY, http_client=fake.http_client(), provenance="synthetic"
        )
    )
    request = TextRequest(
        model=MODEL,
        input_text=PROMPT_SENTINEL,
        max_output_tokens=64,
        prompt_name="groq-it",
        prompt_version="v1",
    )
    with telemetry.tracer.start_as_current_span(root) as span:
        adapter.generate_text(request)
    assert telemetry.force_flush(10_000)
    return format(span.get_span_context().trace_id, "032x")


def usage_rows(live: LiveBackend, pid: int, tid: str) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s",
        (pid, tid),
    )
    return [r[0] for r in rows]


def client_span(live: LiveBackend, pid: int, tid: str) -> dict:
    (row,) = live.query(
        "SELECT row_to_json(s) FROM spans s WHERE project_id = %s AND trace_id = %s"
        " AND kind = 'CLIENT'",
        (pid, tid),
    )
    return row[0]


def test_a_groq_trace_reaches_postgresql_and_every_read_view(priced_backend, project, telemetry):
    pid, key = project
    started = datetime.now(UTC)
    fake = FakeGroq([ok(response_id="chatcmpl-SYNTHETIC-groq-it-01")])
    tid = run(telemetry, fake, root="demo.groq")
    assert len(fake.requests) == 1

    span = client_span(priced_backend, pid, tid)
    assert span["name"] == f"chat {MODEL}" and span["status"] != "ERROR"
    assert span["provenance"] == "synthetic"
    assert span["attributes_schema_version"] == "llminsight.attrmap.v1"
    assert span["server_dropped_attributes_count"] == 0
    (root,) = priced_backend.query(
        "SELECT span_id FROM spans WHERE project_id = %s AND trace_id = %s AND name = 'demo.groq'",
        (pid, tid),
    )
    assert span["parent_span_id"] == root[0]
    assert span["attributes"]["gen_ai.provider.name"] == "groq"
    assert span["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]

    (u,) = usage_rows(priced_backend, pid, tid)
    assert (u["provider"], u["requested_model"], u["response_model"]) == ("groq", MODEL, MODEL)
    assert (u["input_tokens"], u["output_tokens"]) == (20, 32)
    assert u["usage_source"] == "provider_reported" and u["attempt_id"] == 1
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert u["price_version"] is None and u["model_price_id"] is None
    assert u["canonical_call_key"] == "resp:groq:chatcmpl-SYNTHETIC-groq-it-01"

    detail = priced_backend.api(f"/api/v1/traces/{tid}?project={pid}")
    assert len(detail["spans"]) == 2 and [x["cost_usd"] for x in detail["usage"]] == [None]
    window = {
        "project": pid,
        "provider": "groq",
        "from": (started - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    listing = priced_backend.api(f"/api/v1/traces?{urlencode(window)}")
    assert tid in {t["trace_id"] for t in listing["items"]}
    overview = priced_backend.api(f"/api/v1/overview?{urlencode(window)}")
    cost = overview["inference"]["cost"]["total"]
    assert cost["cost_complete"] is False and cost["unknown_cost_by_reason"]["price_missing"] >= 1
    assert cost["known_cost_usd"] is None
    groups = priced_backend.api(f"/api/v1/models/compare?{urlencode(window)}")["groups"]
    (group,) = [g for g in groups if g["response_model"] == MODEL]
    assert group["provider"] == "groq" and group["requested_model"] == MODEL
    assert group["cost"]["known_cost_usd"] is None and group["cost"]["cost_complete"] is False

    text = json.dumps([detail, listing, overview, groups])
    text += json.dumps(
        priced_backend.query(
            "SELECT row_to_json(s) FROM spans s WHERE project_id = %s AND trace_id = %s"
            " UNION ALL SELECT row_to_json(u) FROM usage_records u"
            " WHERE project_id = %s AND trace_id = %s",
            (pid, tid, pid, tid),
        ),
        default=str,
    )
    log = priced_backend.log_text()
    for secret in (*SECRETS, key):
        assert secret not in text
        assert secret not in log


@pytest.mark.parametrize(
    ("step", "span_status", "error_type", "reason"),
    [
        (fail(429, "rate_limit_exceeded"), "ERROR", "429", "usage_missing"),
        (Step(raise_=httpx.ReadTimeout), "ERROR", "timeout", "usage_missing"),
        (Step(raw=b"{bad"), "ERROR", "groq.response.malformed", "usage_missing"),
        (
            ok(finish="length", response_id="chatcmpl-SYNTHETIC-it-len"),
            "UNSET",
            None,
            "price_missing",
        ),
        (ok(usage=None, response_id="chatcmpl-SYNTHETIC-it-nou"), "UNSET", None, "usage_missing"),
    ],
)
def test_outcomes_are_stored_once_with_unknown_cost(
    priced_backend, project, telemetry, step, span_status, error_type, reason
):
    pid, _ = project
    tid = run(telemetry, FakeGroq([step]))
    span = client_span(priced_backend, pid, tid)
    assert (span["status"], span["error_type"]) == (span_status, error_type)
    assert span["server_dropped_attributes_count"] == 0
    (u,) = usage_rows(priced_backend, pid, tid)
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (None, "unknown", reason)
