"""Phase 6A data path: Responses adapter and live runner → official OTLP exporter → live
`python -m app` → PostgreSQL → `/api/v1` (the dashboard's read API).

The OpenAI side is OFFLINE (SYNTHETIC fixtures behind `httpx2.MockTransport`, every
non-loopback connection blocked; `synthetic` provenance). The backend side is real: the
verified catalogue is seeded with `python -m app.cli seed-prices`, and the SDK's price
mirror is compared with the stored rows, so drift between the two copies fails here.
"""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

import llm_insight_sdk as lli
from llm_insight_sdk import live
from llm_insight_sdk.openai_adapter import ObservedOpenAIClient, OpenAIResponsesTextAdapter
from llm_insight_sdk.providers import TextRequest
from tests.conftest import PROMPT_SENTINEL
from tests.integration.conftest import LiveBackend, _run_backend
from tests.openai_fixtures import (
    COMPLETION_SENTINEL,
    FAKE_API_KEY,
    FakeOpenAI,
    Step,
    block_network,
    fail,
    response_ok,
)
from tests.unit.test_live_guard import live_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import live_openai  # noqa: E402

pytestmark = pytest.mark.integration

MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
EXPECTED_COST = Decimal("0.0015750000")  # docs/PRICING.md §4, T19 usage
NOW = live.PRICE_VERIFIED_AT + timedelta(days=1)


@pytest.fixture(scope="module")
def priced_backend(live_backend: LiveBackend) -> LiveBackend:
    out = _run_backend(live_backend.python, live_backend.env, ["-m", "app.cli", "seed-prices"])
    assert "(catalogue openai-2026-10-24)" in out
    return live_backend


@pytest.fixture(scope="module")
def project(priced_backend: LiveBackend) -> tuple[int, str]:
    key, prefix = priced_backend.create_key("sdk-live-responses-it")
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
            service_name="sdk-live-responses-it",
            schedule_delay_millis=100,
            export_timeout_seconds=3.0,
        )
    )
    yield t
    t.shutdown()


def adapter(telemetry, fake: FakeOpenAI, **kw) -> OpenAIResponsesTextAdapter:
    llm = ObservedOpenAIClient(
        telemetry, fake.client(), provenance="synthetic", backoff_seconds=0.0
    )
    return OpenAIResponsesTextAdapter(llm, **kw)


def request() -> TextRequest:
    return TextRequest(model=MODEL, input_text=PROMPT_SENTINEL, max_output_tokens=64)


def usage_rows(live_: LiveBackend, project_id: int, trace_id: str) -> list[dict]:
    rows = live_.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s"
        " ORDER BY attempt_id, span_id",
        (project_id, trace_id),
    )
    return [r[0] for r in rows]


def trace_of(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


# ---------------------------------------------------------------- price mirror drift
def test_sdk_price_mirror_equals_the_seeded_catalogue(priced_backend):
    rows = priced_backend.query(
        "SELECT provider, model, input_per_million, cached_input_per_million,"
        " output_per_million, cache_write_input_per_million, version, source_url, verified_at"
        " FROM model_prices WHERE version = %s",
        (live.PRICE_CATALOGUE_VERSION,),
    )
    stored = {
        (r[0], r[1]): (Decimal(r[2]), Decimal(r[3]), Decimal(r[4]), r[5], r[6], r[7], r[8])
        for r in rows
    }
    mirror = {
        key: (
            p.input,
            p.cached_input,
            p.output,
            p.cache_write,
            p.version,
            p.source_url,
            live.PRICE_VERIFIED_AT,
        )
        for key, p in live.VERIFIED_PRICES.items()
    }
    assert stored == mirror


# ---------------------------------------------------------------- adapter → database
def test_responses_call_is_stored_with_the_verified_cost(priced_backend, project, telemetry):
    pid, _ = project
    fake = FakeOpenAI([response_ok(response_id="resp_SYNTHETIC_it_cost")])
    with telemetry.tracer.start_as_current_span("POST /v1/answer") as root:
        outcome = adapter(telemetry, fake).generate_text(request())
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)
    assert outcome.status == "completed"

    (u,) = usage_rows(priced_backend, pid, tid)
    assert len(fake.requests) == 1 and u["attempt_id"] == 1
    assert (u["input_tokens"], u["cached_input_tokens"]) == (1200, 1000)
    assert (u["output_tokens"], u["reasoning_output_tokens"]) == (300, 200)
    assert u["cache_write_input_tokens"] == 0  # reported as 0 by the Responses API
    assert u["usage_source"] == "provider_reported"
    assert Decimal(str(u["cost_usd"])) == EXPECTED_COST
    assert (u["cost_status"], u["cost_unknown_reason"]) == ("computed", None)
    assert u["provider_response_id"] == "resp_SYNTHETIC_it_cost"
    assert u["call_key_source"] == "provider_response_id"
    assert (u["requested_model"], u["response_model"]) == (MODEL, SNAPSHOT)
    assert u["provenance"] == "synthetic"

    (span,) = priced_backend.query(
        "SELECT attributes, server_dropped_attributes_count, parent_span_id FROM spans"
        " WHERE project_id = %s AND trace_id = %s AND kind = 'CLIENT'",
        (pid, tid),
    )
    attrs, dropped, parent = span
    assert dropped == 0  # attrmap.v1 unchanged: every emitted attribute is allowlisted
    assert parent == format(root.get_span_context().span_id, "016x")
    assert attrs["openai.api.type"] == "responses"
    assert attrs["gen_ai.operation.name"] == "chat"


