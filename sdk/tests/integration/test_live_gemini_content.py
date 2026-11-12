"""Phase 7C evidence for `demo_app.live_gemini_content` (ADR-39):

    runner → fake Gemini transport → GeminiTextAdapter → SDK spans → official OTLP/HTTP protobuf
           → live `python -m app` → disposable PostgreSQL → project-scoped read API

The Gemini side is OFFLINE: SYNTHETIC fixtures behind `httpx.MockTransport`, `synthetic`
provenance, and every non-loopback connection blocked. Nothing here can reach a provider. The
backend side is real and uses only the session's disposable database (see `conftest.py`) and
projects created by this module under unique names.

A test without the `live_backend` fixture needs no database: those use the local OTLP stub to
see the exporter's real protobuf bytes. Each test keeps five questions apart: did the provider
answer, did local validation pass, was telemetry delivered, is it in PostgreSQL, and can the
read API return it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import logging
import sys
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import llm_insight_sdk as lli
from llm_insight_sdk.telemetry import Telemetry, build_span_exporter
from tests.conftest import DUMMY_KEY, free_port
from tests.gemini_fixtures import (
    ERROR_SENTINEL,
    FAKE_GEMINI_KEY,
    MODEL,
    OUTPUT_SENTINEL,
    THOUGHT_SENTINEL,
    FakeGemini,
    GStep,
    candidate,
    g_fail,
    g_ok,
)
from tests.integration.conftest import LiveBackend
from tests.openai_fixtures import block_network
from tests.unit.test_gemini_live_guard import NOW, gemini_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import content_preview, gemini_scenarios  # noqa: E402
from demo_app import live_gemini_content as runner  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("gemini_released")]  # ADR-50

TICKET = "support-ticket-classification"
WEEKLY = "json-data-interpretation"
SUMMARY = "short-text-summary"
# Pinned independently of the registry.
IDENTITY = {
    TICKET: (
        "18d3061e26e4b09759b469d674c0aba18a87a3610e83534ef679d758c2cbdf0d",
        "ticket-001",
        "demo.ticket-classify",
        512,
    ),
    WEEKLY: (
        "1e9f1ba31ae538115071dc95e5fd81aeef150685ccbc27112d157116105d4951",
        "weekly-usage-001",
        "demo.json-interpret",
        768,
    ),
    SUMMARY: (
        "740179ed99445b9564589b5c07518f4cea890fbb98dfa4ed112a680728be61b5",
        "release-note-001",
        "demo.short-summary",
        512,
    ),
}
# Answers that pass their scenario's rules and still carry the output sentinel, so that a
# passing run proves the answer did not travel anywhere it must not.
ANSWERS = {
    TICKET: json.dumps(
        {"category": "billing", "priority": "high", "reason": f"Charged. {OUTPUT_SENTINEL}"}
    ),
    WEEKLY: json.dumps(
        {
            "total_requests": 700,
            "highest_error_day": "Wed",
            "error_rate_percent": 3.0,
            "observation": f"Peak on Wed. {OUTPUT_SENTINEL}",
        }
    ),
    SUMMARY: f"Large notebook export can time out: {OUTPUT_SENTINEL}",
}
ANSWER_SENTINELS = (OUTPUT_SENTINEL, THOUGHT_SENTINEL)
PROMPT_SENTINELS = tuple(
    text
    for scenario in gemini_scenarios.SCENARIOS.values()
    for text in (scenario.task, scenario.fixture, gemini_scenarios.compose_input(scenario))
) + ("Acme Notes", "T-1001", "SYNTHETIC-weekly-usage")
CREDENTIALS = (FAKE_GEMINI_KEY, DUMMY_KEY)
ALLOWED_SPAN_ATTRIBUTES = {
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.response.id",
    "gen_ai.response.finish_reasons",
    "gen_ai.request.max_tokens",
    "gen_ai.prompt.name",
    "gen_ai.prompt.version",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.reasoning.output_tokens",
    "llminsight.call_id",
    "llminsight.attempt",
    "llminsight.provenance",
    "llminsight.usage_source",
    "server.address",
    "server.port",
    "error.type",
}


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    lli.shutdown(timeout_millis=3000)
    with block_network(attempts):
        yield
    lli.shutdown(timeout_millis=3000)
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@dataclasses.dataclass(frozen=True)
class Project:
    id: int
    key: str = dataclasses.field(repr=False)
    prefix: str


def new_project(live: LiveBackend, label: str) -> Project:
    """A project owned by this test module, under a name no other run can share."""
    key, prefix = live.create_key(f"sdk-content-{label}-{uuid.uuid4().hex[:16]}")
    (row,) = live.query("SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,))
    return Project(int(row[0]), key, prefix)


@pytest.fixture(scope="module")
def project(live_backend: LiveBackend) -> Project:
    return new_project(live_backend, "main")


@pytest.fixture(scope="module")
def foreign(live_backend: LiveBackend) -> Project:
    return new_project(live_backend, "foreign")


def rid() -> str:
    """A unique response ID: the usage ledger's call key is unique per project."""
    return f"SYNTHETICcontent{uuid.uuid4().hex}"


def answer(text: str, *, finish: str = "STOP", response_id: str | None = None) -> GStep:
    body = candidate(finish)
    body["content"] = {
        "parts": [{"text": THOUGHT_SENTINEL, "thought": True}, {"text": text}],
        "role": "model",
    }
    return g_ok(candidates=[body], response_id=response_id or rid())


class Terminal(io.StringIO):
    encoding = "utf-8"

    def isatty(self) -> bool:
        return True


class InterruptingGemini(FakeGemini):
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise KeyboardInterrupt


class NeverCalled:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, policy: Any) -> Any:
        self.calls += 1
        raise AssertionError("no provider client may be created")


