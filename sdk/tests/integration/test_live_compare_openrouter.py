"""Evidence for the OpenRouter path of `demo_app.live_compare` (ADR-48), and for the two-provider
comparison it exists for:

    runner → free-model guard → audited httpx client → SYNTHETIC wire → OpenRouterTextAdapter
           → official OTLP/HTTP protobuf → live `python -m app` → disposable PostgreSQL
           → project-scoped Overview, Traces and Models / Compare

The provider side is OFFLINE: only `httpx.HTTPTransport.handle_request` is replaced (`wire`), so
the runner's real client, endpoint boundary and send gate run, and every non-loopback connection is
blocked. The locks are lifted **inside this test process only** (`released`), as in the unit
tests; the shipped modules keep them closed, which `test_as_shipped_*` checks against the live
backend. As in `test_live_compare.py` (R129), the runner has only the real path, so its spans carry
`llminsight.provenance = real` although every response is SYNTHETIC, lives in the disposable
database and is dropped with it. None of this is evidence about a real OpenRouter response.

One request per process (ADR-44, ADR-47): each test case stands for one process lifetime. The
two-provider test runs two executions, Groq then OpenRouter, and gives the second its own fresh
process slot explicitly, standing for a second process.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

import llm_insight_sdk as lli
from llm_insight_sdk import groq_adapter, groq_live, openrouter_adapter, openrouter_live
from llm_insight_sdk.groq_live import FreePlanModel
from llm_insight_sdk.openrouter_live import FreeModel
from tests.conftest import DUMMY_KEY
from tests.groq_fixtures import MODEL as GROQ_MODEL
from tests.groq_fixtures import FakeGroq, Step
from tests.groq_fixtures import ok as groq_ok
from tests.integration.conftest import LiveBackend
from tests.openai_fixtures import block_network
from tests.openrouter_fixtures import (
    MODEL,
    OUTPUT_SENTINEL,
    SENTINELS,
    fail,
    ok,
    usage_with,
)
from tests.unit.test_groq_live_guard import ENV as GROQ_ENV
from tests.unit.test_openrouter_live_guard import ENV as OPENROUTER_ENV
from tests.unit.test_openrouter_live_guard import NOW

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import live_compare as runner  # noqa: E402

pytestmark = pytest.mark.integration

TICKET, USAGE, SUMMARY = "cmp-ticket-triage", "cmp-json-aggregate", "cmp-release-summary"
SCENARIO_IDS = (TICKET, USAGE, SUMMARY)
PROMPT_VERSIONS = {
    TICKET: "cmp-v1.ticket-triage",
    USAGE: "cmp-v1.json-aggregate",
    SUMMARY: "cmp-v1.release-summary",
}
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
)
OPENROUTER_PRICE_VERSION = "SYNTHETIC-openrouter-it-v1"
TODAY = NOW.astimezone(UTC).date().isoformat()  # the UTC date the guards compare against
ATTESTATION_DATE_VARS = {
    "groq": "LLMI_GROQ_PLAN_CHECKED_ON",
    "openrouter": "LLMI_OPENROUTER_ACCOUNT_CHECKED_ON",
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
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


@pytest.fixture
def released(monkeypatch) -> None:
    """Test-only release of the OpenRouter locks, and of Groq's for the two-provider test."""
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(openrouter_live, "FREE_MODEL_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(openrouter_live, "ZERO_PRICE_ROUTING_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        openrouter_live,
        "FREE_MODELS",
        {MODEL: FreeModel(MODEL, ("https://example.invalid/synthetic",))},
    )
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        groq_live,
        "FREE_PLAN_MODELS",
        {GROQ_MODEL: FreePlanModel(GROQ_MODEL, ("https://example.invalid/synthetic",))},
    )