def test_missing_usage_is_unknown_cost_never_zero(priced_backend, project, telemetry):
    pid, _ = project
    with telemetry.tracer.start_as_current_span("POST /v1/answer") as root:
        adapter(telemetry, FakeOpenAI([response_ok(usage=None)])).generate_text(request())
    assert telemetry.force_flush(10_000)
    (u,) = usage_rows(priced_backend, pid, trace_of(root))
    assert u["input_tokens"] is None and u["output_tokens"] is None
    assert (u["cost_usd"], u["cost_status"]) == (None, "unknown")
    assert u["cost_unknown_reason"] == "usage_missing" and u["usage_source"] == "unknown"


def test_failed_and_retried_attempts_are_separate_unpriced_rows(priced_backend, project, telemetry):
    pid, _ = project
    fake = FakeOpenAI([fail(429, "rate_limit_exceeded"), response_ok()])
    with telemetry.tracer.start_as_current_span("POST /v1/answer") as root:
        adapter(telemetry, fake, max_attempts=2).generate_text(request())
    assert telemetry.force_flush(10_000)
    rows = usage_rows(priced_backend, pid, trace_of(root))
    assert len(fake.requests) == len(rows) == 2
    assert len({r["call_id"] for r in rows}) == 1
    failed, succeeded = rows
    assert (failed["cost_usd"], failed["cost_unknown_reason"]) == (None, "usage_missing")
    assert failed["call_key_source"] == "sdk_attempt"
    assert Decimal(str(succeeded["cost_usd"])) == EXPECTED_COST  # counted once, no double


def test_timeout_is_one_row_with_an_error_type(priced_backend, project, telemetry):
    pid, _ = project
    fake = FakeOpenAI([Step(raise_=httpx2.ReadTimeout)])
    with telemetry.tracer.start_as_current_span("POST /v1/answer") as root:
        outcome = adapter(telemetry, fake, max_attempts=3).generate_text(request())
    assert telemetry.force_flush(10_000)
    assert outcome.error_category == "timeout" and len(fake.requests) == 1
    (row,) = priced_backend.query(
        "SELECT status, error_type FROM spans WHERE project_id = %s AND trace_id = %s"
        " AND kind = 'CLIENT'",
        (pid, trace_of(root)),
    )
    assert row == ("ERROR", "timeout")
    (u,) = usage_rows(priced_backend, pid, trace_of(root))
    assert u["cost_status"] == "unknown"


# ---------------------------------------------------------------- runner → database → API
def test_live_runner_trace_reaches_the_database_and_the_read_api(priced_backend, project):
    pid, key = project
    env = live_env()
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": priced_backend.ingest_endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={key}",
            "OTEL_SERVICE_NAME": "live-runner-it",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "100",
            "LLMI_DASHBOARD_PROJECT_ID": str(pid),
        }
    )
    usage = {
        "input_tokens": 40,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 12,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 52,
    }
    fake = FakeOpenAI([response_ok(usage=usage, response_id="resp_SYNTHETIC_it_runner")])
    out = io.StringIO()
    code = live_openai.main(
        ["--execute"],
        env,
        now=NOW,
        client_factory=lambda policy: fake.client(),
        provenance="synthetic",
        out=out,
    )
    assert code == 0
    doc = json.loads(out.getvalue())
    tid = doc["trace_id"]
    assert doc["telemetry"]["flushed"] is True
    assert doc["estimated_cost_usd"] == "0.0000840000"
    assert doc["dashboard_url"].endswith(f"/traces/{tid}?project={pid}")

    spans = priced_backend.query(
        "SELECT name, kind, parent_span_id, span_id FROM spans"
        " WHERE project_id = %s AND trace_id = %s ORDER BY kind",
        (pid, tid),
    )
    assert len(spans) == 2
    by_kind = {s[1]: s for s in spans}
    assert by_kind["INTERNAL"][0] == live_openai.ROOT_SPAN
    assert by_kind["CLIENT"][2] == by_kind["INTERNAL"][3]
    (u,) = usage_rows(priced_backend, pid, tid)
    # the backend's stored cost equals the runner's estimate (same rule, same prices)
    assert Decimal(str(u["cost_usd"])) == Decimal(doc["estimated_cost_usd"])

    detail = priced_backend.api(f"/api/v1/traces/{tid}?project={pid}")
    assert detail["trace"]["trace_id"] == tid and len(detail["spans"]) == 2
    assert [x["cost_usd"] for x in detail["usage"]] == ["0.0000840000"]
    body = json.dumps(detail)
    for secret in (FAKE_API_KEY, key, COMPLETION_SENTINEL, live_openai.INPUT_TEXT):
        assert secret not in body
    log = priced_backend.log_text()
    for secret in (FAKE_API_KEY, COMPLETION_SENTINEL, live_openai.INPUT_TEXT):
        assert secret not in log
