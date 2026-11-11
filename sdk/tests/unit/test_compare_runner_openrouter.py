"""Comparison runner, OpenRouter path (ADR-48), fully offline.

The backend is the local OTLP stub and the provider is SYNTHETIC: the real audited client and the
real free-model guard run, and only `httpx.HTTPTransport.handle_request` is replaced (`wire`). The
three OpenRouter locks are lifted only by `released`; tests without it run the modules exactly as
shipped. No test sends a provider request.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Iterator, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_insight_sdk import groq_live, openrouter_adapter, openrouter_live
from llm_insight_sdk.openrouter_live import FreeModel
from tests.conftest import DUMMY_KEY, PROMPT_SENTINEL
from tests.groq_fixtures import Step
from tests.openai_fixtures import block_network
from tests.openrouter_fixtures import (
    ENDPOINT,
    FAKE_OPENROUTER_KEY,
    MODEL,
    PAID_MODEL,
    SENTINELS,
    FakeOpenRouter,
    error_body,
    fail,
    ok,
    usage_with,
)
from tests.unit.test_compare_runner import GOOD, SUMMARY, TICKET, USAGE, NeverBuilt, Recording
from tests.unit.test_compare_runner import runner_env as groq_runner_env
from tests.unit.test_openrouter_live_guard import ENV as GUARD_ENV
from tests.unit.test_openrouter_live_guard import NOW

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import comparison_scenarios as cs  # noqa: E402
from demo_app import live_compare as runner  # noqa: E402

SECRETS = (*SENTINELS, DUMMY_KEY, PROMPT_SENTINEL)
SHIPPED_REFUSALS = {
    "real_execution_disabled",
    "free_model_evidence_missing",
    "free_routing_evidence_missing",
    "model_not_free_verified",
}


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture(autouse=True)
def own_slot(monkeypatch) -> None:
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


@pytest.fixture
def released(monkeypatch) -> None:
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(openrouter_live, "FREE_MODEL_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(openrouter_live, "ZERO_PRICE_ROUTING_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        openrouter_live,
        "FREE_MODELS",
        {MODEL: FreeModel(MODEL, ("https://example.invalid/synthetic",))},
    )


@pytest.fixture
def wire(monkeypatch) -> FakeOpenRouter:
    fake = FakeOpenRouter([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


def runner_env(endpoint: str, **changes: str | None) -> dict[str, str]:
    env = dict(GUARD_ENV)
    env.update(
        {
            "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "live-compare-openrouter-unit-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "7",
        }
    )
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def run(argv: list[str], env: Mapping[str, str], **kw: Any) -> tuple[int, dict[str, Any], str]:
    out = io.StringIO()
    kw.setdefault("now", NOW)
    code = runner.main(argv, env, out=out, **kw)
    text = out.getvalue()
    for secret in SECRETS:
        assert secret not in text
    for scenario in cs.SCENARIOS.values():
        assert scenario.fixture not in text and scenario.task not in text
    for answer in GOOD.values():
        assert answer not in text
    (line,) = text.splitlines()
    doc = json.loads(line)
    assert doc["schema"] == "llminsight.demo.compare.v1"
    return code, doc, text


def execute(stub, scenario: str = SUMMARY, **kw: Any) -> tuple[int, dict[str, Any], str]:
    env = kw.pop("env", None) or runner_env(stub.endpoint)
    return run(["--provider", "openrouter", "--scenario", scenario, "--execute"], env, **kw)


def codes(doc: dict[str, Any]) -> set[str]:
    return {r["code"] for r in doc["refusals"]}


# ---------------------------------------------------------------- as shipped
@pytest.mark.parametrize("scenario", [TICKET, USAGE, SUMMARY])
def test_as_shipped_the_dry_run_reports_the_three_locks_and_sends_nothing(stub, wire, scenario):
    never = NeverBuilt()
    code, doc, _ = run(
        ["--provider", "openrouter", "--scenario", scenario],
        runner_env(stub.endpoint),
        http_factory=never,
    )
    assert code == 0 and never.calls == 0 and wire.requests == []
    assert doc["provider_selection"] == {
        "name": "openrouter",
        "provider": "openrouter",
        "available": True,
    }
    assert (doc["policy_eligible"], doc["execution_allowed"]) == (False, False)
    assert codes(doc) >= SHIPPED_REFUSALS
    constraints = doc["constraints"]
    assert constraints["real_execution_enabled"] is False
    assert constraints["free_models_released"] == 0
    assert constraints["zero_price_routing_verified"] is False
    assert stub.spans() == []


def test_as_shipped_execute_is_refused_before_any_client_exists(stub, wire, monkeypatch):
    def forbidden() -> httpx.Client:
        raise AssertionError("the audited client must not be built")

    monkeypatch.setattr(openrouter_adapter, "build_openrouter_http_client", forbidden)
    code, doc, _ = execute(stub, TICKET, http_factory=None)
    assert code == 2 and wire.requests == [] and stub.spans() == []
    assert codes(doc) >= SHIPPED_REFUSALS and doc["session"] is None


def test_the_fixed_constraints_are_reported_as_shipped(stub):
    _, doc, _ = run(["--provider", "openrouter", "--scenario", TICKET], runner_env(stub.endpoint))
    constraints = doc["constraints"]
    assert constraints["endpoint"] == f"POST {ENDPOINT}"
    assert constraints["request_fields"] == [
        "model",
        "messages",
        "max_tokens",
        "stream",
        "reasoning",
        "provider",
    ]
    assert constraints["routing"] == {
        "allow_fallbacks": False,
        "data_collection": "deny",
        "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
    }
    assert constraints["model_variant"] == ":free"
    assert constraints["max_output_tokens"] == 1024 and constraints["retries"] == 0
    for flag in ("model_fallback", "provider_fallback", "paid_plan", "proxy", "streaming"):
        assert constraints[flag] is False, flag


# ---------------------------------------------------------------- released: dry run
@pytest.mark.usefixtures("released")
def test_a_released_dry_run_is_ready_and_still_sends_nothing(stub, wire):
    never = NeverBuilt()
    code, doc, _ = run(
        ["--provider", "openrouter", "--scenario", USAGE],
        runner_env(stub.endpoint),
        http_factory=never,
    )
    assert code == 0 and never.calls == 0 and wire.requests == []
    assert doc["execution_allowed"] is True and doc["refusals"] == []
    plan = doc["plan"]
    assert (plan["provider"], plan["model"], plan["endpoint"]) == ("openrouter", MODEL, ENDPOINT)
    assert plan["account_state"] == "no-purchased-credits"
    assert "not verified" in plan["account_basis"]
    assert plan["routing"]["allow_fallbacks"] is False and plan["model_fallback"] is False
    assert plan["paid_fallback"] is False
    assert doc["scenario"]["prompt_version"] == "cmp-v1.json-aggregate"


@pytest.mark.usefixtures("released")
def test_the_groq_settings_do_not_authorize_openrouter(stub, wire):
    code, doc, _ = run(
        ["--provider", "openrouter", "--scenario", TICKET, "--execute"],
        groq_runner_env(stub.endpoint),
        http_factory=NeverBuilt(),
    )
    assert code == 2 and wire.requests == []
    assert {"openrouter_mode_not_free", "consent_missing", "api_key_missing"} <= codes(doc)


# ---------------------------------------------------------------- released: one request
@pytest.mark.usefixtures("released")
@pytest.mark.parametrize("scenario", [TICKET, USAGE, SUMMARY])
def test_one_completed_request_with_the_shared_prompt_and_metadata_only_output(
    stub, wire, scenario
):
    wire.steps = [ok(content=GOOD[scenario])]
    code, doc, _ = execute(stub, scenario)
    assert code == 0, doc
    (request,) = wire.requests
    assert str(request.url) == ENDPOINT
    assert request.headers["authorization"] == f"Bearer {FAKE_OPENROUTER_KEY}"
    body = json.loads(request.content)
    reviewed = cs.SCENARIOS[scenario]
    # The identical composed text and output cap that the Groq path sends (test_compare_runner).
    assert body["messages"] == [{"role": "user", "content": cs.compose_input(reviewed)}]
    assert body["max_tokens"] == reviewed.max_output_tokens == cs.UNIFORM_OUTPUT_TOKENS
    assert body["model"] == MODEL and "models" not in body and "route" not in body
    assert body["provider"] == dict(openrouter_adapter.PROVIDER_ROUTING)

    assert doc["provider_request_state"] == "sent" and doc["provider_request_sent"] is True
    provider = doc["provider"]
    assert (provider["provider"], provider["outcome"], provider["attempts"]) == (
        "openrouter",
        "completed",
        1,
    )
    assert provider["request_model"] == provider["response_model"] == MODEL
    assert doc["usage"]["tokens"] == {
        "input": 24,
        "output": 40,
        "cache_read_input": 4,
        "cache_write_input": 2,
        "reasoning_output": 8,
    }
    cost = doc["usage"]["cost"]
    assert cost["status"] == "unknown" and cost["billed_amount_usd"] is None
    assert cost["ledger_cost_status"] == "unknown (price_missing)"
    assert (cost["provider_reported_cost"], cost["provider_reported_byok"]) == ("zero", "false")
    assert doc["validation"]["status"] == "passed"
    assert doc["free_plan_violation"] is None and doc["export"]["outcome"] == "confirmed"
    assert doc["session"]["physical_attempts"] == 1


@pytest.mark.usefixtures("released")
def test_the_exported_trace_groups_by_provider_model_and_scenario(stub, wire):
    wire.steps = [ok(content=GOOD[TICKET])]
    code, doc, _ = execute(stub, TICKET)
    assert code == 0
    spans = stub.spans()
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    (root,) = [s for s in spans if s["name"] == runner.ROOT_SPAN]
    attrs = client["attributes"]
    assert attrs["gen_ai.provider.name"] == "openrouter"
    assert attrs["gen_ai.request.model"] == attrs["gen_ai.response.model"] == MODEL
    assert attrs["gen_ai.prompt.version"] == "cmp-v1.ticket-triage"
    assert attrs["server.address"] == "openrouter.ai"
    assert client["parent_span_id"] == root["span_id"]
    assert client["trace_id"] == doc["correlation"]["trace_id"]
    text = json.dumps(spans)
    for secret in (*SECRETS, cs.compose_input(cs.SCENARIOS[TICKET])):
        assert secret not in text
    assert "cost" not in json.dumps(attrs) and "byok" not in json.dumps(attrs)


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("step", "halt"),
    [
        (ok(content=GOOD[TICKET], usage=usage_with(cost=0.01)), "cost_not_zero"),
        (ok(content=GOOD[TICKET], usage=usage_with(cost=None)), "cost_unverified"),
        (ok(content=GOOD[TICKET], usage=usage_with(is_byok=True)), "byok_used"),
        (ok(content=GOOD[TICKET], model=PAID_MODEL), "response_model_mismatch"),
        (fail(402), "payment_required_indicator"),
        (Step(200, error_body(402)), "payment_required_indicator"),
    ],
    ids=["cost", "no-cost", "byok", "paid-model", "402", "200-with-402"],
)
def test_a_free_only_violation_halts_with_exit_3_and_sends_nothing_more(stub, wire, step, halt):
    wire.steps = [step, ok()]
    code, doc, _ = execute(stub, TICKET)
    assert code == 3 and doc["free_plan_violation"] == halt
    assert len(wire.requests) == 1


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("step", "category"),
    [
        (fail(429), "rate_limited"),
        (fail(503), "provider_error"),
        (Step(200, error_body(502)), "provider_error"),
        (Step(raise_=httpx.ReadTimeout), "timeout"),
    ],
    ids=["429", "503", "200-with-502", "timeout"],
)
def test_a_failure_is_reported_once_and_never_retried(stub, wire, step, category):
    wire.steps = [step, ok()]
    code, doc, _ = execute(stub, TICKET)
    assert code == 1 and len(wire.requests) == 1
    assert doc["provider"]["error_category"] == category
    assert doc["usage"]["tokens"] is None and doc["usage"]["cost"]["status"] == "unknown"
    assert doc["validation"]["status"] == "not_evaluated"


@pytest.mark.usefixtures("released")
def test_an_incomplete_answer_is_not_a_success(stub, wire):
    wire.steps = [ok(finish="length", content=GOOD[TICKET])]
    code, doc, _ = execute(stub, TICKET)
    assert code == 3 and doc["provider"]["outcome"] == "incomplete"
    assert doc["validation"]["status"] == "not_evaluated"


@pytest.mark.usefixtures("released")
def test_a_second_execution_in_the_same_process_is_refused(stub, wire):
    wire.steps = [ok(content=GOOD[TICKET]), ok(content=GOOD[TICKET])]
    assert execute(stub, TICKET)[0] == 0
    code, doc, _ = execute(stub, TICKET)
    assert code == 2 and "request_limit_reached" in codes(doc)
    assert len(wire.requests) == 1


@pytest.mark.usefixtures("released")
def test_the_runner_asks_the_guard_exactly_once(stub, wire, monkeypatch):
    calls: list[int] = []
    original = openrouter_live.FreeModelSession.authorize_send

    def counted(self: openrouter_live.FreeModelSession, *args: Any) -> Any:
        calls.append(1)
        return original(self, *args)

    monkeypatch.setattr(openrouter_live.FreeModelSession, "authorize_send", counted)
    wire.steps = [fail(503)]
    execute(stub, TICKET)
    assert calls == [1] and len(wire.requests) == 1


@pytest.mark.usefixtures("released")
@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"LLMI_LIVE_MODELS": PAID_MODEL}, "model_not_free_variant"),
        ({"LLMI_LIVE_MODELS": "openrouter/auto"}, "model_not_free_variant"),
        ({"LLMI_OPENROUTER_ACCOUNT_STATE": "has-credits"}, "account_not_free_only"),
        ({"OPENROUTER_BASE_URL": "http://127.0.0.1:9"}, "client_env_override"),
        ({"SSLKEYLOGFILE": "keys.log"}, "client_env_override"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "512"}, "output_limit_exceeded"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid"),
        ({"LLMI_DASHBOARD_PROJECT_ID": None}, "dashboard_project_invalid"),
    ],
)
def test_each_refusal_blocks_execution_and_sends_nothing(stub, wire, changes, code):
    never = NeverBuilt()
    env = runner_env(stub.endpoint, **changes)
    argv = ["--provider", "openrouter", "--scenario", USAGE]
    dry, dry_doc, _ = run(argv, env, http_factory=never)
    rc, doc, _ = run([*argv, "--execute"], env, http_factory=never)
    assert dry == 0 and code in codes(dry_doc) and dry_doc["execution_allowed"] is False
    assert rc == 2 and code in codes(doc)
    assert never.calls == 0 and wire.requests == []


@pytest.mark.usefixtures("released")
def test_the_validated_credential_is_the_one_that_is_sent(stub, wire):
    """The runner hands the client the key the policy read; a changed environment is refused."""
    env = Recording(runner_env(stub.endpoint))
    wire.steps = [ok(content=GOOD[TICKET])]
    code, _, _ = execute(stub, TICKET, env=env)
    assert code == 0
    assert wire.requests[0].headers["authorization"] == f"Bearer {FAKE_OPENROUTER_KEY}"
