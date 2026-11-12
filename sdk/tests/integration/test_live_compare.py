"""Phase 8A-5 evidence for `demo_app.live_compare` (ADR-45):

    runner → Free Plan guard → audited httpx client → SYNTHETIC wire → GroqTextAdapter → SDK spans
           → official OTLP/HTTP protobuf → live `python -m app` → disposable PostgreSQL
           → project-scoped Overview, Traces and Models / Compare

The provider side is OFFLINE. Only `httpx.HTTPTransport.handle_request` is replaced (`wire`), so
the runner's real client, endpoint boundary and send gate run, and every non-loopback connection is
blocked. The execution lock and the Free Plan evidence are lifted **inside this test process only**
(`released`), exactly as in the guard's unit tests; the shipped module keeps both locked, which
`test_as_shipped_*` checks against the live backend. Because the runner has only the real path, its
spans carry `llminsight.provenance = real`: that label is asserted by the runner, never verified
(`provenance_verified` is false), and every row here is SYNTHETIC, lives in a disposable database
and is dropped with it. None of this is evidence about a real Groq response (R115).

The runner allows one request per process (ADR-44). Each test case stands for one process
lifetime (`own_slot`) and runs the runner at most once to a send, except the negative tests, which
show that a second execution in the same lifetime is refused. The three-scenario Overview and
Compare set is verified with SYNTHETIC adapter spans that never pass through the runner
(`test_a_synthetic_comparison_set_*`), labelled `synthetic`, separate from the runner executions.

Tests without `live_backend` need no database and use the local OTLP stub.
"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import llm_insight_sdk as lli
from llm_insight_sdk import groq_adapter, groq_live
from llm_insight_sdk.groq_live import FreePlanModel
from llm_insight_sdk.providers import TextRequest
from llm_insight_sdk.telemetry import Telemetry, build_span_exporter
from tests.conftest import DUMMY_KEY
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
from tests.unit.test_groq_live_guard import ENV as GUARD_ENV
from tests.unit.test_groq_live_guard import NOW

REPO = Path(__file__).resolve().parents[3]
import sys  # noqa: E402

sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import live_compare as runner  # noqa: E402

pytestmark = pytest.mark.integration

TICKET, USAGE, SUMMARY = "cmp-ticket-triage", "cmp-json-aggregate", "cmp-release-summary"
SCENARIO_IDS = (TICKET, USAGE, SUMMARY)
ROOT_SPAN = "demo.live-compare"  # pinned independently of the runner: what the dashboard reads
# Pinned independently of the registry: (prompt_name, prompt_version, input SHA-256).
IDENTITY = {
    TICKET: (
        "cmp.ticket-triage",
        "cmp-v1.ticket-triage",
        "5d9040f3a67d37e5edaf4dab90a9a59ff236061c729c7b62dcd0a39812c267ff",
    ),
    USAGE: (
        "cmp.json-aggregate",
        "cmp-v1.json-aggregate",
        "89684d9402c71ad6681fbd77ad6199473612a5859da9580fa18dd537bcb31a37",
    ),
    SUMMARY: (
        "cmp.release-summary",
        "cmp-v1.release-summary",
        "815eef603170295620d27a787ff3e8345384cc43e56b016bb4d6976ee135ea47",
    ),
}
# Answers that pass their scenario's rules and carry the output sentinel, so a passing run also
# proves the answer travelled nowhere it must not.
ANSWERS = {
    TICKET: json.dumps(
        {"category": "login", "priority": "high", "reason": f"Sign-in fails. {OUTPUT_SENTINEL}"}
    ),
    USAGE: json.dumps(
        {
            "total_requests": 1000,
            "highest_error_day": "Wed",
            "error_rate_percent": 3.0,
            "observation": f"Wednesday peaked. {OUTPUT_SENTINEL}",
        }
    ),
    SUMMARY: f"Boards load faster with {OUTPUT_SENTINEL}. Known issue: large imports can stall.",
}
PROMPT_TEXTS = tuple(
    text for s in cs.SCENARIOS.values() for text in (s.task, s.fixture, cs.compose_input(s))
) + ("Lumen Tasks", "T-2044", "SYNTHETIC-comparison-usage")
CONTENT_SENTINELS = (OUTPUT_SENTINEL, REASONING_SENTINEL, ERROR_SENTINEL, GENERATION_SENTINEL)
CREDENTIALS = (FAKE_GROQ_KEY, DUMMY_KEY)
CLIENT_SPAN_KEYS = {
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
    "llminsight.call_id",
    "llminsight.attempt",
    "llminsight.provenance",
    "llminsight.usage_source",
    "server.address",
    "server.port",
}


# ---------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    lli.shutdown(timeout_millis=3000)
    with block_network(attempts):
        yield
    lli.shutdown(timeout_millis=3000)
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture(autouse=True)
def own_slot(monkeypatch) -> None:
    """Each test case stands for one process lifetime: it gets one fresh slot, which the runner
    takes once and never releases, so no test can make a second request
    (`test_a_second_execution_in_the_same_process_is_refused_*`). The shipped slot is never
    touched, and the slot is never replaced inside a test."""
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


@pytest.fixture
def released(monkeypatch) -> None:
    """Test-only release of both locks, in this process only (as the guard's unit tests do)."""
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        groq_live,
        "FREE_PLAN_MODELS",
        {MODEL: FreePlanModel(MODEL, ("https://example.invalid/synthetic",))},
    )


