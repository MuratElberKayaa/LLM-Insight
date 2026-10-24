"""OpenAI adapter → official OTLP/HTTP protobuf exporter → live `python -m app` → PostgreSQL.

The OpenAI side is OFFLINE: SYNTHETIC response fixtures behind an `httpx2.MockTransport`,
with every non-loopback connection blocked, so no request can reach OpenAI and nothing is
spent. The backend side is real: the verified price catalogue is seeded with
`python -m app.cli seed-prices`, and costs are read back from the database and the API.

Three different quantities are checked separately:
- physical requests seen by the transport (what would have been billed),
- provider-reported token usage (copied from the fixture's `usage` object, never estimated),
- the computed cost, an estimate from those tokens and the stored price row.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import llm_insight_sdk as lli
from llm_insight_sdk.openai_adapter import ObservedOpenAIClient
from llm_insight_sdk.telemetry import build_span_exporter
from tests.conftest import PROMPT_SENTINEL, SYSTEM_SENTINEL, messages
from tests.integration.conftest import LiveBackend, _run_backend
from tests.openai_fixtures import (
    COMPLETION_SENTINEL,
    ERROR_SENTINEL,
    FAKE_API_KEY,
    REQUEST_ID_SENTINEL,
    FakeOpenAI,
    block_network,
    fail,
    ok,
)

pytestmark = pytest.mark.integration

MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
# docs/PRICING.md §4: (200 * 0.75 + 1000 * 0.075 + 300 * 4.50) / 1e6
EXPECTED_COST = Decimal("0.0015750000")


@pytest.fixture(scope="module")
def priced_backend(live_backend: LiveBackend) -> LiveBackend:
    """The live backend with the verified catalogue seeded (idempotent CLI)."""
    out = _run_backend(live_backend.python, live_backend.env, ["-m", "app.cli", "seed-prices"])
    assert "(catalogue openai-2026-10-24)" in out
    return live_backend


@pytest.fixture(scope="module")
def project(priced_backend: LiveBackend) -> tuple[int, str]:
    """A separate project, so these priced rows never mix with the mock-only tests."""
    key, prefix = priced_backend.create_key("sdk-openai-it")
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


def config(live: LiveBackend, key: str) -> lli.LLMInsightConfig:
    return lli.LLMInsightConfig(
        endpoint=live.ingest_endpoint,
        project_key=key,
        service_name="sdk-openai-live-it",
        service_version="0.2.0",
        deployment_environment="test",
        schedule_delay_millis=100,
        export_timeout_seconds=3.0,
    )


@pytest.fixture
def telemetry(priced_backend, project) -> Iterator[lli.Telemetry]:
    t = lli.init(config(priced_backend, project[1]))
    yield t
    t.shutdown()


def observed(telemetry, fake, **kw) -> ObservedOpenAIClient:
    kw.setdefault("backoff_seconds", 0.0)
    return ObservedOpenAIClient(telemetry, fake.client(), provenance="synthetic", **kw)


def chat(llm, **kw):
    kw.setdefault("model", MODEL)
    kw.setdefault("messages", messages())
    kw.setdefault("service_tier", "default")
    return llm.create_chat_completion(**kw)


def usage_rows(live: LiveBackend, project_id: int, trace_id: str) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s"
        " ORDER BY attempt_id, span_id",
        (project_id, trace_id),
    )
    return [r[0] for r in rows]


def price_id(live: LiveBackend, model: str) -> int:
    (row,) = live.query(
        "SELECT id FROM model_prices WHERE provider = 'openai' AND model = %s"
        " AND version = 'openai-2026-10-24'",
        (model,),
    )
    return int(row[0])


def trace_of(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


# ---------------------------------------------------------------- cost end to end
def test_offline_openai_call_is_stored_with_the_verified_cost(priced_backend, project, telemetry):
    pid, _ = project
    fake = FakeOpenAI([ok()])
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        chat(observed(telemetry, fake))
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)

    (u,) = usage_rows(priced_backend, pid, tid)
    assert len(fake.requests) == 1  # one physical request ...
    assert u["attempt_id"] == 1  # ... one ledger row
    # provider-reported usage, exactly as in the fixture's usage object
    assert (u["input_tokens"], u["cached_input_tokens"]) == (1200, 1000)
    assert (u["output_tokens"], u["reasoning_output_tokens"]) == (300, 200)
    assert u["cache_write_input_tokens"] is None  # not reported, so not invented
    assert u["usage_source"] == "provider_reported"
    # the cost estimate, from those tokens and the exact stored price row
    assert Decimal(str(u["cost_usd"])) == EXPECTED_COST
    assert (u["cost_status"], u["cost_unknown_reason"]) == ("computed", None)
    assert u["price_version"] == "openai-2026-10-24"
    assert u["model_price_id"] == price_id(priced_backend, SNAPSHOT)
    assert (u["provider"], u["requested_model"], u["response_model"]) == ("openai", MODEL, SNAPSHOT)
    assert u["provenance"] == "synthetic" and u["call_key_source"] == "provider_response_id"

    (span,) = priced_backend.query(
        "SELECT attributes, server_dropped_attributes_count, kind, parent_span_id FROM spans"
        " WHERE project_id = %s AND trace_id = %s AND kind = 'CLIENT'",
        (pid, tid),
    )
    attrs, dropped, _, parent = span
    assert dropped == 0  # every attribute the adapter emits is allowlisted
    assert parent == format(root.get_span_context().span_id, "016x")
    assert attrs["openai.response.service_tier"] == "default"
    assert attrs["server.address"] == "api.openai.com"
    assert attrs["openai.api.type"] == "chat_completions"


def test_read_api_shows_usage_and_cost(priced_backend, project, telemetry):
    pid, _ = project
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        chat(observed(telemetry, FakeOpenAI([ok()])))
        chat(observed(telemetry, FakeOpenAI([ok(model="gpt-5.4")])))  # not catalogued
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)
    detail = priced_backend.api(f"/api/v1/traces/{tid}?project={pid}")
    costs = sorted((u["cost_usd"] or "null", u["cost_unknown_reason"]) for u in detail["usage"])
    assert costs == [("0.0015750000", None), ("null", "price_missing")]
    assert all(u["provenance"] == "synthetic" for u in detail["usage"])
    items = priced_backend.api(f"/api/v1/traces?project={pid}")["items"]
    (item,) = [i for i in items if i["trace_id"] == tid]
    assert item["usage"]["known_cost_usd"] == "0.0015750000"
    assert item["usage"]["unknown_cost_calls"] == 1
    assert item["provenance"] == "synthetic" and item["provenance_verified"] is False


# ---------------------------------------------------------------- retries
def test_each_physical_attempt_is_one_ledger_row(priced_backend, project, telemetry):
    pid, _ = project
    fake = FakeOpenAI([fail(429, "rate_limit_error"), fail(503), ok()])
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        chat(observed(telemetry, fake, max_attempts=3))
    assert telemetry.force_flush(10_000)
    rows = usage_rows(priced_backend, pid, trace_of(root))

    assert len(fake.requests) == len(rows) == 3  # transport requests == attempts == rows
    assert len({r["call_id"] for r in rows}) == 1  # one logical call (M03 = 1, M02 = 3)
    assert [r["attempt_id"] for r in rows] == [1, 2, 3]
    for failed in rows[:2]:
        assert failed["input_tokens"] is None and failed["usage_source"] == "unknown"
        assert (failed["cost_usd"], failed["cost_unknown_reason"]) == (None, "usage_missing")
        assert failed["call_key_source"] == "sdk_attempt"  # no response, no response ID
    assert Decimal(str(rows[2]["cost_usd"])) == EXPECTED_COST
    (errors,) = priced_backend.query(
        # by attempt, not start_ns: zero-latency attempts can share a Windows clock tick
        "SELECT array_agg(error_type ORDER BY (attributes->>'llminsight.attempt')::int) FROM spans"
        " WHERE project_id = %s AND trace_id = %s AND kind = 'CLIENT' AND status = 'ERROR'",
        (pid, trace_of(root)),
    )
    assert errors[0] == ["429", "503"]


# ---------------------------------------------------------------- re-export idempotency
def test_duplicate_exports_never_change_the_cost(priced_backend, project, telemetry):
    pid, key = project
    memory = InMemorySpanExporter()
    telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(memory))
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        chat(observed(telemetry, FakeOpenAI([fail(429), ok()]), max_attempts=2))
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)
    before = usage_rows(priced_backend, pid, tid)
    assert len(before) == 2

    exporter = build_span_exporter(config(priced_backend, key))
    try:
        for _ in range(2):  # an exporter or collector resending the same spans
            assert exporter.export(memory.get_finished_spans()) == SpanExportResult.SUCCESS
    finally:
        exporter.shutdown()
    assert usage_rows(priced_backend, pid, tid) == before  # same rows, ids, costs, created_at


# ---------------------------------------------------------------- price_missing paths
@pytest.mark.parametrize(
    "fixture,request_tier",
    [
        ({"model": "gpt-5.4"}, "default"),  # long-context model, deliberately not catalogued
        ({"model": "gpt-5.4-mini-2099-01-01"}, "default"),  # snapshot without evidence
        ({"service_tier": "flex"}, "flex"),
        ({"service_tier": "priority"}, "fast"),  # Fast mode answers `priority`
        ({"service_tier": None}, None),  # tier not reported: standard price not provable
    ],
)
def test_unpriceable_calls_stay_unknown(priced_backend, project, telemetry, fixture, request_tier):
    pid, _ = project
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        chat(observed(telemetry, FakeOpenAI([ok(**fixture)])), service_tier=request_tier)
    assert telemetry.force_flush(10_000)
    (u,) = usage_rows(priced_backend, pid, trace_of(root))
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert u["input_tokens"] == 1200  # usage is still recorded; only the price is unknown


def test_missing_usage_is_usage_missing(priced_backend, project, telemetry):
    pid, _ = project
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        chat(observed(telemetry, FakeOpenAI([ok(usage=None)])))
    assert telemetry.force_flush(10_000)
    (u,) = usage_rows(priced_backend, pid, trace_of(root))
    assert (u["input_tokens"], u["output_tokens"], u["usage_source"]) == (None, None, "unknown")
    assert (u["cost_usd"], u["cost_unknown_reason"]) == (None, "usage_missing")


# ---------------------------------------------------------------- no leaks
def test_no_key_content_or_provider_error_text_reaches_db_api_or_logs(
    priced_backend, project, telemetry
):
    pid, key = project
    fake = FakeOpenAI([fail(429), ok(), fail(400, "invalid_request_error")])
    with telemetry.tracer.start_as_current_span("POST /v1/summarize") as root:
        llm = observed(telemetry, fake, max_attempts=2)
        chat(llm)
        with pytest.raises(Exception, match="Error code: 400"):
            chat(llm, max_attempts=1)
    assert telemetry.force_flush(10_000)
    tid = trace_of(root)
    dump = "\n".join(
        json.dumps(r[0])
        for table in ("traces", "spans", "usage_records")
        for r in priced_backend.query(
            f"SELECT row_to_json(x) FROM {table} x WHERE project_id = %s",  # noqa: S608
            (pid,),
        )
    )
    api = json.dumps(priced_backend.api(f"/api/v1/traces/{tid}?project={pid}"))
    log = priced_backend.log_text()
    assert priced_backend.log_tracebacks() == []  # shows the exception block if one occurs
    for text in (dump, api, log):
        for forbidden in (
            FAKE_API_KEY,
            key,
            PROMPT_SENTINEL,
            SYSTEM_SENTINEL,
            COMPLETION_SENTINEL,
            ERROR_SENTINEL,
            REQUEST_ID_SENTINEL,
            "Traceback",
        ):
            assert forbidden not in text
