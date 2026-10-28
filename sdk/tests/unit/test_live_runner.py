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
    REQUEST_ID_SENTINEL,
    FakeOpenAI,
    Step,
    block_network,
    fail,
    quota_exhausted,
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
    assert REQUEST_ID_SENTINEL not in text
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


# ---------------------------------------------------------------- Phase 6B preflight
# The operator configuration of the first real request (reports/phase-6b-preflight.md §10).
PHASE6B_PROFILE = {
    "LLMI_LIVE_MAX_REQUESTS": "1",
    "LLMI_LIVE_MAX_INPUT_TOKENS": "256",
    "LLMI_LIVE_MAX_OUTPUT_TOKENS": "64",
    "LLMI_LIVE_MAX_REQUEST_COST_USD": "0.01",
    "LLMI_REAL_LLM_BUDGET_USD": "0.02",
    "LLMI_LIVE_TIMEOUT_SECONDS": "30",
    "LLMI_LIVE_MAX_RETRIES": "0",
    "LLMI_LIVE_MAX_CONCURRENCY": "1",
}
# (69 UTF-8 bytes + 64 framing) * 0.75 + 64 * 4.50, per 1M tokens, rounded up
PHASE6B_WORST_CASE = Decimal("0.0003877500")


def test_phase6b_profile_is_one_attempt_and_far_below_every_ceiling(stub):
    code, doc = run([], runner_env(stub.endpoint, **PHASE6B_PROFILE), client_factory=NeverCalled())
    assert code == 0 and doc["execution_allowed"] is True and doc["provider_request_sent"] is False
    plan = doc["plan"]
    assert (plan["max_requests"], plan["max_attempts"], plan["max_output_tokens"]) == (1, 1, 64)
    assert plan["input_token_bound"] == len(live_openai.INPUT_TEXT.encode()) + 64 == 133
    worst = Decimal(plan["worst_case_request_usd"])
    assert worst == PHASE6B_WORST_CASE
    assert worst < Decimal(plan["max_request_cost_usd"]) < Decimal(plan["session_budget_usd"])
    assert Decimal(plan["session_budget_usd"]) <= Decimal("0.10")
    policy = live.evaluate_policy(runner_env(stub.endpoint, **PHASE6B_PROFILE), now=NOW).policy
    assert policy is not None and policy.max_concurrency == 1
    # Even a full-size request (every allowed input token) stays far below the ceilings.
    full = live.request_bound(policy, live.VERIFIED_PRICES[("openai", "gpt-5.4-mini")], 256)
    assert full == Decimal("0.0004800000")


def test_a_second_request_in_the_profile_session_is_refused():
    policy = live.evaluate_policy(live_env(**PHASE6B_PROFILE), now=NOW).policy
    assert policy is not None
    session = live.LiveSession(policy)
    kw = {"provider": "openai", "model": "gpt-5.4-mini", "max_output_tokens": 64}
    first = session.reserve(input_text=live_openai.INPUT_TEXT, **kw)
    with pytest.raises(live.LiveRefusal) as refused:
        session.reserve(input_text=live_openai.INPUT_TEXT, **kw)
    assert {r.code for r in refused.value.refusals} == {"request_limit_reached"}
    session.settle(first, [])
    with pytest.raises(live.LiveRefusal):
        session.reserve(input_text=live_openai.INPUT_TEXT, **kw)


INCOMPLETE = {"status": "incomplete", "incomplete_reason": "max_output_tokens"}
SMALL_COST = "0.0000840000"
HTML_BODY = Step(200, raw=b"<html>SYNTHETIC</html>", content_type="text/html")
TRUNCATED_JSON = Step(200, raw=b'{"id": "resp_SYNTHETIC", "usage": ')