def content_env(endpoint: str, key: str, project_id: int | str, **overrides: str | None) -> dict:
    env = gemini_env(LLMI_LIVE_MAX_INPUT_TOKENS="1024", LLMI_LIVE_MAX_OUTPUT_TOKENS="1024")
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={key}",
            "OTEL_SERVICE_NAME": "live-gemini-content-it",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "100",
            "LLMI_DASHBOARD_PROJECT_ID": str(project_id),
        }
    )
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


@dataclasses.dataclass
class Run:
    code: int
    doc: dict[str, Any]
    stdout: str
    stderr: str

    @property
    def trace_id(self) -> str:
        return self.doc["correlation"]["trace_id"]

    @property
    def api_path(self) -> str:
        return self.doc["correlation"]["api_path"]


def invoke(
    env: dict[str, str],
    scenario: str = SUMMARY,
    *,
    execute: bool = True,
    extra: tuple[str, ...] = (),
    factory: Any = None,
    err: io.StringIO | None = None,
    provenance: str = "synthetic",
) -> Run:
    out = io.StringIO()
    stderr = err if err is not None else io.StringIO()
    argv = ["--scenario", scenario, *(("--execute",) if execute else ()), *extra]
    code = runner.main(
        argv, env, now=NOW, client_factory=factory, provenance=provenance, out=out, err=stderr
    )
    text = out.getvalue()
    (line,) = text.splitlines()  # exactly one line, whatever happened
    doc = json.loads(line)
    assert doc["schema"] == runner.SCHEMA
    for forbidden in (*ANSWER_SENTINELS, ERROR_SENTINEL, *PROMPT_SENTINELS, *CREDENTIALS):
        assert forbidden not in text, "stdout must stay metadata-only"
    return Run(code, doc, text, stderr.getvalue())


def run_live(
    live: LiveBackend, owner: Project, fake: FakeGemini, scenario: str = SUMMARY, **kw: Any
) -> Run:
    overrides = kw.pop("overrides", {})
    env = content_env(live.ingest_endpoint, owner.key, owner.id, **overrides)
    kw.setdefault("factory", lambda policy: fake.client())
    return invoke(env, scenario, **kw)


# ---------------------------------------------------------------- read helpers
def counts(live: LiveBackend, pid: int, tid: str) -> tuple[int, int, int]:
    (row,) = live.query(
        "SELECT (SELECT count(*) FROM traces WHERE project_id = %s AND trace_id = %s),"
        " (SELECT count(*) FROM spans WHERE project_id = %s AND trace_id = %s),"
        " (SELECT count(*) FROM usage_records WHERE project_id = %s AND trace_id = %s)",
        (pid, tid) * 3,
    )
    return int(row[0]), int(row[1]), int(row[2])


def project_totals(live: LiveBackend, pid: int) -> tuple[int, int, int]:
    (row,) = live.query(
        "SELECT (SELECT count(*) FROM traces WHERE project_id = %s),"
        " (SELECT count(*) FROM spans WHERE project_id = %s),"
        " (SELECT count(*) FROM usage_records WHERE project_id = %s)",
        (pid, pid, pid),
    )
    return int(row[0]), int(row[1]), int(row[2])


def trace_row(live: LiveBackend, pid: int, tid: str) -> dict:
    (row,) = live.query(
        "SELECT row_to_json(t) FROM traces t WHERE project_id = %s AND trace_id = %s", (pid, tid)
    )
    return row[0]


def span_rows(live: LiveBackend, pid: int, tid: str) -> dict[str, dict]:
    rows = live.query(
        "SELECT row_to_json(s) FROM spans s WHERE project_id = %s AND trace_id = %s", (pid, tid)
    )
    by_kind = {r[0]["kind"]: r[0] for r in rows}
    assert len(by_kind) == len(rows) == 2 and set(by_kind) == {"INTERNAL", "CLIENT"}
    return by_kind


def usage_rows(live: LiveBackend, pid: int, tid: str) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s AND trace_id = %s"
        " ORDER BY attempt_id, span_id",
        (pid, tid),
    )
    return [r[0] for r in rows]