@pytest.fixture
def wire(monkeypatch) -> FakeGroq:
    fake = FakeGroq([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


@pytest.fixture(scope="module")
def priced(groq_price: LiveBackend) -> LiveBackend:
    """A SYNTHETIC catalogue row for exactly `openrouter` and the model, beside the session's
    groq row: neither may ever price a call (ADR-40)."""
    groq_price.query(
        "INSERT INTO model_prices (version, provider, model, effective_from, input_per_million,"
        " output_per_million, source_url, verified_at)"
        " VALUES (%s, 'openrouter', %s, %s, 0, 0, 'https://example.invalid/synthetic', %s)"
        " ON CONFLICT DO NOTHING RETURNING 1",
        (OPENROUTER_PRICE_VERSION, MODEL, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    rows = groq_price.query(
        "SELECT version FROM model_prices WHERE provider = 'openrouter' AND model = %s", (MODEL,)
    )
    assert rows == [(OPENROUTER_PRICE_VERSION,)]
    return groq_price


@dataclass(frozen=True)
class Project:
    id: int
    key: str = field(repr=False)


def new_project(live: LiveBackend, label: str) -> Project:
    key, prefix = live.create_key(f"sdk-compare-or-it-{label}-{uuid.uuid4().hex[:16]}")
    (row,) = live.query("SELECT project_id FROM project_api_keys WHERE prefix = %s", (prefix,))
    return Project(int(row[0]), key)


def provider_env(provider: str) -> dict[str, str]:
    """The guard tests' SYNTHETIC environment, with the operator attestation dated on this
    module's injected clock: an attestation is valid for one day only, and the Groq guard tests
    use an earlier `NOW` than this module."""
    env = dict(OPENROUTER_ENV if provider == "openrouter" else GROQ_ENV)
    env[ATTESTATION_DATE_VARS[provider]] = TODAY
    return env


def env_for(provider: str, live: LiveBackend, owner: Project) -> dict[str, str]:
    env = provider_env(provider)
    env.update(
        {
            "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": live.ingest_endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={owner.key}",
            "OTEL_SERVICE_NAME": "live-compare-openrouter-it-SYNTHETIC",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "100",
            "LLMI_DASHBOARD_PROJECT_ID": str(owner.id),
        }
    )
    return env


def assert_clean(text: str, *extra: str) -> None:
    for forbidden in (*SENTINELS, *PROMPT_TEXTS, DUMMY_KEY, *extra):
        assert forbidden not in text


def invoke(provider: str, env: Mapping[str, str], scenario: str) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    argv = ["--provider", provider, "--scenario", scenario, "--execute"]
    code = runner.main(argv, env, now=NOW, out=out)
    (line,) = out.getvalue().splitlines()
    assert_clean(line)
    return code, json.loads(line)


def rid(prefix: str) -> str:
    return f"{prefix}-SYNTHETIC-compare-{uuid.uuid4().hex}"


def window(started: datetime) -> dict[str, str]:
    return {
        "from": (started - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (datetime.now(UTC) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def usage_rows(live: LiveBackend, pid: int) -> list[dict]:
    rows = live.query(
        "SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s ORDER BY provider",
        (pid,),
    )
    return [r[0] for r in rows]


def stored_text(live: LiveBackend, pid: int) -> str:
    rows = live.query(
        "SELECT row_to_json(t) FROM traces t WHERE project_id = %s"
        " UNION ALL SELECT row_to_json(s) FROM spans s WHERE project_id = %s"
        " UNION ALL SELECT row_to_json(u) FROM usage_records u WHERE project_id = %s",
        (pid, pid, pid),
    )
    return "\n".join(json.dumps(r[0]) for r in rows)


def compare_groups(live: LiveBackend, scope: Mapping[str, Any], **params: str) -> list[dict]:
    return live.api(f"/api/v1/models/compare?{urlencode({**scope, **params})}")["groups"]


# ---------------------------------------------------------------- the fixture's own consistency
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("provider", ["groq", "openrouter"])
def test_each_synthetic_environment_is_attested_on_the_injected_clock(provider):
    """Database-free. The runner reads its clock from `NOW`, so a fixture attested on another day
    is refused as stale before any send (PR #11, run 43092031596)."""
    env = provider_env(provider)
    assert env[ATTESTATION_DATE_VARS[provider]] == TODAY == "2026-11-11"
    if provider == "groq":
        decision = groq_live.evaluate_free_plan_policy(env, now=NOW)
    else:
        decision = openrouter_live.evaluate_free_model_policy(env, now=NOW)
    assert decision.allowed, sorted(r.code for r in decision.refusals)
    # The shared guard-test fixtures are not changed by this module.
    assert GROQ_ENV["LLMI_GROQ_PLAN_CHECKED_ON"] == "2026-11-09"


# ---------------------------------------------------------------- as shipped
def test_as_shipped_an_openrouter_execution_is_refused_and_stores_nothing(live_backend, wire):
    owner = new_project(live_backend, "shipped")
    code, doc = invoke("openrouter", env_for("openrouter", live_backend, owner), TICKET)
    assert code == 2 and wire.requests == []
    codes = {r["code"] for r in doc["refusals"]}
    assert {
        "real_execution_disabled",
        "free_model_evidence_missing",
        "free_routing_evidence_missing",
    } <= codes
    assert doc["environment"]["backend"] == {"ready": True, "reason": "ready"}
    assert usage_rows(live_backend, owner.id) == []


# ---------------------------------------------------------------- one execution per scenario
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("scenario", SCENARIO_IDS)
def test_each_scenario_is_stored_and_read_back_with_its_identity(priced, wire, scenario):
    owner = new_project(priced, f"one-{scenario}")
    started = datetime.now(UTC)
    wire.steps = [ok(content=ANSWERS[scenario], response_id=rid("gen"))]
    code, doc = invoke("openrouter", env_for("openrouter", priced, owner), scenario)
    assert code == 0, doc
    assert len(wire.requests) == 1
    tid = doc["correlation"]["trace_id"]

    (u,) = usage_rows(priced, owner.id)
    assert (u["provider"], u["requested_model"], u["response_model"]) == (
        "openrouter",
        MODEL,
        MODEL,
    )
    assert (u["input_tokens"], u["output_tokens"]) == (24, 40)
    assert (u["cached_input_tokens"], u["cache_write_input_tokens"]) == (4, 2)
    assert u["reasoning_output_tokens"] == 8
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )  # the provider reported 0 credits and a 0 catalogue row exists: still unknown, never 0
    assert u["price_version"] is None and u["model_price_id"] is None
    assert u["canonical_call_key"].startswith("resp:openrouter:gen-SYNTHETIC-compare-")

    scope = {"project": owner.id, **window(started)}
    (group,) = compare_groups(priced, scope)
    assert (group["provider"], group["requested_model"], group["response_model"]) == (
        "openrouter",
        MODEL,
        MODEL,
    )
    assert group["prompt_version"] == PROMPT_VERSIONS[scenario]
    assert group["cost"]["known_cost_usd"] is None and group["cost"]["cost_complete"] is False
    assert group["latency"]["low_sample"] and all(group["latency"]["low_sample"].values())
    listing = priced.api(f"/api/v1/traces?{urlencode({**scope, 'provider': 'openrouter'})}")
    assert [i["trace_id"] for i in listing["items"]] == [tid]
    detail = priced.api(doc["correlation"]["api_path"])
    client = [s for s in detail["spans"] if s["kind"] == "CLIENT"]
    assert len(client) == 1 and client[0]["attributes"]["server.address"] == "openrouter.ai"
    assert_clean(stored_text(priced, owner.id), owner.key)
    assert "byok" not in stored_text(priced, owner.id)


# ---------------------------------------------------------------- the two-provider comparison
@pytest.mark.usefixtures("released")
def test_groq_and_openrouter_on_one_scenario_give_two_compare_rows(priced, wire, monkeypatch):
    owner = new_project(priced, "two-providers")
    started = datetime.now(UTC)
    wire.steps = [groq_ok(content=ANSWERS[USAGE], response_id=rid("chatcmpl"))]
    groq_code, groq_doc = invoke("groq", env_for("groq", priced, owner), USAGE)
    assert groq_code == 0, groq_doc
    # A second process: one fresh slot, taken once by the OpenRouter execution.
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())
    wire.steps = [ok(content=ANSWERS[USAGE], response_id=rid("gen"))]
    or_code, or_doc = invoke("openrouter", env_for("openrouter", priced, owner), USAGE)
    assert or_code == 0, or_doc
    assert len(wire.requests) == 2

    # The same scenario text and output cap went to both providers.
    groq_body, or_body = (json.loads(r.content) for r in wire.requests)
    assert groq_body["messages"] == or_body["messages"]
    assert groq_body["max_completion_tokens"] == or_body["max_tokens"] == 1024
    assert groq_doc["scenario"]["input_sha256"] == or_doc["scenario"]["input_sha256"]

    scope = {"project": owner.id, **window(started)}
    groups = compare_groups(priced, scope)
    by_provider = {g["provider"]: g for g in groups}
    assert set(by_provider) == {"groq", "openrouter"} and len(groups) == 2
    assert {g["prompt_version"] for g in groups} == {PROMPT_VERSIONS[USAGE]}
    assert by_provider["groq"]["response_model"] == GROQ_MODEL
    assert by_provider["openrouter"]["response_model"] == MODEL
    for group in groups:
        assert group["attempts"] == 1
        assert group["cost"]["known_cost_usd"] is None and group["cost"]["cost_complete"] is False
        assert group["latency"]["low_sample"] and all(group["latency"]["low_sample"].values())
    assert compare_groups(priced, scope, provider="openrouter") == [by_provider["openrouter"]]

    overview = priced.api(f"/api/v1/overview?{urlencode(scope)}")
    cost = overview["inference"]["cost"]["total"]
    assert cost["known_cost_usd"] is None and cost["unknown_cost_calls"] == 2
    assert overview["inference"]["price_versions"]["items"] == []
    assert_clean(stored_text(priced, owner.id), owner.key)


# ---------------------------------------------------------------- failures and halts
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("step", "exit_code", "error_type", "reason"),
    [
        (fail(429), 1, "429", "usage_missing"),
        (
            Step(200, {"error": {"code": 502, "message": "x"}}),
            1,
            "openrouter.response.error",
            "usage_missing",
        ),
        (Step(raise_=httpx.ReadTimeout), 1, "timeout", "usage_missing"),
    ],
    ids=["429", "200-with-error", "timeout"],
)
def test_a_failure_is_stored_once_with_unknown_usage_and_cost(
    priced, wire, step, exit_code, error_type, reason
):
    owner = new_project(priced, "failure")
    wire.steps = [step, ok()]
    code, doc = invoke("openrouter", env_for("openrouter", priced, owner), TICKET)
    assert code == exit_code and len(wire.requests) == 1
    (u,) = usage_rows(priced, owner.id)
    assert u["input_tokens"] is None and u["output_tokens"] is None
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (None, "unknown", reason)
    (span,) = priced.query(
        "SELECT error_type FROM spans WHERE project_id = %s AND kind = 'CLIENT'", (owner.id,)
    )
    assert span[0] == error_type


@pytest.mark.usefixtures("released")
def test_a_charge_indicator_halts_and_the_stored_cost_stays_unknown(priced, wire):
    owner = new_project(priced, "halt")
    wire.steps = [ok(content=ANSWERS[TICKET], usage=usage_with(cost=0.02), response_id=rid("gen"))]
    code, doc = invoke("openrouter", env_for("openrouter", priced, owner), TICKET)
    assert code == 3 and doc["free_plan_violation"] == "cost_not_zero"
    (u,) = usage_rows(priced, owner.id)
    assert u["cost_usd"] is None and u["cost_status"] == "unknown"
    assert "0.02" not in stored_text(priced, owner.id)
