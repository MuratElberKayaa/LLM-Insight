"""Phase 6C-A data path: Gemini fake response → SDK (adapter and live runner) → official
OTLP exporter → live `python -m app` → PostgreSQL → `/api/v1` (the dashboard's read API).

The Gemini side is OFFLINE (SYNTHETIC fixtures behind `httpx.MockTransport`, every
non-loopback connection blocked; `synthetic` provenance). The backend side is real and uses
the session's disposable database only. The OpenAI catalogue is seeded, to show that no
OpenAI price is ever applied to a Gemini call and that a Gemini cost stays unknown, never 0.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import llm_insight_sdk as lli
from llm_insight_sdk.gemini_adapter import GeminiTextAdapter, ObservedGeminiClient
from llm_insight_sdk.providers import TextRequest
from llm_insight_sdk.telemetry import build_span_exporter
from tests.conftest import PROMPT_SENTINEL
from tests.gemini_fixtures import (
    ERROR_SENTINEL,
    FAKE_GEMINI_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    THOUGHT_SENTINEL,
    FakeGemini,
    GStep,
    g_fail,
    g_ok,
)
from tests.integration.conftest import LiveBackend, _run_backend
from tests.openai_fixtures import block_network
from tests.unit.test_gemini_live_guard import NOW, gemini_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import live_gemini  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("gemini_released")]  # ADR-50

SECRETS = (FAKE_GEMINI_KEY, PROMPT_SENTINEL, OUTPUT_SENTINEL, THOUGHT_SENTINEL, ERROR_SENTINEL)


@pytest.fixture(scope="module")
def priced_backend(live_backend: LiveBackend) -> LiveBackend:
    out = _run_backend(live_backend.python, live_backend.env, ["-m", "app.cli", "seed-prices"])
    assert "(catalogue openai-2026-10-24)" in out
    return live_backend


@pytest.fixture(scope="module")
def project(priced_backend: LiveBackend) -> tuple[int, str]:
    key, prefix = priced_backend.create_key("sdk-live-gemini-it")
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
            service_name="sdk-live-gemini-it",
            schedule_delay_millis=100,
            export_timeout_seconds=3.0,
        )
    )
    yield t
    t.shutdown()


def adapter(telemetry, fake: FakeGemini) -> GeminiTextAdapter:
    return GeminiTextAdapter(ObservedGeminiClient(telemetry, fake.client(), provenance="synthetic"))


def request() -> TextRequest:
    return TextRequest(model=MODEL, input_text=PROMPT_SENTINEL, max_output_tokens=64)


def trace_of(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


def usage_rows(live: LiveBackend, pid: int, tid: str) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s"
        " ORDER BY attempt_id, span_id",
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


def runner_env(live: LiveBackend, project: tuple[int, str]) -> dict[str, str]:
    pid, key = project
    env = gemini_env()
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": live.ingest_endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={key}",
            "OTEL_SERVICE_NAME": "live-gemini-it",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "100",
            "LLMI_DASHBOARD_PROJECT_ID": str(pid),
        }
    )
    return env


def run_runner(live: LiveBackend, project: tuple[int, str], fake: FakeGemini) -> tuple[int, dict]:
    out = io.StringIO()
    code = live_gemini.main(
        ["--execute"],
        runner_env(live, project),
        now=NOW,
        client_factory=lambda policy: fake.client(),
        provenance="synthetic",
        out=out,
    )
    return code, json.loads(out.getvalue())


# ---------------------------------------------------------------- runner → database → API
def test_runner_trace_reaches_postgresql_and_the_read_api(priced_backend, project):
    pid, key = project
    started = datetime.now(UTC)
    fake = FakeGemini([g_ok(response_id="SYNTHETICgeminiIT01")])
    code, doc = run_runner(priced_backend, project, fake)
    assert code == 0 and len(fake.requests) == 1
    assert doc["telemetry"]["flushed"] is True and doc["status"] == "completed"
    tid = doc["trace_id"]
    assert doc["dashboard_url"].endswith(f"/traces/{tid}?project={pid}")

    spans = priced_backend.query(
        "SELECT name, kind, parent_span_id, span_id, status, provenance,"
        " attributes_schema_version, server_dropped_attributes_count"
        " FROM spans WHERE project_id = %s AND trace_id = %s ORDER BY kind",
        (pid, tid),
    )
    assert len(spans) == 2
    by_kind = {s[1]: s for s in spans}
    assert by_kind["INTERNAL"][0] == live_gemini.ROOT_SPAN
    assert by_kind["CLIENT"][0] == f"generate_content {MODEL}"
    assert by_kind["CLIENT"][2] == by_kind["INTERNAL"][3]  # correct parent
    assert by_kind["CLIENT"][4] != "ERROR" and by_kind["CLIENT"][5] == "synthetic"
    assert by_kind["CLIENT"][6] == "llminsight.attrmap.v1"
    assert by_kind["CLIENT"][7] == 0  # every Gemini attribute is allowlisted: no change needed
    attrs = client_span(priced_backend, pid, tid)["attributes"]
    assert attrs["gen_ai.provider.name"] == "gcp.gemini"
    assert attrs["gen_ai.operation.name"] == "generate_content"
    assert attrs["gen_ai.response.finish_reasons"] == ["STOP"]

    (u,) = usage_rows(priced_backend, pid, tid)
    assert (u["provider"], u["requested_model"], u["response_model"]) == (
        "gcp.gemini",
        MODEL,
        MODEL,
    )
    assert (u["input_tokens"], u["cached_input_tokens"]) == (20, 4)
    assert (u["output_tokens"], u["reasoning_output_tokens"]) == (32, 30)
    assert u["cache_write_input_tokens"] is None
    assert u["usage_source"] == "provider_reported" and u["attempt_id"] == 1
    # Free Tier: no price applies (not the OpenAI rows, not a paid list price), never 0
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert u["price_version"] is None and u["model_price_id"] is None
    assert u["provider_response_id"] == "SYNTHETICgeminiIT01"
    assert u["canonical_call_key"] == "resp:gcp.gemini:SYNTHETICgeminiIT01"

    detail = priced_backend.api(f"/api/v1/traces/{tid}?project={pid}")
    assert detail["trace"]["trace_id"] == tid and len(detail["spans"]) == 2
    assert [x["cost_usd"] for x in detail["usage"]] == [None]
    assert detail["trace"]["usage"]["inference_calls"] == 1
    window = {
        "project": pid,
        "from": (started - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (datetime.now(UTC) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    listing = priced_backend.api(f"/api/v1/traces?{urlencode(window)}")
    assert tid in {t["trace_id"] for t in listing["items"]}
    overview = priced_backend.api(f"/api/v1/overview?{urlencode(window)}")
    cost = overview["inference"]["cost"]["total"]
    assert cost["cost_complete"] is False and cost["unknown_cost_by_reason"]["price_missing"] >= 1
    groups = priced_backend.api(f"/api/v1/models/compare?{urlencode(window)}")["groups"]
    assert any(g["provider"] == "gcp.gemini" for g in groups)

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
    for secret in (*SECRETS, key, live_gemini.INPUT_TEXT):
        assert secret not in text
        assert secret not in log


@pytest.mark.parametrize(
    ("step", "span_status", "error_type", "reason"),
    [
        (g_fail(429, "RESOURCE_EXHAUSTED"), "ERROR", "429", "usage_missing"),
        (GStep(raise_=httpx.ReadTimeout), "ERROR", "timeout", "usage_missing"),
        (
            g_ok(finish="MAX_TOKENS", response_id="SYNTHETICitMaxTokens"),
            "UNSET",
            None,
            "price_missing",
        ),
        (
            GStep(
                200,
                {
                    "promptFeedback": {"blockReason": "SAFETY"},
                    "usageMetadata": {"promptTokenCount": 9, "totalTokenCount": 9},
                },
            ),
            "ERROR",
            "gcp.gemini.prompt_blocked",
            "price_missing",
        ),
        (g_ok(usage=None, response_id="SYNTHETICitNoUsage"), "UNSET", None, "usage_missing"),
    ],
)
def test_outcomes_are_stored_once_with_unknown_cost(
    priced_backend, project, telemetry, step, span_status, error_type, reason
):
    pid, _ = project
    fake = FakeGemini([step])
    with telemetry.tracer.start_as_current_span("POST /v1/answer") as root:
        adapter(telemetry, fake).generate_text(request())
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)
    span = client_span(priced_backend, pid, tid)
    assert (span["status"], span["error_type"]) == (span_status, error_type)
    (u,) = usage_rows(priced_backend, pid, tid)
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (None, "unknown", reason)


def test_repeated_export_does_not_duplicate_spans_or_usage(priced_backend, project, telemetry):
    pid, key = project
    memory = InMemorySpanExporter()
    telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(memory))
    with telemetry.tracer.start_as_current_span("demo.reexport") as root:
        adapter(telemetry, FakeGemini([g_ok(response_id="SYNTHETICreexport")])).generate_text(
            request()
        )
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)

    def counts() -> tuple[int, int]:
        (row,) = priced_backend.query(
            "SELECT (SELECT count(*) FROM spans WHERE project_id = %s AND trace_id = %s),"
            " (SELECT count(*) FROM usage_records WHERE project_id = %s AND trace_id = %s)",
            (pid, tid, pid, tid),
        )
        return int(row[0]), int(row[1])

    assert counts() == (2, 1)  # the parent span carries no usage: no double count
    finished = memory.get_finished_spans()
    exporter = build_span_exporter(
        lli.LLMInsightConfig(
            endpoint=priced_backend.ingest_endpoint,
            project_key=key,
            service_name="sdk-live-gemini-it",
            export_timeout_seconds=3.0,
        )
    )
    try:
        for _ in range(2):  # an exporter or collector resending the same spans
            assert exporter.export(finished) == SpanExportResult.SUCCESS
    finally:
        exporter.shutdown()
    assert counts() == (2, 1)
