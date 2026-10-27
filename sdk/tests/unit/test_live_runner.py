"""Live demo runner `demo_app.live_openai` (Phase 6A, ADR-30), offline: the backend is the
local OTLP stub, the provider is an injected SYNTHETIC fake transport (`synthetic`
provenance), and every non-loopback connection is blocked."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx2
import openai
import pytest

from llm_insight_sdk import live
from tests.conftest import DUMMY_KEY, free_port
from tests.openai_fixtures import (
    COMPLETION_SENTINEL,
    ERROR_SENTINEL,
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

NOW = live.PRICE_VERIFIED_AT + timedelta(days=1)
SMALL_USAGE = {
    "input_tokens": 40,
    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
    "output_tokens": 12,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 52,
}


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def runner_env(endpoint: str, **overrides: str | None) -> dict[str, str]:
    env = live_env(**overrides)
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "live-runner-unit-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "7",
        }
    )
    return {k: v for k, v in env.items() if v is not None}


class NeverCalled:
    calls = 0

    def __call__(self, policy: live.LivePolicy) -> openai.OpenAI:
        NeverCalled.calls += 1
        raise AssertionError("no provider client may be created")


def run(argv: list[str], env: dict[str, str], **kw) -> tuple[int, dict]:
    out = io.StringIO()
    kw.setdefault("now", NOW)
    kw.setdefault("provenance", "synthetic")
    code = live_openai.main(argv, env, out=out, **kw)
    text = out.getvalue()
    for secret in (FAKE_API_KEY, DUMMY_KEY, COMPLETION_SENTINEL, ERROR_SENTINEL):
        assert secret not in text
    assert live_openai.INPUT_TEXT not in text
    (line,) = text.splitlines()
    return code, json.loads(line)


# ---------------------------------------------------------------- dry run (the default)
def test_dry_run_is_the_default_and_never_creates_a_provider_client(stub):
    never = NeverCalled()
    code, doc = run([], runner_env(stub.endpoint), client_factory=never)
    assert code == 0 and never.calls == 0
    assert doc["mode"] == "dry_run" and doc["provider_request_sent"] is False
    assert doc["execution_allowed"] is True and doc["refusals"] == []
    assert doc["backend"] == {"ready": True, "reason": "ready"}
    plan = doc["plan"]
    assert plan["model"] == "gpt-5.4-mini" and plan["store"] is False
    assert plan["service_tier"] == "default" and plan["max_attempts"] == 1
    assert Decimal(plan["worst_case_request_usd"]) > 0
    assert plan["price_catalogue"] == live.PRICE_CATALOGUE_VERSION
    assert stub.spans() == []  # the readiness probe is an empty request


def test_dry_run_without_configuration_lists_every_refusal(stub):
    code, doc = run([], {}, client_factory=NeverCalled())
    assert code == 0 and doc["execution_allowed"] is False and doc["plan"] is None
    assert doc["backend"]["reason"] == "telemetry_config_invalid"
    assert {"provider_mode_not_real", "consent_missing", "api_key_missing"} <= {
        r["code"] for r in doc["refusals"]
    }


# ---------------------------------------------------------------- execute: refusals
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real"),
        ({"LLMI_LIVE_CONSENT": None}, "consent_missing"),
        ({"OPENAI_API_KEY": None}, "api_key_missing"),
        ({"LLMI_LIVE_MODELS": "gpt-5.5"}, "model_price_unknown"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": None}, "setting_missing"),
        ({"LLMI_REAL_LLM_BUDGET_USD": "0.0000001"}, "session_budget_too_small"),
        ({"LLMI_REAL_LLM_BUDGET_USD": "0"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "0.00001"}, "request_cost_bound_exceeds_limit"),
    ],
)
def test_execute_is_refused_by_every_gate(stub, overrides, code):
    never = NeverCalled()
    rc, doc = run(["--execute"], runner_env(stub.endpoint, **overrides), client_factory=never)
    assert rc == 2 and never.calls == 0
    assert doc["provider_request_sent"] is False and doc["execution_allowed"] is False
    assert code in {r["code"] for r in doc["refusals"]}
    assert stub.spans() == []


def test_execute_is_refused_with_a_stale_price_catalogue(stub):
    late = live.PRICE_VERIFIED_AT + live.PRICE_MAX_AGE + timedelta(days=1)
    rc, doc = run(["--execute"], runner_env(stub.endpoint), now=late, client_factory=NeverCalled())
    assert rc == 2 and "price_catalogue_stale" in {r["code"] for r in doc["refusals"]}


def test_execute_is_refused_for_a_model_outside_the_allowlist(stub):
    rc, doc = run(
        ["--execute", "--model", "gpt-4.1-mini"],
        runner_env(stub.endpoint),
        client_factory=NeverCalled(),
    )
    assert rc == 2 and "model_not_allowed" in {r["code"] for r in doc["refusals"]}


def test_execute_is_refused_when_the_backend_is_unreachable():
    endpoint = f"http://127.0.0.1:{free_port()}/v1/traces"
    rc, doc = run(["--execute"], runner_env(endpoint), client_factory=NeverCalled())
    assert rc == 2 and doc["backend"]["reason"] == "backend_unreachable"
    assert "backend_not_ready" in {r["code"] for r in doc["refusals"]}


def test_execute_is_refused_when_the_project_key_is_rejected(stub):
    stub.behaviour.status = 401
    rc, doc = run(["--execute"], runner_env(stub.endpoint), client_factory=NeverCalled())
    assert rc == 2 and doc["backend"]["reason"] == "project_key_rejected"


# ---------------------------------------------------------------- execute: one request
def test_execute_sends_one_request_and_exports_a_correlated_trace(stub):
    fake = FakeOpenAI([response_ok(usage=SMALL_USAGE, response_id="resp_SYNTHETIC_runner")])
    rc, doc = run(["--execute"], runner_env(stub.endpoint), client_factory=lambda p: fake.client())
    assert rc == 0
    assert len(fake.requests) == 1
    sent = fake.request_json(0)
    assert sent["store"] is False and sent["service_tier"] == "default"
    assert sent["input"] == live_openai.INPUT_TEXT and sent["max_output_tokens"] == 64
    assert doc["provider_request_sent"] is True and doc["status"] == "completed"
    assert doc["error_category"] is None and doc["attempts"] == 1
    assert doc["provenance"] == "synthetic"
    assert doc["tokens"] == {
        "input": 40,
        "output": 12,
        "cache_read_input": 0,
        "cache_write_input": 0,
        "reasoning_output": 0,
    }
    assert doc["estimated_cost_usd"] == "0.0000840000"  # (40 * 0.75 + 12 * 4.50) / 1e6
    assert doc["booked_against_budget_usd"] == "0.0000840000"
    assert "not a billed amount" in doc["cost_basis"]
    assert doc["session"]["halted"] is None and doc["session"]["requests_started"] == 1
    assert doc["telemetry"]["flushed"] is True and doc["telemetry"]["spans_export_failed"] == 0
    trace_id = doc["trace_id"]
    assert doc["dashboard_url"] == f"http://127.0.0.1:3001/traces/{trace_id}?project=7"
    assert doc["api_path"] == f"/api/v1/traces/{trace_id}"

    spans = stub.spans()
    (root,) = [s for s in spans if s["name"] == live_openai.ROOT_SPAN]
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert len(spans) == 2  # one root, one attempt: nothing duplicated
    assert root["trace_id"] == client["trace_id"] == trace_id
    assert client["parent_span_id"] == root["span_id"]
    a = client["attributes"]
    assert a["openai.api.type"] == "responses" and a["llminsight.provenance"] == "synthetic"
    assert a["gen_ai.response.id"] == "resp_SYNTHETIC_runner"
    assert a["gen_ai.prompt.name"] == "live-smoke" and a["gen_ai.request.max_tokens"] == 64


def test_usage_beyond_the_bound_is_reported_and_halts_the_session(stub):
    fake = FakeOpenAI([response_ok()])  # 1200 input / 300 output: beyond the limits
    rc, doc = run(["--execute"], runner_env(stub.endpoint), client_factory=lambda p: fake.client())
    assert rc == 0 and doc["status"] == "completed"
    assert doc["estimated_cost_usd"] == "0.0015750000"
    assert doc["session"]["halted"] == "usage_exceeded_bound"


@pytest.mark.parametrize(
    ("step", "category"),
    [
        (fail(401, "invalid_api_key"), "authentication_failed"),
        (fail(429, "rate_limit_exceeded"), "rate_limited"),
        (fail(500, "server_error"), "provider_error"),
        (Step(raise_=httpx2.ReadTimeout), "timeout"),
    ],
)
def test_provider_failures_are_reported_safely_and_booked_conservatively(stub, step, category):
    fake = FakeOpenAI([step])
    rc, doc = run(["--execute"], runner_env(stub.endpoint), client_factory=lambda p: fake.client())
    assert rc == 1 and len(fake.requests) == 1  # no automatic retry (LLMI_LIVE_MAX_RETRIES=0)
    assert doc["status"] == "error" and doc["error_category"] == category
    assert doc["tokens"] is None and doc["estimated_cost_usd"] is None
    assert Decimal(doc["booked_against_budget_usd"]) > 0  # unknown is never 0
    (client,) = [s for s in stub.spans() if s["kind"] == "CLIENT"]
    assert client["status"] == "ERROR"


def test_configured_retry_is_used_only_for_rate_limits(stub):
    fake = FakeOpenAI([fail(429, "rate_limit_exceeded"), response_ok(usage=SMALL_USAGE)])
    env = runner_env(stub.endpoint, LLMI_LIVE_MAX_RETRIES="1")
    rc, doc = run(["--execute"], env, client_factory=lambda p: fake.client())
    assert rc == 0 and doc["attempts"] == 2 and len(fake.requests) == 2
    assert doc["estimated_cost_usd"] is None  # the rejected attempt's cost is unknown
    bound = Decimal(doc["booked_against_budget_usd"]) - Decimal("0.0000840000")
    assert bound > 0


def test_an_interrupted_call_books_every_allowed_attempt(stub, monkeypatch):
    sessions: list[live.LiveSession] = []

    class Recording(live.LiveSession):
        def __init__(self, policy: live.LivePolicy) -> None:
            super().__init__(policy)
            sessions.append(self)

    def interrupted(request: httpx2.Request) -> httpx2.Response:
        raise KeyboardInterrupt

    def factory(policy: live.LivePolicy) -> openai.OpenAI:
        return openai.OpenAI(
            api_key=FAKE_API_KEY,
            http_client=httpx2.Client(transport=httpx2.MockTransport(interrupted)),
        )

    monkeypatch.setattr(live, "LiveSession", Recording)
    env = runner_env(stub.endpoint, LLMI_LIVE_MAX_RETRIES="2")
    with pytest.raises(KeyboardInterrupt):
        live_openai.main(
            ["--execute"],
            env,
            now=NOW,
            client_factory=factory,
            provenance="synthetic",
            out=io.StringIO(),
        )
    (session,) = sessions
    snap = session.snapshot()
    assert Decimal(str(snap["outstanding_usd"])) == 0
    assert Decimal(str(snap["booked_usd"])) > 0  # all three attempts at their bound


def test_the_real_client_factory_pins_the_endpoint_and_disables_sdk_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.invalid/v1")
    decision = live.evaluate_policy(live_env(), now=NOW)
    assert decision.policy is not None
    client = live_openai._default_client({"OPENAI_API_KEY": FAKE_API_KEY})(decision.policy)
    assert str(client.base_url) == "https://api.openai.com/v1/"
    assert client.max_retries == 0 and client.timeout == decision.policy.timeout_seconds
    assert FAKE_API_KEY not in repr(client.base_url)