@pytest.mark.parametrize(
    ("step", "rc", "status", "category", "cost", "span"),
    [
        pytest.param(
            response_ok(usage=SMALL_USAGE, **INCOMPLETE),
            *(0, "incomplete", None, SMALL_COST, "UNSET"),
            id="incomplete",
        ),
        pytest.param(
            response_ok(usage=SMALL_USAGE, status="failed"),
            *(1, "failed", "response_failed", SMALL_COST, "ERROR"),
            id="failed",
        ),
        pytest.param(
            HTML_BODY, *(1, "malformed", "malformed_response", None, "ERROR"), id="html_body"
        ),
        pytest.param(
            TRUNCATED_JSON, *(1, "error", "malformed_response", None, "ERROR"), id="invalid_json"
        ),
        pytest.param(
            response_ok(usage=None), *(0, "completed", None, None, "UNSET"), id="missing_usage"
        ),
        pytest.param(
            response_ok(usage=SMALL_USAGE, response_id=None),
            *(0, "completed", None, SMALL_COST, "UNSET"),
            id="missing_id",
        ),
    ],
)
def test_response_integrity_is_reported_as_it_is(stub, step, rc, status, category, cost, span):
    fake = FakeOpenAI([step])
    env = runner_env(stub.endpoint)
    code, doc = run(["--execute"], env, client_factory=lambda p: fake.client())
    assert code == rc and len(fake.requests) == 1 and doc["attempts"] == 1
    assert doc["status"] == status  # an incomplete answer is never reported as completed
    assert doc["incomplete_reason"] == ("max_output_tokens" if status == "incomplete" else None)
    assert doc["error_category"] == category and doc["estimated_cost_usd"] == cost
    booked = Decimal(doc["booked_against_budget_usd"])
    if cost is None:  # unknown is booked at the reserved bound, never 0
        assert doc["tokens"] is None
        assert booked == Decimal(doc["plan"]["worst_case_request_usd"]) > 0
    else:
        assert booked == Decimal(cost)
    assert doc["telemetry"]["flushed"] is True
    spans = stub.spans()
    assert len(spans) == 2  # one root, one attempt: nothing duplicated
    (client,) = [s for s in spans if s["kind"] == "CLIENT"]
    assert client["trace_id"] == doc["trace_id"] and client["status"] == span
    if step.body is not None and "id" not in step.body:
        assert "gen_ai.response.id" not in client["attributes"]
    if status == "incomplete":
        assert client["attributes"]["gen_ai.response.finish_reasons"] == ["max_output_tokens"]


@pytest.mark.parametrize(
    ("step", "category"),
    [
        (Step(raise_=httpx2.ReadTimeout), "timeout"),
        (Step(raise_=httpx2.ConnectError), "connection_error"),
        (fail(500, "server_error"), "provider_error"),
        (fail(503, "server_error"), "provider_error"),
        (fail(403, "permission_denied"), "permission_denied"),
        (quota_exhausted(), "quota_exhausted"),
        (Step(200, raw=b"{", content_type="application/json"), "malformed_response"),
    ],
)
def test_ambiguous_or_final_failures_are_never_retried_even_with_retries_allowed(
    stub, step, category
):
    fake = FakeOpenAI([step])  # a second physical request would fail the fake
    env = runner_env(stub.endpoint, LLMI_LIVE_MAX_RETRIES="2")
    rc, doc = run(["--execute"], env, client_factory=lambda p: fake.client())
    assert rc == 1 and len(fake.requests) == 1 and doc["attempts"] == 1
    assert doc["error_category"] == category
    # One physical attempt booked at its bound; the other reserved attempts are released.
    per_attempt = Decimal(doc["plan"]["worst_case_request_usd"]) / 3
    assert Decimal(doc["booked_against_budget_usd"]) == per_attempt
    assert doc["session"]["outstanding_usd"] == "0.0000000000"  # fixed-point, never "0E-10"


def test_the_runner_never_uses_the_legacy_chat_completions_path(stub, monkeypatch):
    from llm_insight_sdk.openai_adapter import ObservedOpenAIClient

    def forbidden(*args, **kwargs):
        raise AssertionError("R80: the live runner must not call Chat Completions")

    monkeypatch.setattr(ObservedOpenAIClient, "create_chat_completion", forbidden)
    fake = FakeOpenAI([response_ok(usage=SMALL_USAGE)])
    rc, doc = run(["--execute"], runner_env(stub.endpoint), client_factory=lambda p: fake.client())
    assert rc == 0 and doc["status"] == "completed"
    (request,) = fake.requests
    assert request.method == "POST" and request.url.path == "/v1/responses"
    source = Path(live_openai.__file__).read_text(encoding="utf-8")
    assert "chat.completions" not in source and "create_chat_completion" not in source


@pytest.mark.parametrize("var", ["OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"])
def test_execute_is_refused_when_the_environment_could_swap_credential_or_project(stub, var):
    never = NeverCalled()
    env = runner_env(stub.endpoint, **{var: "Authorization: Bearer SYNTHETIC-other"})
    rc, doc = run(["--execute"], env, client_factory=never)
    assert rc == 2 and never.calls == 0 and doc["provider_request_sent"] is False
    assert "client_env_override" in {r["code"] for r in doc["refusals"]}
    assert "SYNTHETIC-other" not in json.dumps(doc)


def test_custom_headers_env_would_replace_the_guarded_key(monkeypatch):
    """Pins the `openai` 3.28.0 behaviour the `client_env_override` refusal exists for."""
    seen: list[httpx2.Headers] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers)
        return httpx2.Response(500, json={"error": {"message": "SYNTHETIC"}})

    other = "Bearer SYNTHETIC-other"  # short: not key-shaped for the secret scan
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", f"Authorization: {other}\nOpenAI-Project: proj_X")
    client = openai.OpenAI(
        api_key=FAKE_API_KEY,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(openai.InternalServerError):
        client.responses.create(model="gpt-5.4-mini", input="SYNTHETIC", store=False)
    (headers,) = seen  # max_retries=0: exactly one physical request, even for a 500
    assert headers["authorization"] == other and headers["openai-project"] == "proj_X"