@pytest.fixture
def wire(monkeypatch) -> FakeGroq:
    fake = FakeGroq([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


@dataclass(frozen=True)
class Project:
    id: int
    key: str = field(repr=False)
    prefix: str


def new_project(live: LiveBackend, label: str) -> Project:
    """A SYNTHETIC project owned by this module, under a name no other run can share."""
    key, prefix = live.create_key(f"sdk-compare-it-{label}-{uuid.uuid4().hex[:16]}")
    (row,) = live.query("SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,))
    return Project(int(row[0]), key, prefix)


@pytest.fixture(scope="module")
def priced(groq_price: LiveBackend) -> LiveBackend:
    """The session's one canonical catalogue row for exactly `groq` and the model
    (`conftest.groq_price`): it must never price a call (ADR-40)."""
    return groq_price


@pytest.fixture(scope="module")
def project(priced: LiveBackend) -> Project:
    return new_project(priced, "main")


@pytest.fixture(scope="module")
def foreign(priced: LiveBackend) -> Project:
    return new_project(priced, "foreign")


def rid() -> str:
    """A unique SYNTHETIC response ID: the usage ledger's call key is unique per project."""
    return f"chatcmpl-SYNTHETIC-compare-{uuid.uuid4().hex}"


def compare_env(endpoint: str, key: str, project_id: int | str, **changes: str | None) -> dict:
    env = dict(GUARD_ENV)
    env.update(
        {
            "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={key}",
            "OTEL_SERVICE_NAME": "live-compare-it-SYNTHETIC",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "100",
            "LLMI_DASHBOARD_PROJECT_ID": str(project_id),
        }
    )
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


@dataclass
class Run:
    code: int
    doc: dict[str, Any]
    stdout: str

    @property
    def trace_id(self) -> str:
        return self.doc["correlation"]["trace_id"]

    @property
    def api_path(self) -> str:
        return self.doc["correlation"]["api_path"]


def invoke(env: Mapping[str, str], scenario: str = SUMMARY, *, execute: bool = True) -> Run:
    out = io.StringIO()
    argv = ["--provider", "groq", "--scenario", scenario, *(("--execute",) if execute else ())]
    code = runner.main(argv, env, now=NOW, out=out)
    text = out.getvalue()
    (line,) = text.splitlines()  # exactly one line, whatever happened
    doc = json.loads(line)
    assert doc["schema"] == runner.SCHEMA
    assert_clean(text)
    return Run(code, doc, text)


def run_live(live: LiveBackend, owner: Project, wire: FakeGroq, steps: list[Step], scenario: str):
    wire.steps = list(steps)
    return invoke(compare_env(live.ingest_endpoint, owner.key, owner.id), scenario)


# ---------------------------------------------------------------- read helpers
def window(started: datetime) -> dict[str, str]:
    return {
        "from": (started - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


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


def trace_row(live: LiveBackend, pid: int, tid: str) -> dict:
    (row,) = live.query(
        "SELECT row_to_json(t) FROM traces t WHERE project_id = %s AND trace_id = %s", (pid, tid)
    )
    return row[0]


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


def assert_clean(text: str, *extra: str) -> None:
    for forbidden in (*CONTENT_SENTINELS, *PROMPT_TEXTS, *CREDENTIALS, *extra):
        assert forbidden not in text


# ---------------------------------------------------------------- as shipped: nothing is stored
def test_as_shipped_an_execution_is_refused_and_stores_nothing(live_backend, wire):
    owner = new_project(live_backend, "shipped")
    before = project_totals(live_backend, owner.id)
    run = invoke(compare_env(live_backend.ingest_endpoint, owner.key, owner.id), TICKET)
    assert run.code == 2 and wire.requests == []
    codes = {r["code"] for r in run.doc["refusals"]}
    # Since the evidence-only release (2026-11-12 06:00 UTC, reports/phase-8g.md) the Groq evidence
    # exists but postdates this module's fixed clock (2026-11-09); the execution lock still refuses.
    assert {"real_execution_disabled", "free_plan_evidence_not_yet_valid"} <= codes
    assert run.doc["environment"]["backend"] == {"ready": True, "reason": "ready"}
    assert run.doc["correlation"]["trace_id"] is None
    assert project_totals(live_backend, owner.id) == before == (0, 0, 0)


@pytest.mark.usefixtures("released")
def test_a_dry_run_stores_nothing(live_backend, project, wire):
    before = project_totals(live_backend, project.id)
    run = invoke(
        compare_env(live_backend.ingest_endpoint, project.key, project.id), USAGE, execute=False
    )
    assert run.code == 0 and run.doc["execution_allowed"] is True and wire.requests == []
    assert project_totals(live_backend, project.id) == before


# ---------------------------------------------------------------- A, B, F, K, L: per scenario
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_each_scenario_is_stored_and_read_back_with_its_exact_identity(
    priced, project, wire, scenario
):
    started = datetime.now(UTC)
    response_id = rid()
    run = run_live(
        priced, project, wire, [ok(content=ANSWERS[scenario], response_id=response_id)], scenario
    )
    prompt_name, prompt_version, sha = IDENTITY[scenario]

    # the provider operation: one physical request carrying exactly the pinned input
    assert run.code == 0 and len(wire.requests) == 1
    sent = json.loads(wire.requests[0].content)
    assert sent["messages"] == [
        {"role": "user", "content": cs.compose_input(cs.SCENARIOS[scenario])}
    ]
    assert run.doc["scenario"]["input_sha256"] == sha
    assert run.doc["export"]["outcome"] == "confirmed"
    assert run.doc["environment"]["sdk_checkout_match"] is True

    # PostgreSQL, read directly
    tid = run.trace_id
    assert counts(priced, project.id, tid) == (1, 2, 1)
    spans = span_rows(priced, project.id, tid)
    root, client = spans["INTERNAL"], spans["CLIENT"]
    assert root["name"] == ROOT_SPAN and root["parent_span_id"] is None
    assert client["name"] == f"chat {MODEL}" and client["parent_span_id"] == root["span_id"]
    assert (root["status"], client["status"]) == ("UNSET", "UNSET")
    assert not any(k.startswith("gen_ai.") for k in root["attributes"])  # no parent double count
    assert (root["provenance"], client["provenance"]) == ("unknown", "real")
    assert client["attributes_schema_version"] == "llminsight.attrmap.v1"
    assert client["server_dropped_attributes_count"] == 0
    assert root["server_dropped_attributes_count"] == 0
    attrs = client["attributes"]
    assert set(attrs) == CLIENT_SPAN_KEYS
    assert attrs["gen_ai.provider.name"] == "groq"
    assert attrs["gen_ai.request.model"] == attrs["gen_ai.response.model"] == MODEL
    assert (attrs["gen_ai.prompt.name"], attrs["gen_ai.prompt.version"]) == (
        prompt_name,
        prompt_version,
    )
    assert attrs["gen_ai.response.finish_reasons"] == ["stop"]
    assert attrs["gen_ai.response.id"] == response_id
    assert attrs["gen_ai.request.max_tokens"] == 1024
    assert attrs["server.address"] == "api.groq.com"

    trace = trace_row(priced, project.id, tid)
    assert (trace["status"], trace["prompt_version"], trace["provenance"]) == (
        "ok",
        prompt_version,
        "real",
    )
    assert trace["span_count"] == 2

    (usage,) = usage_rows(priced, project.id, tid)
    assert usage["span_id"] == client["span_id"] and usage["attempt_id"] == 1
    assert (usage["provider"], usage["requested_model"], usage["response_model"]) == (
        "groq",
        MODEL,
        MODEL,
    )
    assert (usage["input_tokens"], usage["output_tokens"]) == (20, 32)
    assert usage["cached_input_tokens"] is None and usage["reasoning_output_tokens"] is None
    assert usage["usage_source"] == "provider_reported" and usage["provenance"] == "real"
    assert usage["canonical_call_key"] == f"resp:groq:{response_id}"
    # D: the seeded catalogue row for exactly groq and this model is never applied
    assert (usage["cost_usd"], usage["cost_status"], usage["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert usage["price_version"] is None and usage["model_price_id"] is None

    # the read API, through the exact path the runner printed
    assert run.api_path == f"/api/v1/traces/{tid}?project={project.id}"
    detail = priced.api(run.api_path)
    assert detail["trace"]["trace_id"] == tid and detail["trace"]["status"] == "ok"
    assert detail["trace"]["prompt_version"] == prompt_version
    assert (
        detail["trace"]["provenance"] == "real" and detail["trace"]["provenance_verified"] is False
    )
    assert detail["trace"]["usage"]["models"]["items"] == [
        {"provider": "groq", "requested_model": MODEL, "response_model": MODEL, "calls": 1}
    ]
    assert detail["trace"]["usage"]["known_cost_usd"] is None
    assert detail["trace"]["usage"]["unknown_cost_calls"] == 1
    roles = {s["span_id"]: s for s in detail["spans"]}
    assert roles[root["span_id"]]["role"] == "root"
    assert roles[root["span_id"]]["children"] == [client["span_id"]]
    assert roles[client["span_id"]]["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    assert roles[client["span_id"]]["server_dropped_attributes_count"] == 0
    (api_usage,) = detail["usage"]
    assert (api_usage["cost_usd"], api_usage["cost_status"], api_usage["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )

    scope = {"project": project.id, **window(started)}
    # The trace list filters by provider and model, not by prompt version (only Overview and
    # Models / Compare accept `prompt_version`); each item still carries it.
    listing = priced.api(
        f"/api/v1/traces?{urlencode({**scope, 'provider': 'groq', 'model': MODEL})}"
    )
    (item,) = [i for i in listing["items"] if i["trace_id"] == tid]
    assert item["prompt_version"] == prompt_version and item["status"] == "ok"
    assert item["usage"]["models"]["items"][0]["provider"] == "groq"

    text = "\n".join(
        [
            stored_text(priced, project.id),
            json.dumps(detail),
            json.dumps(listing),
            priced.log_text(),
        ]
    )
    assert_clean(text, project.key)


# ---------------------------------------------------------------- E, G: Overview and Compare
# The runner sends at most one request per process (ADR-44, `groq_live._PROCESS_SLOT`), so each
# runner execution below is its own test case with its own project. The three-row comparison set
# is verified separately, with SYNTHETIC spans that never pass through the runner.
def compare_groups(live: LiveBackend, scope: Mapping[str, Any], **params: str) -> list[dict]:
    return live.api(f"/api/v1/models/compare?{urlencode({**scope, **params})}")["groups"]


def assert_unpriced_group(group: dict, prompt_version: str, provenance: str) -> None:
    assert (
        group["provider"],
        group["requested_model"],
        group["response_model"],
        group["prompt_version"],
    ) == ("groq", MODEL, MODEL, prompt_version)
    assert group["attempts"] == 1 and group["errors"]["failed"] == 0
    assert (group["tokens"]["input_tokens"], group["tokens"]["output_tokens"]) == (20, 32)
    assert group["cost"]["known_cost_usd"] is None and group["cost"]["cost_complete"] is False
    assert group["cost"]["unknown_cost_by_reason"]["price_missing"] == 1
    assert group["by_provenance"][provenance] == 1 and sum(group["by_provenance"].values()) == 1
    assert group["latency"]["low_sample"] and all(group["latency"]["low_sample"].values())


def assert_unpriced_overview(overview: dict, calls: int) -> None:
    inference = overview["inference"]
    assert inference["attempts"]["total"] == calls and overview["traces"]["count"]["total"] == calls
    assert inference["errors"]["total"]["failed"] == 0
    tokens = inference["tokens"]["total"]
    assert (tokens["input_tokens"], tokens["output_tokens"]) == (20 * calls, 32 * calls)
    assert (
        tokens["calls_with_unknown_input_tokens"] == tokens["calls_with_unknown_output_tokens"] == 0
    )
    cost = inference["cost"]["total"]
    assert cost["known_cost_usd"] is None and cost["computed_cost_calls"] == 0
    assert cost["unknown_cost_calls"] == calls
    assert cost["unknown_cost_by_reason"]["price_missing"] == calls
    assert cost["cost_complete"] is False
    assert inference["usage_source"]["total"]["provider_reported"] == calls
    assert inference["price_versions"]["items"] == []  # the seeded groq row was never used
    assert overview["provenance_verified"] is False


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_one_runner_execution_gives_one_overview_and_compare_row(priced, wire, scenario):
    """One real runner execution (one per test case and process lifetime), own project."""
    owner = new_project(priced, f"one-{scenario}")
    started = datetime.now(UTC)
    run = run_live(
        priced, owner, wire, [ok(content=ANSWERS[scenario], response_id=rid())], scenario
    )
    assert run.code == 0 and len(wire.requests) == 1
    _, prompt_version, _ = IDENTITY[scenario]
    scope = {"project": owner.id, **window(started)}

    (group,) = compare_groups(priced, scope)
    assert_unpriced_group(group, prompt_version, "real")
    assert compare_groups(priced, scope, prompt_version=prompt_version) == [group]
    for other in sorted({v for _, v, _ in IDENTITY.values()} - {prompt_version}):
        assert compare_groups(priced, scope, prompt_version=other) == []

    assert_unpriced_overview(priced.api(f"/api/v1/overview?{urlencode(scope)}"), 1)
    listing = priced.api(f"/api/v1/traces?{urlencode({**scope, 'provider': 'groq'})}")
    assert [i["trace_id"] for i in listing["items"]] == [run.trace_id]


def test_a_synthetic_comparison_set_keeps_one_compare_row_per_scenario(priced):
    """SYNTHETIC aggregate, NOT a runner execution: three comparison-shaped traces, one per
    scenario, made by the SDK's Groq adapter on a SYNTHETIC wire with `synthetic` provenance and
    no send gate, and exported by the official OTLP exporter into one project. It checks how the
    views combine the three scenarios; the runner itself is checked one execution per test above.
    """
    owner = new_project(priced, "synthetic-set")
    started = datetime.now(UTC)
    fake = FakeGroq(
        [
            ok(content=ANSWERS[s], response_id=f"chatcmpl-SYNTHETIC-compare-set-{i}")
            for i, s in enumerate(SCENARIO_IDS)
        ]
    )
    telemetry = lli.init(
        lli.LLMInsightConfig(
            endpoint=priced.ingest_endpoint,
            project_key=owner.key,
            service_name="live-compare-set-it-SYNTHETIC",
            schedule_delay_millis=100,
            export_timeout_seconds=3.0,
        )
    )
    tids = []
    try:
        adapter = groq_adapter.GroqTextAdapter(
            groq_adapter.ObservedGroqClient(
                telemetry, FAKE_GROQ_KEY, http_client=fake.http_client(), provenance="synthetic"
            )
        )
        for scenario_id in SCENARIO_IDS:
            scenario = cs.SCENARIOS[scenario_id]
            request = TextRequest(
                model=MODEL,
                input_text=cs.compose_input(scenario),
                max_output_tokens=scenario.max_output_tokens,
                prompt_name=scenario.prompt_name,
                prompt_version=scenario.prompt_version,
            )
            with telemetry.tracer.start_as_current_span(ROOT_SPAN) as root:
                assert adapter.generate_text(request).status == "completed"
            tids.append(format(root.get_span_context().trace_id, "032x"))
        assert telemetry.force_flush(10_000)
    finally:
        telemetry.shutdown()
    assert len(fake.requests) == 3 and len(set(tids)) == 3
    scope = {"project": owner.id, **window(started)}

    groups = compare_groups(priced, scope)
    assert sorted(g["prompt_version"] for g in groups) == sorted(v for _, v, _ in IDENTITY.values())
    for g in groups:
        assert_unpriced_group(g, g["prompt_version"], "synthetic")
    (one,) = compare_groups(priced, scope, prompt_version="cmp-v1.json-aggregate")
    assert one["prompt_version"] == "cmp-v1.json-aggregate"

    overview = priced.api(f"/api/v1/overview?{urlencode(scope)}")
    assert_unpriced_overview(overview, 3)
    assert overview["inference"]["attempts"]["by_provenance"]["real"] == 0
    listing = priced.api(f"/api/v1/traces?{urlencode({**scope, 'provider': 'groq'})}")
    assert sorted(i["trace_id"] for i in listing["items"]) == sorted(tids)
    assert_clean(stored_text(priced, owner.id) + priced.log_text(), owner.key)


# ---------------------------------------------------------------- one request per process
@pytest.mark.usefixtures("released")
def test_a_second_execution_in_the_same_process_is_refused_and_stores_no_call(priced, wire):
    """The limit holds end to end: the second execution sends nothing and records no call. The
    runner opens its root span before the send gate refuses, so that root alone is exported."""
    owner = new_project(priced, "second")
    started = datetime.now(UTC)
    first = run_live(priced, owner, wire, [ok(content=ANSWERS[TICKET], response_id=rid())], TICKET)
    second = run_live(priced, owner, wire, [ok(content=ANSWERS[USAGE], response_id=rid())], USAGE)
    assert (first.code, second.code) == (0, 2) and len(wire.requests) == 1
    assert "request_limit_reached" in {r["code"] for r in second.doc["refusals"]}
    assert second.doc["provider_request_state"] == "not_sent"
    assert second.doc["session"]["physical_attempts"] == 0

    assert counts(priced, owner.id, first.trace_id) == (1, 2, 1)
    assert counts(priced, owner.id, second.trace_id) == (1, 1, 0)
    (row,) = priced.query(
        "SELECT row_to_json(s) FROM spans s WHERE project_id = %s AND trace_id = %s",
        (owner.id, second.trace_id),
    )
    root = row[0]
    assert (root["name"], root["kind"]) == (ROOT_SPAN, "INTERNAL")
    assert not any(k.startswith("gen_ai.") for k in root["attributes"])
    assert project_totals(priced, owner.id) == (2, 3, 1)

    scope = {"project": owner.id, **window(started)}
    (group,) = compare_groups(priced, scope)
    assert_unpriced_group(group, IDENTITY[TICKET][1], "real")
    overview = priced.api(f"/api/v1/overview?{urlencode(scope)}")
    assert overview["inference"]["attempts"]["total"] == 1
    assert overview["traces"]["count"]["total"] == 2  # the refused execution's root-only trace


# ---------------------------------------------------------------- C: the same export again
@pytest.mark.usefixtures("released")
def test_a_reexported_comparison_trace_creates_no_duplicate(priced, project, wire, monkeypatch):
    memory = InMemorySpanExporter()
    real_init = lli.init

    def recording_init(config: lli.LLMInsightConfig) -> Telemetry:
        telemetry = real_init(config)
        telemetry.tracer_provider.add_span_processor(SimpleSpanProcessor(memory))
        return telemetry

    monkeypatch.setattr(lli, "init", recording_init)
    run = run_live(priced, project, wire, [ok(content=ANSWERS[TICKET], response_id=rid())], TICKET)
    assert run.code == 0
    tid = run.trace_id
    before = (
        counts(priced, project.id, tid),
        usage_rows(priced, project.id, tid),
        span_rows(priced, project.id, tid),
    )
    assert before[0] == (1, 2, 1)
    finished = memory.get_finished_spans()
    assert len(finished) == 2
    exporter = build_span_exporter(
        lli.LLMInsightConfig(
            endpoint=priced.ingest_endpoint,
            project_key=project.key,
            service_name="live-compare-it-SYNTHETIC",
            export_timeout_seconds=3.0,
        )
    )
    try:
        for _ in range(2):  # an exporter or collector resending the same spans
            assert exporter.export(finished) == SpanExportResult.SUCCESS
    finally:
        exporter.shutdown()
    after = (
        counts(priced, project.id, tid),
        usage_rows(priced, project.id, tid),
        span_rows(priced, project.id, tid),
    )
    assert after == before


# ---------------------------------------------------------------- H: project isolation
@pytest.mark.usefixtures("released")
def test_a_comparison_trace_never_appears_in_another_project(priced, project, foreign, wire):
    started = datetime.now(UTC)
    run = run_live(
        priced, project, wire, [ok(content=ANSWERS[SUMMARY], response_id=rid())], SUMMARY
    )
    tid = run.trace_id
    assert api_status(priced, run.api_path)[0] == 200
    denied = api_status(priced, f"/api/v1/traces/{tid}?project={foreign.id}")
    absent = api_status(priced, f"/api/v1/traces/{uuid.uuid4().hex}?project={foreign.id}")
    assert denied[0] == absent[0] == 404 and denied[1] == absent[1]
    scope = {"project": foreign.id, **window(started)}
    listing = priced.api(f"/api/v1/traces?{urlencode(scope)}")
    assert tid not in {i["trace_id"] for i in listing["items"]}
    overview = priced.api(f"/api/v1/overview?{urlencode({**scope, 'provider': 'groq'})}")
    assert overview["inference"]["attempts"]["total"] == 0
    assert overview["traces"]["count"]["total"] == 0
    compare = priced.api(f"/api/v1/models/compare?{urlencode({**scope, 'provider': 'groq'})}")
    assert compare["groups"] == []
    assert counts(priced, foreign.id, tid) == (0, 0, 0)
    assert counts(priced, project.id, tid) == (1, 2, 1)


# ---------------------------------------------------------------- I: incomplete data fails closed
@dataclass(frozen=True)
class Case:
    id: str
    step: Step
    exit_code: int
    span_status: str
    error_type: str | None
    finish: list[str] | None
    trace_status: str
    tokens_known: bool
    cost_reason: str
    halt: str | None = None


INCONSISTENT = {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 99}
CASES = [
    Case("429", fail(429, "rate_limit_exceeded"), 1, "ERROR", "429", None, "error",
         False, "usage_missing"),
    Case("timeout", Step(raise_=httpx.ReadTimeout), 1, "ERROR", "timeout", None, "error",
         False, "usage_missing"),
    Case("malformed", Step(raw=b"{SYNTHETIC-not-json"), 1, "ERROR", "groq.response.malformed",
         None, "error", False, "usage_missing"),
    Case("usage-missing", ok(usage=None), 3, "UNSET", None, ["stop"], "ok",
         False, "usage_missing"),
    Case("usage-inconsistent", ok(usage=INCONSISTENT), 3, "UNSET", None, ["stop"], "ok",
         False, "usage_missing"),
    Case("incomplete", ok(finish="length", content="partial"), 3, "UNSET", None, ["length"], "ok",
         True, "price_missing"),
    Case("empty", ok(content=""), 1, "ERROR", "groq.response.empty", ["stop"], "error",
         True, "price_missing"),
    Case("paid-plan-indicator", fail(402), 3, "ERROR", "402", None, "error",
         False, "usage_missing", "paid_plan_indicator"),
    Case("service-tier", ok(service_tier="flex"), 3, "UNSET", None, ["stop"], "ok",
         True, "price_missing", "service_tier_not_free_plan"),
]  # fmt: skip


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_incomplete_or_failed_data_is_stored_once_without_manufactured_metrics(priced, wire, case):
    owner = new_project(priced, f"case-{case.id}")
    started = datetime.now(UTC)
    step = case.step
    if step.status == 200 and step.raw is None and step.raise_ is None:
        step.body["id"] = rid()
    run = run_live(priced, owner, wire, [step, ok(), ok()], TICKET)
    assert len(wire.requests) == 1, "exactly one physical request; nothing is resent"
    assert run.code == case.exit_code
    assert run.doc["free_plan_violation"] == case.halt
    assert run.doc["usage"]["cost"]["status"] == "unknown"
    assert run.doc["usage"]["cost"]["billed_amount_usd"] is None
    assert run.code != 0, "no incomplete or failed case may be reported as a success"

    tid = run.trace_id
    assert counts(priced, owner.id, tid) == (1, 2, 1)
    client = span_rows(priced, owner.id, tid)["CLIENT"]
    assert (client["status"], client["error_type"]) == (case.span_status, case.error_type)
    assert client["attributes"].get("gen_ai.response.finish_reasons") == case.finish
    assert client["server_dropped_attributes_count"] == 0
    (usage,) = usage_rows(priced, owner.id, tid)
    assert (usage["input_tokens"] is not None) is case.tokens_known
    assert (usage["output_tokens"] is not None) is case.tokens_known
    assert (usage["cost_usd"], usage["cost_status"], usage["cost_unknown_reason"]) == (
        None,
        "unknown",
        case.cost_reason,
    )
    if not case.tokens_known:
        assert usage["usage_source"] == "unknown"

    scope = {"project": owner.id, **window(started)}
    overview = priced.api(f"/api/v1/overview?{urlencode(scope)}")
    tokens = overview["inference"]["tokens"]["total"]
    if case.tokens_known:
        assert tokens["input_tokens"] == usage["input_tokens"]
    else:  # unknown stays unknown: no zero is manufactured
        assert tokens["input_tokens"] is None and tokens["output_tokens"] is None
        assert tokens["calls_with_unknown_input_tokens"] == 1
    cost = overview["inference"]["cost"]["total"]
    assert cost["known_cost_usd"] is None and cost["cost_complete"] is False
    assert cost["unknown_cost_by_reason"][case.cost_reason] == 1
    errors = overview["inference"]["errors"]["total"]
    assert errors["failed"] == (1 if case.span_status == "ERROR" else 0)
    detail = priced.api(run.api_path)
    assert detail["trace"]["status"] == case.trace_status
    assert_clean(json.dumps([overview, detail]) + stored_text(priced, owner.id) + priced.log_text())


# ---------------------------------------------------------------- K, L on the wire (no database)
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_each_scenario_on_the_wire(stub, wire, scenario):
    """No database: exactly what the official OTLP exporter sent, as decoded protobuf."""
    wire.steps = [ok(content=ANSWERS[scenario], response_id=rid())]
    run = invoke(compare_env(stub.endpoint, DUMMY_KEY, 7), scenario)
    prompt_name, prompt_version, _ = IDENTITY[scenario]
    assert run.code == 0 and run.doc["export"]["outcome"] == "confirmed"
    spans = stub.spans()
    assert len(spans) == 2 and {s["trace_id"] for s in spans} == {run.trace_id}
    (root,) = [s for s in spans if s["kind"] == "INTERNAL"]
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert root["name"] == ROOT_SPAN and client["parent_span_id"] == root["span_id"]
    assert set(client["attributes"]) == CLIENT_SPAN_KEYS
    assert client["attributes"]["gen_ai.provider.name"] == "groq"
    assert client["attributes"]["gen_ai.request.max_tokens"] == 1024
    assert client["attributes"]["llminsight.provenance"] == "real"
    assert (
        client["attributes"]["gen_ai.prompt.name"],
        client["attributes"]["gen_ai.prompt.version"],
    ) == (
        prompt_name,
        prompt_version,
    )
    for received in stub.received:
        assert_clean(received.body.decode("latin-1"))
        assert FAKE_GROQ_KEY not in json.dumps(received.headers)


@pytest.mark.usefixtures("released")
def test_a_second_execution_in_the_same_process_is_refused_on_the_wire(stub, wire):
    """No database: the second execution in one process lifetime reaches neither the provider
    nor the attempt span; only the runner's root span is exported for it."""
    wire.steps = [ok(content=ANSWERS[TICKET], response_id=rid())]
    first = invoke(compare_env(stub.endpoint, DUMMY_KEY, 7), TICKET)
    wire.steps = [ok(content=ANSWERS[USAGE], response_id=rid())]
    second = invoke(compare_env(stub.endpoint, DUMMY_KEY, 7), USAGE)
    assert (first.code, second.code) == (0, 2) and len(wire.requests) == 1
    assert "request_limit_reached" in {r["code"] for r in second.doc["refusals"]}
    assert second.doc["provider_request_state"] == "not_sent"
    assert second.doc["session"]["physical_attempts"] == 0
    spans = stub.spans()
    assert len(spans) == 3 and sum(s["kind"] == "CLIENT" for s in spans) == 1
    (root,) = [s for s in spans if s["trace_id"] == second.trace_id]
    assert (root["name"], root["kind"]) == (ROOT_SPAN, "INTERNAL")
    assert not any(k.startswith("gen_ai.") for k in root["attributes"])