def api_status(live: LiveBackend, path: str) -> tuple[int, bytes]:
    request = urllib.request.Request(  # noqa: S310 - fixed loopback URL
        f"{live.base_url}{path}", headers={"Authorization": f"Bearer {live.admin_token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def stored_text(live: LiveBackend, pid: int) -> str:
    rows = live.query(
        "SELECT row_to_json(t) FROM traces t WHERE project_id = %s"
        " UNION ALL SELECT row_to_json(s) FROM spans s WHERE project_id = %s"
        " UNION ALL SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s",
        (pid, pid, pid),
    )
    return "\n".join(json.dumps(r[0]) for r in rows)


# ---------------------------------------------------------------- A. success, end to end
@pytest.mark.parametrize("scenario", [TICKET, WEEKLY, SUMMARY])
def test_success_is_stored_and_readable_with_identical_identity(live_backend, project, scenario):
    response_id = rid()
    fake = FakeGemini([answer(ANSWERS[scenario], response_id=response_id), g_ok()])
    run = run_live(live_backend, project, fake, scenario)
    sha, fixture_id, prompt_name, cap = IDENTITY[scenario]

    # the provider operation: one physical request, carrying exactly the composed input
    assert len(fake.requests) == 1 and run.code == 0
    sent = fake.request_json(0)
    text = sent["contents"][0]["parts"][0]["text"]
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sha
    assert sent["generationConfig"] == {"maxOutputTokens": cap}
    doc = run.doc
    assert doc["scenario"]["id"] == scenario and doc["scenario"]["version"] == 1
    assert doc["scenario"]["input_sha256"] == sha and doc["scenario"]["fixture_id"] == fixture_id
    assert (doc["scenario"]["prompt_name"], doc["scenario"]["prompt_version"]) == (
        prompt_name,
        "v1",
    )
    assert doc["provider_request_state"] == "sent" and doc["provider"]["attempts"] == 1
    assert doc["provider"]["outcome"] == "completed"
    assert doc["provider"]["provenance"] == "synthetic"
    assert doc["environment"]["sdk_checkout_match"] is True
    # local validation and telemetry delivery are reported separately
    assert doc["validation"]["status"] == "passed"
    assert doc["export"] == {
        "outcome": "confirmed",
        "flushed": True,
        "shutdown_ok": True,
        "spans_ended": 2,
        "spans_exported": 2,
        "spans_export_failed": 0,
    }

    # PostgreSQL, read directly: the stdout line is not treated as proof of storage
    tid = run.trace_id
    assert counts(live_backend, project.id, tid) == (1, 2, 1)
    spans = span_rows(live_backend, project.id, tid)
    root, client = spans["INTERNAL"], spans["CLIENT"]
    assert root["name"] == runner.ROOT_SPAN and root["parent_span_id"] is None
    assert client["name"] == f"generate_content {MODEL}"
    assert client["parent_span_id"] == root["span_id"]
    assert {root["trace_id"], client["trace_id"]} == {tid}
    assert (root["status"], client["status"]) == ("UNSET", "UNSET")
    assert not any(k.startswith("gen_ai.") for k in root["attributes"])  # no parent double count
    # TELEMETRY_CONTRACT §5: only inference spans carry provenance
    assert (root["provenance"], client["provenance"]) == ("unknown", "synthetic")
    assert client["server_dropped_attributes_count"] == 0
    attrs = client["attributes"]
    assert attrs["gen_ai.provider.name"] == "gcp.gemini"
    assert attrs["gen_ai.request.model"] == attrs["gen_ai.response.model"] == MODEL
    assert attrs["gen_ai.response.finish_reasons"] == ["STOP"]
    assert (attrs["gen_ai.prompt.name"], attrs["gen_ai.prompt.version"]) == (prompt_name, "v1")
    assert attrs["gen_ai.response.id"] == response_id
    assert attrs["gen_ai.request.max_tokens"] == cap

    stored_trace = trace_row(live_backend, project.id, tid)
    assert (stored_trace["status"], stored_trace["prompt_version"]) == ("ok", "v1")
    assert (stored_trace["provenance"], stored_trace["span_count"]) == ("synthetic", 2)

    (usage,) = usage_rows(live_backend, project.id, tid)
    assert usage["span_id"] == client["span_id"] and usage["attempt_id"] == 1
    assert (usage["provider"], usage["requested_model"], usage["response_model"]) == (
        "gcp.gemini",
        MODEL,
        MODEL,
    )
    assert (usage["input_tokens"], usage["cached_input_tokens"]) == (20, 4)
    assert (usage["output_tokens"], usage["reasoning_output_tokens"]) == (32, 30)
    assert usage["usage_source"] == "provider_reported" and usage["provenance"] == "synthetic"
    assert usage["canonical_call_key"] == f"resp:gcp.gemini:{response_id}"
    assert (usage["cost_usd"], usage["cost_status"], usage["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert usage["price_version"] is None and usage["model_price_id"] is None

    # stdout agrees with storage, on normalized and on provider-reported counts
    tokens = doc["usage"]["tokens"]
    assert (tokens["input"], tokens["output"]) == (usage["input_tokens"], usage["output_tokens"])
    assert tokens["cache_read_input"] == usage["cached_input_tokens"]
    assert tokens["reasoning_output"] == usage["reasoning_output_tokens"]
    reported = doc["usage"]["provider_usage"]
    assert (reported["prompt"], reported["candidates"], reported["thoughts"]) == (20, 2, 30)
    assert reported["total"] == 52 and doc["usage"]["usage_check"] == "consistent"
    assert doc["usage"]["cost"]["status"] == "unknown"
    assert doc["usage"]["cost"]["billed_amount_usd"] is None

    # the read API, through the exact path the runner printed
    corr = doc["correlation"]
    assert corr["project_id"] == project.id
    assert corr["project_id_source"] == "operator_env_unverified"
    assert corr["api_path"] == f"/api/v1/traces/{tid}?project={project.id}"
    link = urlsplit(corr["dashboard_url"])
    assert (link.scheme, link.netloc, link.path) == ("http", "127.0.0.1:3001", f"/traces/{tid}")
    assert parse_qs(link.query) == {"project": [str(project.id)]}
    detail = live_backend.api(run.api_path)
    assert detail["trace"]["trace_id"] == tid and detail["trace"]["status"] == "ok"
    assert detail["trace"]["provenance"] == "synthetic"
    assert detail["trace"]["provenance_verified"] is False
    summary = detail["trace"]["usage"]
    assert (summary["inference_calls"], summary["input_tokens"], summary["output_tokens"]) == (
        1,
        20,
        32,
    )
    assert summary["known_cost_usd"] is None and summary["unknown_cost_calls"] == 1
    assert summary["models"]["items"] == [
        {"provider": "gcp.gemini", "requested_model": MODEL, "response_model": MODEL, "calls": 1}
    ]
    roles = {s["span_id"]: s for s in detail["spans"]}
    assert roles[root["span_id"]]["role"] == "root"
    assert roles[root["span_id"]]["children"] == [client["span_id"]]
    assert roles[client["span_id"]]["role"] == "child"
    (api_usage,) = detail["usage"]
    assert (api_usage["cost_usd"], api_usage["cost_status"]) == (None, "unknown")
    assert api_usage["input_tokens"] == 20 and api_usage["output_tokens"] == 32

    # durations: stored, served, and consistent with what the runner measured
    client_ns = int(roles[client["span_id"]]["duration_ns"])
    root_ns = int(roles[root["span_id"]]["duration_ns"])
    assert client_ns == client["duration_ns"] and root_ns == root["duration_ns"]
    assert 0 <= client_ns <= root_ns == int(detail["trace"]["duration_ns"])
    assert abs(client_ns / 1e6 - doc["provider"]["duration_ms"]) < 2000


@pytest.mark.parametrize("scenario", [TICKET, WEEKLY, SUMMARY])
def test_success_on_the_wire(stub, scenario):
    """No database: exactly what the official OTLP exporter sent for a successful run."""
    response_id = rid()
    fake = FakeGemini([answer(ANSWERS[scenario], response_id=response_id), g_ok()])
    run = invoke(
        content_env(stub.endpoint, DUMMY_KEY, 7), scenario, factory=lambda p: fake.client()
    )
    _, _, prompt_name, cap = IDENTITY[scenario]
    assert run.code == 0 and len(fake.requests) == 1
    assert run.doc["export"]["outcome"] == "confirmed"
    spans = stub.spans()
    assert len(spans) == 2 and {s["trace_id"] for s in spans} == {run.trace_id}
    (root,) = [s for s in spans if s["kind"] == "INTERNAL"]
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert root["name"] == runner.ROOT_SPAN and root["parent_span_id"] is None
    assert client["name"] == f"generate_content {MODEL}"
    assert client["parent_span_id"] == root["span_id"]
    assert (root["status"], client["status"]) == ("UNSET", "UNSET")
    assert not any(k.startswith("gen_ai.") for k in root["attributes"])
    attrs = client["attributes"]
    assert attrs["gen_ai.provider.name"] == "gcp.gemini"
    assert attrs["gen_ai.operation.name"] == "generate_content"
    assert attrs["gen_ai.request.model"] == attrs["gen_ai.response.model"] == MODEL
    assert attrs["gen_ai.response.finish_reasons"] == ["STOP"]
    assert attrs["gen_ai.response.id"] == response_id
    assert (attrs["gen_ai.prompt.name"], attrs["gen_ai.prompt.version"]) == (prompt_name, "v1")
    assert attrs["gen_ai.request.max_tokens"] == cap
    assert (attrs["gen_ai.usage.input_tokens"], attrs["gen_ai.usage.output_tokens"]) == (20, 32)
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 4
    assert attrs["gen_ai.usage.reasoning.output_tokens"] == 30
    assert attrs["llminsight.usage_source"] == "provider_reported"
    assert (attrs["llminsight.attempt"], attrs["llminsight.provenance"]) == (1, "synthetic")


def test_a_dry_run_changes_nothing_in_storage(live_backend, project):
    before = project_totals(live_backend, project.id)
    never = NeverCalled()
    env = content_env(live_backend.ingest_endpoint, project.key, project.id)
    run = invoke(env, TICKET, execute=False, factory=never)
    assert run.code == 0 and never.calls == 0 and run.doc["execution_allowed"] is True
    assert run.doc["environment"]["backend"] == {"ready": True, "reason": "ready"}
    assert run.doc["correlation"]["trace_id"] is None and run.doc["provider"] is None
    assert project_totals(live_backend, project.id) == before


# ---------------------------------------------------------------- B. idempotency, isolation
def test_reexported_spans_change_nothing(live_backend, project, monkeypatch):
    memory = InMemorySpanExporter()
    real_init = lli.init

    def recording_init(config: lli.LLMInsightConfig) -> Telemetry:
        telemetry = real_init(config)
        telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(memory))
        return telemetry

    monkeypatch.setattr(lli, "init", recording_init)
    run = run_live(live_backend, project, FakeGemini([answer(ANSWERS[SUMMARY])]))
    assert run.code == 0
    tid = run.trace_id

    before = (
        counts(live_backend, project.id, tid),
        usage_rows(live_backend, project.id, tid),
        span_rows(live_backend, project.id, tid),
    )
    assert before[0] == (1, 2, 1)
    finished = memory.get_finished_spans()
    assert len(finished) == 2
    exporter = build_span_exporter(
        lli.LLMInsightConfig(
            endpoint=live_backend.ingest_endpoint,
            project_key=project.key,
            service_name="live-gemini-content-it",
            export_timeout_seconds=3.0,
        )
    )
    try:
        for _ in range(2):  # an exporter or collector resending the same spans
            assert exporter.export(finished) == SpanExportResult.SUCCESS
    finally:
        exporter.shutdown()
    after = (
        counts(live_backend, project.id, tid),
        usage_rows(live_backend, project.id, tid),
        span_rows(live_backend, project.id, tid),
    )
    assert after == before
    (usage,) = after[1]
    assert usage["attempt_id"] == 1  # no duplicate attempt was created


def test_a_foreign_project_cannot_read_the_trace_or_its_usage(live_backend, project, foreign):
    run = run_live(live_backend, project, FakeGemini([answer(ANSWERS[SUMMARY])]))
    tid = run.trace_id
    assert api_status(live_backend, run.api_path)[0] == 200
    missing_id = uuid.uuid4().hex

    denied = api_status(live_backend, f"/api/v1/traces/{tid}?project={foreign.id}")
    absent = api_status(live_backend, f"/api/v1/traces/{missing_id}?project={foreign.id}")
    assert denied[0] == absent[0] == 404 and denied[1] == absent[1]  # indistinguishable
    diagnostics = api_status(live_backend, f"/api/v1/traces/{tid}/diagnostics?project={foreign.id}")
    assert diagnostics[0] == 404
    listing = live_backend.api(f"/api/v1/traces?project={foreign.id}&limit=200")
    assert tid not in {item["trace_id"] for item in listing["items"]}
    assert counts(live_backend, foreign.id, tid) == (0, 0, 0)
    assert counts(live_backend, project.id, tid) == (1, 2, 1)


def test_a_wrong_but_valid_project_id_finds_nothing_and_stores_under_the_key(
    live_backend, project, foreign
):
    """R109: the link uses the operator's unverified ID. A wrong one gives not-found, never
    another project's data, and storage follows the ingest key."""
    env = content_env(live_backend.ingest_endpoint, project.key, foreign.id)
    fake = FakeGemini([answer(ANSWERS[SUMMARY])])
    run = invoke(env, factory=lambda policy: fake.client())
    assert run.code == 0 and run.doc["correlation"]["project_id"] == foreign.id
    assert run.doc["correlation"]["project_id_source"] == "operator_env_unverified"
    assert api_status(live_backend, run.api_path)[0] == 404
    assert counts(live_backend, foreign.id, run.trace_id) == (0, 0, 0)
    assert counts(live_backend, project.id, run.trace_id) == (1, 2, 1)


# ---------------------------------------------------------------- C. failure matrix
@dataclasses.dataclass(frozen=True)
class Case:
    id: str
    step: Callable[[], GStep]
    exit_code: int
    outcome: str
    category: str | None
    validation: str
    span_status: str
    error_type: str | None
    finish: list[str] | None
    trace_status: str
    tokens_known: bool
    cost_reason: str
    scenario: str = SUMMARY


CASES = [
    Case("A-timeout", lambda: GStep(raise_=httpx.ReadTimeout), 1, "error", "timeout",
         "not_evaluated", "ERROR", "timeout", None, "error", False, "usage_missing"),
    Case("B-429", lambda: g_fail(429, "RESOURCE_EXHAUSTED"), 1, "error", "rate_limited",
         "not_evaluated", "ERROR", "429", None, "error", False, "usage_missing"),
    Case("C-blocked-prompt",
         lambda: GStep(200, {"promptFeedback": {"blockReason": "SAFETY"},
                             "usageMetadata": {"promptTokenCount": 9, "totalTokenCount": 9}}),
         1, "blocked", "content_blocked", "not_evaluated", "ERROR", "gcp.gemini.prompt_blocked",
         None, "error", True, "price_missing"),
    Case("C-safety-finish", lambda: g_ok(finish="SAFETY", response_id=rid()), 1, "blocked",
         "content_blocked", "not_evaluated", "UNSET", None, ["SAFETY"], "ok", True,
         "price_missing"),
    Case("D-max-tokens", lambda: answer(ANSWERS[SUMMARY], finish="MAX_TOKENS"), 3, "incomplete",
         None, "not_evaluated", "UNSET", None, ["MAX_TOKENS"], "ok", True, "price_missing"),
    Case("E-usage-missing", lambda: g_ok(usage=None, response_id=rid()), 0, "completed", None,
         "passed", "UNSET", None, ["STOP"], "ok", False, "usage_missing"),
    Case("F-usage-inconsistent",
         lambda: g_ok(usage={"promptTokenCount": 3, "totalTokenCount": 77}, response_id=rid()),
         0, "completed", None, "passed", "UNSET", None, ["STOP"], "ok", False, "usage_missing"),
    Case("G-malformed", lambda: GStep(200, raw=b"<html>SYNTHETIC</html>",
                                      content_type="text/html"),
         1, "error", "malformed_response", "not_evaluated", "ERROR",
         "gcp.gemini.response.malformed", None, "error", False, "usage_missing"),
    Case("H-local-validation", lambda: answer("not json at all"), 3, "completed", None, "failed",
         "UNSET", None, ["STOP"], "ok", True, "price_missing", TICKET),
]  # fmt: skip


def assert_provider_and_validation(run: Run, case: Case, fake: FakeGemini) -> None:
    assert len(fake.requests) == 1, "exactly one physical request; nothing is resent"
    assert run.code == case.exit_code
    assert run.doc["provider_request_state"] == "sent"  # a timeout may have reached the provider
    assert run.doc["provider_request_sent"] is True
    assert run.doc["provider"]["outcome"] == case.outcome
    assert run.doc["provider"]["error_category"] == case.category
    assert run.doc["provider"]["attempts"] == 1
    assert run.doc["validation"]["status"] == case.validation
    assert run.doc["session"]["physical_attempts"] == 1
    assert (run.doc["usage"]["tokens"] is not None) is case.tokens_known
    assert run.doc["usage"]["cost"]["status"] == "unknown"
    assert run.doc["usage"]["cost"]["billed_amount_usd"] is None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_failure_matrix_on_the_wire(stub, case):
    """No database: the spans the official exporter really sent, as decoded protobuf."""
    fake = FakeGemini([case.step(), g_ok(), g_ok()])
    env = content_env(stub.endpoint, DUMMY_KEY, 7)
    run = invoke(env, case.scenario, factory=lambda policy: fake.client())
    assert_provider_and_validation(run, case, fake)
    assert run.doc["export"]["outcome"] == "confirmed"
    spans = stub.spans()
    assert len(spans) == 2 and {s["trace_id"] for s in spans} == {run.trace_id}
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    (root,) = [s for s in spans if s["kind"] == "INTERNAL"]
    assert client["parent_span_id"] == root["span_id"] and root["name"] == runner.ROOT_SPAN
    assert client["status"] == case.span_status
    assert client["attributes"].get("error.type") == case.error_type
    assert client["attributes"].get("gen_ai.response.finish_reasons") == case.finish
    assert client["attributes"]["llminsight.provenance"] == "synthetic"
    assert ("gen_ai.usage.input_tokens" in client["attributes"]) is case.tokens_known


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_failure_matrix_in_postgresql_and_the_read_api(live_backend, project, case):
    fake = FakeGemini([case.step(), g_ok(), g_ok()])
    run = run_live(live_backend, project, fake, case.scenario)
    assert_provider_and_validation(run, case, fake)
    assert run.doc["export"]["outcome"] == "confirmed"
    tid = run.trace_id

    assert counts(live_backend, project.id, tid) == (1, 2, 1)
    client = span_rows(live_backend, project.id, tid)["CLIENT"]
    assert (client["status"], client["error_type"]) == (case.span_status, case.error_type)
    assert client["attributes"].get("gen_ai.response.finish_reasons") == case.finish
    (usage,) = usage_rows(live_backend, project.id, tid)
    assert (usage["input_tokens"] is not None) is case.tokens_known
    assert usage["attempt_id"] == 1
    assert (usage["cost_usd"], usage["cost_status"], usage["cost_unknown_reason"]) == (
        None,
        "unknown",
        case.cost_reason,
    )
    if not case.tokens_known:
        assert usage["usage_source"] == "unknown"

    status, body = api_status(live_backend, run.api_path)
    assert status == 200
    detail = json.loads(body)
    assert detail["trace"]["status"] == case.trace_status
    assert [u["cost_usd"] for u in detail["usage"]] == [None]
    # R108: for these outcomes a dashboard `ok` is not provider completion
    if case.outcome in ("incomplete", "blocked") and case.span_status == "UNSET":
        assert detail["trace"]["status"] == "ok" and run.code != 0


# ---------------------------------------------------------------- I. invalid scenario or fixture
def test_invalid_scenario_and_fixture_store_nothing(live_backend, project, monkeypatch):
    before = project_totals(live_backend, project.id)
    env = content_env(live_backend.ingest_endpoint, project.key, project.id)
    never = NeverCalled()
    run = invoke(env, "no-such-scenario", factory=never)
    assert run.code == 2 and {r["code"] for r in run.doc["refusals"]} == {"scenario_unknown"}

    table = dict(gemini_scenarios.SCENARIOS)
    table[WEEKLY] = dataclasses.replace(
        table[WEEKLY], fixture='{"dataset":"x","days":[],"unit":"r","tools":[{"googleSearch":{}}]}'
    )
    monkeypatch.setattr(gemini_scenarios, "SCENARIOS", table)
    run = invoke(env, WEEKLY, factory=never)
    assert run.code == 2 and "scenario_fixture_invalid" in {r["code"] for r in run.doc["refusals"]}
    assert run.doc["provider_request_state"] == "not_sent" and run.doc["scenario"] is None
    assert never.calls == 0 and project_totals(live_backend, project.id) == before


# ---------------------------------------------------------------- J. export failure
def test_export_failure_is_neither_provider_failure_nor_storage(live_backend):
    owner = new_project(live_backend, "export-failure")
    fake = FakeGemini([answer(ANSWERS[SUMMARY]), g_ok()])

    def revoke_after_readiness(policy: Any) -> Any:
        live_backend.revoke_key(owner.prefix)  # the probe passed; the export is now rejected
        return fake.client()

    run = run_live(live_backend, owner, fake, factory=revoke_after_readiness)
    assert run.code == 3 and len(fake.requests) == 1
    assert run.doc["provider"]["outcome"] == "completed"
    assert run.doc["provider_request_state"] == "sent"
    assert run.doc["validation"]["status"] == "passed"
    assert run.doc["export"]["outcome"] == "failed"
    assert run.doc["export"]["spans_export_failed"] == 2
    assert counts(live_backend, owner.id, run.trace_id) == (0, 0, 0)
    assert api_status(live_backend, run.api_path)[0] == 404  # the printed link finds nothing


# ---------------------------------------------------------------- K. backend unavailable
def test_an_unavailable_backend_refuses_before_any_send():
    fake = FakeGemini([g_ok()])
    env = content_env(f"http://127.0.0.1:{free_port()}/v1/traces", DUMMY_KEY, 7)
    run = invoke(env, factory=lambda policy: fake.client())
    assert run.code == 2 and fake.requests == []
    assert run.doc["provider_request_state"] == "not_sent"
    assert run.doc["policy_eligible"] is True and run.doc["execution_ready"] is False
    assert "backend_not_ready" in {r["code"] for r in run.doc["refusals"]}
    assert run.doc["provider"] is None and run.doc["export"] is None


def test_a_backend_lost_after_readiness_leaves_delivery_unconfirmed(stub):
    fake = FakeGemini([answer(ANSWERS[SUMMARY]), g_ok()])

    def stop_backend(policy: Any) -> Any:
        stub.stop()
        return fake.client()

    env = content_env(
        stub.endpoint,
        DUMMY_KEY,
        7,
        LLMI_SDK_EXPORT_TIMEOUT_SECONDS="1",
        LLMI_SDK_SHUTDOWN_TIMEOUT_MILLIS="2000",
    )
    run = invoke(env, factory=stop_backend)
    assert run.code == 3 and len(fake.requests) == 1
    assert run.doc["provider"]["outcome"] == "completed"
    assert run.doc["export"]["outcome"] in ("failed", "unknown")
    assert run.doc["export"]["spans_exported"] == 0


# ---------------------------------------------------------------- L. flush or shutdown uncertainty
@pytest.mark.parametrize("method", ["force_flush", "shutdown"])
def test_delivery_uncertainty_is_reported_even_when_storage_succeeded(
    live_backend, project, monkeypatch, method
):
    """Export confirmation and storage are different facts, in both directions."""
    real = getattr(Telemetry, method)

    def uncertain(self: Telemetry, timeout_millis: int | None = None) -> bool:
        real(self, timeout_millis)
        return False

    monkeypatch.setattr(Telemetry, method, uncertain)
    run = run_live(live_backend, project, FakeGemini([answer(ANSWERS[SUMMARY])]))
    assert run.code == 3 and run.doc["provider"]["outcome"] == "completed"
    assert run.doc["export"]["outcome"] == "unknown"
    assert counts(live_backend, project.id, run.trace_id) == (1, 2, 1)
    assert api_status(live_backend, run.api_path)[0] == 200


# ---------------------------------------------------------------- M. interruption
def test_an_interrupted_call_on_the_wire(stub):
    """No database: the interrupted attempt is exported, typed, and carries no content."""
    fake = InterruptingGemini([g_ok(), g_ok()])
    run = invoke(content_env(stub.endpoint, DUMMY_KEY, 7), factory=lambda p: fake.client())
    assert run.code == 1 and len(fake.requests) == 1
    assert run.doc["provider_request_state"] == "possibly_sent"
    assert run.doc["provider_request_sent"] is None
    assert run.doc["export"]["outcome"] == "confirmed"
    (client,) = [s for s in stub.spans() if s["kind"] == "CLIENT"]
    assert (client["status"], client["status_message"]) == ("ERROR", "")
    assert client["attributes"]["error.type"] == "KeyboardInterrupt"
    for received in stub.received:
        assert_clean(received.body.decode("latin-1"))


def test_an_interrupted_call_never_claims_not_sent(live_backend, project):
    fake = InterruptingGemini([g_ok(), g_ok()])
    run = run_live(live_backend, project, fake)
    assert run.code == 1 and len(fake.requests) == 1
    assert run.doc["provider_request_state"] == "possibly_sent"
    assert run.doc["provider_request_sent"] is None
    assert run.doc["provider"]["outcome"] == "interrupted"
    assert run.doc["validation"]["status"] == "not_evaluated"
    assert run.doc["usage"]["tokens"] is None
    assert run.doc["session"]["physical_attempts"] == 1
    assert run.doc["export"]["outcome"] == "confirmed"

    tid = run.trace_id
    assert counts(live_backend, project.id, tid) == (1, 2, 1)
    client = span_rows(live_backend, project.id, tid)["CLIENT"]
    assert (client["status"], client["error_type"]) == ("ERROR", "KeyboardInterrupt")
    assert client["status_message"] is None
    (usage,) = usage_rows(live_backend, project.id, tid)
    assert (usage["usage_source"], usage["cost_unknown_reason"]) == ("unknown", "usage_missing")
    assert json.loads(api_status(live_backend, run.api_path)[1])["trace"]["status"] == "error"


def test_an_interrupt_before_the_send_reports_not_sent_and_stores_nothing(live_backend, project):
    before = project_totals(live_backend, project.id)

    def interrupted(policy: Any) -> Any:
        raise KeyboardInterrupt

    run = run_live(live_backend, project, FakeGemini([]), factory=interrupted)
    assert run.code == 2 and run.doc["provider_request_state"] == "not_sent"
    assert {r["code"] for r in run.doc["refusals"]} == {"interrupted_before_send"}
    assert run.doc["correlation"]["trace_id"] is None
    assert project_totals(live_backend, project.id) == before


# ---------------------------------------------------------------- N. invalid project context
@pytest.mark.parametrize("value", [None, "", "0", "-1", "abc", "1.5", "9223372036854775808"])
def test_an_invalid_project_id_refuses_before_any_send(stub, value):
    fake = FakeGemini([g_ok()])
    env = content_env(stub.endpoint, DUMMY_KEY, 7, LLMI_DASHBOARD_PROJECT_ID=value)
    run = invoke(env, factory=lambda policy: fake.client())
    assert run.code == 2 and fake.requests == [] and stub.spans() == []
    assert "dashboard_project_invalid" in {r["code"] for r in run.doc["refusals"]}
    corr = run.doc["correlation"]
    assert (corr["project_id"], corr["dashboard_url"], corr["api_path"]) == (None, None, None)


# ---------------------------------------------------------------- O. missing trace correlation
def test_a_refused_run_has_no_trace_and_no_links(stub):
    never = NeverCalled()
    run = invoke(content_env(stub.endpoint, DUMMY_KEY, 7, LLMI_LIVE_CONSENT=None), factory=never)
    assert run.code == 2 and never.calls == 0
    corr = run.doc["correlation"]
    assert (corr["trace_id"], corr["dashboard_url"], corr["api_path"]) == (None, None, None)
    assert corr["project_id"] == 7  # valid but unused: no trace is claimed


def test_the_read_api_refuses_incomplete_correlation(live_backend, project):
    run = run_live(live_backend, project, FakeGemini([answer(ANSWERS[SUMMARY])]))
    tid = run.trace_id
    assert api_status(live_backend, f"/api/v1/traces/{tid}")[0] == 422  # the smoke runner's path
    assert api_status(live_backend, f"/api/v1/traces/{tid}?project=abc")[0] == 422
    assert api_status(live_backend, f"/api/v1/traces/not-a-trace?project={project.id}")[0] == 422
    unknown = api_status(live_backend, f"/api/v1/traces/{uuid.uuid4().hex}?project={project.id}")
    assert unknown[0] == 404
    assert api_status(live_backend, run.api_path)[0] == 200


# ---------------------------------------------------------------- privacy
def assert_clean(text: str, *extra: str) -> None:
    for forbidden in (*ANSWER_SENTINELS, ERROR_SENTINEL, *PROMPT_SENTINELS, *CREDENTIALS, *extra):
        assert forbidden not in text


PRIVACY_STEPS = [
    pytest.param(lambda: answer(ANSWERS[SUMMARY]), id="completed"),
    pytest.param(lambda: answer(ANSWERS[SUMMARY], finish="MAX_TOKENS"), id="incomplete"),
    pytest.param(lambda: g_ok(finish="SAFETY", response_id=rid()), id="safety"),
    pytest.param(lambda: g_fail(429, "RESOURCE_EXHAUSTED"), id="rate-limited"),
    pytest.param(lambda: g_fail(500, "INTERNAL"), id="server-error"),
    pytest.param(lambda: GStep(raise_=httpx.ReadTimeout), id="timeout"),
]


@pytest.mark.parametrize("step", PRIVACY_STEPS)
def test_preview_off_is_metadata_only_on_the_wire_and_in_logs(stub, step, caplog, capfd):
    """Prompt, answer and error sentinels, and both credentials, against the protobuf bytes
    the official exporter really sent, every log record, and both process streams."""
    fake = FakeGemini([step()])
    with caplog.at_level(logging.DEBUG):  # the root logger: the SDK and every library it calls
        run = invoke(content_env(stub.endpoint, DUMMY_KEY, 7), factory=lambda p: fake.client())
    assert run.stderr == "" and run.doc["preview"]["requested"] is False
    assert_clean(run.stdout)
    assert_clean(caplog.text)
    captured = capfd.readouterr()
    assert_clean(captured.out + captured.err)

    assert stub.received, "the exporter must have sent something to inspect"
    for received in stub.received:
        body = received.body.decode("latin-1")
        for forbidden in (*ANSWER_SENTINELS, ERROR_SENTINEL, *PROMPT_SENTINELS, *CREDENTIALS):
            assert forbidden not in body
        assert received.headers["x-project-key"] == DUMMY_KEY  # only ever a header
    for span in stub.spans():
        assert set(span["attributes"]) <= ALLOWED_SPAN_ATTRIBUTES
        assert span["status_message"] == ""


def test_preview_off_leaves_no_content_in_storage_the_api_or_the_backend_log(live_backend):
    owner = new_project(live_backend, "privacy")
    runs = [
        run_live(live_backend, owner, FakeGemini([answer(ANSWERS[scenario])]), scenario)
        for scenario in (TICKET, WEEKLY, SUMMARY)
    ]
    runs.append(run_live(live_backend, owner, FakeGemini([g_fail(429, "RESOURCE_EXHAUSTED")])))
    runs.append(run_live(live_backend, owner, InterruptingGemini([g_ok()])))
    assert all(r.stderr == "" and r.doc["preview"]["shown"] is False for r in runs)

    extra = (owner.key, owner.key.split("_", 2)[2], live_backend.admin_token)
    assert_clean(stored_text(live_backend, owner.id), *extra)
    served = [
        live_backend.api(f"/api/v1/traces?project={owner.id}&limit=200"),
        *(live_backend.api(run.api_path) for run in runs),
    ]
    assert_clean(json.dumps(served), *extra)
    log = live_backend.log_text()
    assert_clean(log, *extra)
    assert live_backend.log_tracebacks() == []
    (real,) = live_backend.query(
        "SELECT count(*) FROM traces WHERE project_id = %s AND provenance = 'real'", (owner.id,)
    )
    assert real == (0,)  # fake transports never produce a `real` label


def preview_run(endpoint: str, key: str, pid: int | str, step: GStep, *, scenario: str = SUMMARY):
    fake = FakeGemini([step, g_ok()])
    err = Terminal()
    run = invoke(
        content_env(endpoint, key, pid),
        scenario,
        extra=("--preview",),
        err=err,
        factory=lambda policy: fake.client(),
    )
    assert len(fake.requests) == 1
    return run


def test_preview_on_shows_only_the_answer_and_never_reaches_telemetry(live_backend):
    owner = new_project(live_backend, "preview")
    hostile = f"{OUTPUT_SENTINEL}\x1b[2J\x1b]0;x\x07\r‮ export"
    run = preview_run(live_backend.ingest_endpoint, owner.key, owner.id, answer(hostile))

    assert OUTPUT_SENTINEL in run.stderr and "SYNTHETIC LLM Insight demo data" in run.stderr
    assert THOUGHT_SENTINEL not in run.stderr and FAKE_GEMINI_KEY not in run.stderr
    assert owner.key not in run.stderr and "x-goog-api-key" not in run.stderr.lower()
    for raw in ("\x1b", "\x07", "\r", "‮"):
        assert raw not in run.stderr
    assert "\\x1b[2J" in run.stderr
    assert all(line.startswith("preview| ") for line in run.stderr.splitlines())
    assert run.doc["preview"]["shown"] is True
    assert run.doc["preview"]["thought_parts_excluded"] == 1
    assert_clean(run.stdout)

    extra = (owner.key, owner.key.split("_", 2)[2])
    assert_clean(stored_text(live_backend, owner.id), *extra)
    assert_clean(json.dumps(live_backend.api(run.api_path)), *extra)
    assert_clean(live_backend.log_text(), *extra)


def test_preview_on_is_bounded_and_never_shows_an_error_body(stub):
    run = preview_run(stub.endpoint, DUMMY_KEY, 7, answer("z" * 9000))
    body = [line for line in run.stderr.splitlines() if "zzz" in line]
    assert sum(len(line) - len("preview| ") for line in body) == 2000
    assert run.doc["preview"]["truncated"] is True
    assert content_preview.TRUNCATION_MARKER in run.stderr

    lli.shutdown(timeout_millis=3000)
    failed = preview_run(stub.endpoint, DUMMY_KEY, 7, g_fail(429, "RESOURCE_EXHAUSTED"))
    assert failed.code == 1 and failed.stderr == "" and failed.doc["preview"]["shown"] is False
    assert ERROR_SENTINEL not in failed.stdout + failed.stderr


@pytest.mark.parametrize(
    ("overrides", "stream", "reason"),
    [
        ({}, io.StringIO, "stderr_not_a_terminal"),
        ({"CI": "true"}, Terminal, "ci_environment"),
        ({"GITHUB_ACTIONS": "true"}, Terminal, "ci_environment"),
    ],
)
def test_preview_is_refused_before_any_send_when_unsafe(stub, overrides, stream, reason):
    never = NeverCalled()
    err = stream()
    run = invoke(
        content_env(stub.endpoint, DUMMY_KEY, 7, **overrides),
        extra=("--preview",),
        err=err,
        factory=never,
    )
    assert run.code == 2 and never.calls == 0 and run.stderr == "" and stub.spans() == []
    assert "preview_not_safe" in {r["code"] for r in run.doc["refusals"]}
    assert run.doc["preview"]["reason"] == reason


# ---------------------------------------------------------------- real-mode boundary
def test_real_provenance_still_refuses_the_fake_client(stub):
    """The offline harness does not weaken ADR-34: a fake transport is `synthetic` or nothing."""
    fake = FakeGemini([g_ok()])
    run = invoke(
        content_env(stub.endpoint, DUMMY_KEY, 7),
        factory=lambda policy: fake.client(),
        provenance="real",
    )
    assert run.code == 2 and fake.requests == [] and stub.spans() == []
    assert {r["code"] for r in run.doc["refusals"]} == {"client_not_allowed"}
    assert run.doc["provider_request_state"] == "not_sent"
