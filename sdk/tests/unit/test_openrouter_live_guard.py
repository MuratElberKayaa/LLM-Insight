"""OpenRouter free-model guard and endpoint boundary (ADR-47), fully offline.

The real client shape is used (`build_openrouter_http_client`), so the real boundary code runs, and
only `httpx.HTTPTransport.handle_request` is replaced by a SYNTHETIC fake (`wire`). `offline` fails
any test that attempts a non-loopback connection. The three locks are lifted only inside the
`released` fixture; `test_as_shipped_*` restore the values the module actually ships with.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from llm_insight_sdk import groq_live, openrouter_adapter, openrouter_live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.groq_adapter import _SENT_HEADERS
from llm_insight_sdk.live import LiveRefusal
from llm_insight_sdk.openrouter_adapter import (
    OPENROUTER_TIMEOUT,
    ObservedOpenRouterClient,
    OpenRouterTextAdapter,
    build_openrouter_http_client,
)
from llm_insight_sdk.openrouter_live import (
    FreeModel,
    FreeModelSession,
    evaluate_free_model_policy,
    send_boundary_problems,
)
from llm_insight_sdk.providers import TextRequest
from tests.conftest import PROMPT_SENTINEL
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
from tests.unit.test_instrumentation import exported

SHIPPED_LOCK = openrouter_adapter.REAL_EXECUTION_ENABLED
SHIPPED_MODELS_AT = openrouter_live.FREE_MODEL_EVIDENCE_VERIFIED_AT
SHIPPED_ROUTING_AT = openrouter_live.ZERO_PRICE_ROUTING_VERIFIED_AT
SHIPPED_MODELS = dict(openrouter_live.FREE_MODELS)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TODAY = "2026-11-11"
ENV: dict[str, str] = {
    "LLMI_PROVIDER_MODE": "real",
    "LLMI_OPENROUTER_MODE": "free-models",
    "LLMI_LIVE_CONSENT": "openrouter-free-model-single-call",
    "OPENROUTER_API_KEY": FAKE_OPENROUTER_KEY,
    "LLMI_OPENROUTER_ACCOUNT_STATE": "no-purchased-credits",
    "LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": TODAY,
    "LLMI_LIVE_PROVIDERS": "openrouter",
    "LLMI_LIVE_MODELS": MODEL,
    "LLMI_LIVE_MAX_REQUESTS": "1",
    "LLMI_LIVE_MAX_INPUT_TOKENS": "1024",
    "LLMI_LIVE_MAX_OUTPUT_TOKENS": "256",
}
SECRETS = (*SENTINELS, PROMPT_SENTINEL)


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


def release(monkeypatch) -> None:
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(openrouter_live, "FREE_MODEL_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(openrouter_live, "ZERO_PRICE_ROUTING_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        openrouter_live,
        "FREE_MODELS",
        {MODEL: FreeModel(MODEL, ("https://example.invalid/synthetic",))},
    )


@pytest.fixture(autouse=True)
def released(monkeypatch) -> None:
    """Test-only release of all three locks, and a process slot of this test's own."""
    release(monkeypatch)
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


@pytest.fixture
def wire(monkeypatch) -> FakeOpenRouter:
    fake = FakeOpenRouter([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


@pytest.fixture
def live_http() -> Iterator[httpx.Client]:
    http = build_openrouter_http_client()
    yield http
    http.close()


def env_with(**changes: str | None) -> dict[str, str]:
    env = dict(ENV)
    for name, value in changes.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def decide(env: dict[str, str] | None = None, now: datetime = NOW):
    return evaluate_free_model_policy(ENV if env is None else env, now=now)


def codes(refused: Any) -> set[str]:
    return {r.code for r in refused.refusals}


def session(env: dict[str, str] | None = None, now: datetime = NOW) -> FreeModelSession:
    return FreeModelSession(dict(ENV) if env is None else env, clock=lambda: now)


def guarded(
    telemetry, http: httpx.Client, gate: FreeModelSession, key: str = FAKE_OPENROUTER_KEY
) -> OpenRouterTextAdapter:
    return OpenRouterTextAdapter(
        ObservedOpenRouterClient(
            telemetry, key, http_client=http, provenance="real", send_gate=gate
        )
    )


def text_request(**kw: Any) -> TextRequest:
    kw.setdefault("model", MODEL)
    kw.setdefault("input_text", PROMPT_SENTINEL)
    kw.setdefault("max_output_tokens", 64)
    return TextRequest(**kw)


def assert_untouched(fake: FakeOpenRouter, gate: FreeModelSession) -> None:
    assert fake.requests == []
    assert gate.snapshot() == {
        "requests_started": 0,
        "max_requests": 1,
        "physical_attempts": 0,
        "provider_request_state": "not_sent",
        "halted": None,
    }


def pinned_request(http: httpx.Client, body: object | None = None) -> httpx.Request:
    content = openrouter_adapter.request_body(MODEL, "x", 64) if body is None else body
    return http.build_request(
        "POST",
        ENDPOINT,
        content=json.dumps(content).encode(),
        headers={
            "Authorization": f"Bearer {FAKE_OPENROUTER_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": "llm-insight-sdk/test",
        },
        timeout=OPENROUTER_TIMEOUT,
    )


# ---------------------------------------------------------------- policy: the control
def test_a_complete_environment_is_allowed():
    decision = decide()
    assert decision.allowed and decision.refusals == ()
    policy = decision.policy
    assert policy is not None
    assert (policy.model, policy.max_requests, policy.max_retries, policy.max_concurrency) == (
        MODEL,
        1,
        0,
        1,
    )
    assert (policy.max_input_tokens, policy.max_output_tokens) == (1024, 256)
    assert policy.timeout_seconds == openrouter_adapter.TOTAL_DEADLINE_SECONDS


def test_an_api_key_alone_enables_nothing():
    decision = decide({"OPENROUTER_API_KEY": FAKE_OPENROUTER_KEY})
    assert not decision.allowed and decision.policy is None
    assert {"provider_mode_not_real", "openrouter_mode_not_free", "consent_missing"} <= codes(
        decision
    )


# ---------------------------------------------------------------- as shipped: three locks
@pytest.fixture
def as_shipped(monkeypatch) -> None:
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", SHIPPED_LOCK)
    monkeypatch.setattr(openrouter_live, "FREE_MODEL_EVIDENCE_VERIFIED_AT", SHIPPED_MODELS_AT)
    monkeypatch.setattr(openrouter_live, "ZERO_PRICE_ROUTING_VERIFIED_AT", SHIPPED_ROUTING_AT)
    monkeypatch.setattr(openrouter_live, "FREE_MODELS", SHIPPED_MODELS)


def test_as_shipped_nothing_is_released():
    assert SHIPPED_LOCK is False
    assert SHIPPED_MODELS_AT is None and SHIPPED_ROUTING_AT is None
    assert SHIPPED_MODELS == {}


@pytest.mark.usefixtures("as_shipped")
def test_as_shipped_a_perfect_environment_is_refused():
    assert codes(decide()) == {
        "real_execution_disabled",
        "free_model_evidence_missing",
        "free_routing_evidence_missing",
        "model_not_free_verified",
    }


@pytest.mark.usefixtures("as_shipped")
def test_as_shipped_a_real_client_cannot_be_built(telemetry, live_http):
    with pytest.raises(ConfigError, match="not enabled"):
        guarded(telemetry, live_http, session())


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("REAL_EXECUTION_ENABLED", False, "real_execution_disabled"),
        ("FREE_MODEL_EVIDENCE_VERIFIED_AT", None, "free_model_evidence_missing"),
        ("ZERO_PRICE_ROUTING_VERIFIED_AT", None, "free_routing_evidence_missing"),
    ],
)
def test_each_lock_alone_refuses(monkeypatch, name, value, code):
    module = openrouter_adapter if name == "REAL_EXECUTION_ENABLED" else openrouter_live
    monkeypatch.setattr(module, name, value)
    assert codes(decide()) == {code}


def test_the_groq_lock_and_evidence_do_not_release_openrouter(monkeypatch):
    """Releasing Groq is a separate decision: it opens nothing here."""
    monkeypatch.setattr(openrouter_adapter, "REAL_EXECUTION_ENABLED", False)
    monkeypatch.setattr(groq_live.groq_adapter, "REAL_EXECUTION_ENABLED", True)
    assert "real_execution_disabled" in codes(decide())


# ---------------------------------------------------------------- policy: each refusal alone
SINGLE_REFUSALS = [
    pytest.param({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real", id="mode"),
    pytest.param({"LLMI_OPENROUTER_MODE": "paid"}, "openrouter_mode_not_free", id="paid-mode"),
    pytest.param({"LLMI_OPENROUTER_MODE": None}, "openrouter_mode_not_free", id="no-mode"),
    pytest.param(
        {"LLMI_LIVE_CONSENT": "groq-free-plan-single-call"}, "consent_missing", id="consent"
    ),
    pytest.param({"OPENROUTER_API_KEY": None}, "api_key_missing", id="no-key"),
    pytest.param(
        {"OPENROUTER_API_KEY": "gsk_" + "a" * 40}, "api_key_wrong_provider", id="groq-key"
    ),
    pytest.param(
        {"OPENROUTER_API_KEY": "sk" + "-" + "proj" + "-" + "a" * 30},
        "api_key_wrong_provider",
        id="openai",
    ),
    pytest.param({"OPENROUTER_API_KEY": "AIza" + "a" * 35}, "api_key_wrong_provider", id="google"),
    pytest.param({"OPENROUTER_API_KEY": "lli_" + "a" * 30}, "api_key_wrong_provider", id="ingest"),
    pytest.param({"OPENROUTER_API_KEY": "has a space"}, "api_key_invalid", id="key-shape"),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_STATE": None}, "account_state_unverified", id="no-state"
    ),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_STATE": "has-credits"}, "account_not_free_only", id="credits"
    ),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": None}, "account_state_unverified", id="no-date"
    ),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": "11/11/2026"}, "account_state_unverified", id="fmt"
    ),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": "2026-02-30"}, "account_state_unverified", id="day"
    ),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": "2026-11-12"},
        "account_attestation_invalid",
        id="fut",
    ),
    pytest.param(
        {"LLMI_OPENROUTER_ACCOUNT_CHECKED_ON": "2026-11-09"}, "account_attestation_stale", id="old"
    ),
    pytest.param({"LLMI_LIVE_PROVIDERS": "groq"}, "provider_not_allowed", id="provider"),
    pytest.param(
        {"LLMI_LIVE_PROVIDERS": "openrouter,groq"}, "provider_not_allowed", id="two-providers"
    ),
    pytest.param({"LLMI_LIVE_PROVIDERS": None}, "setting_missing", id="no-provider"),
    pytest.param({"LLMI_LIVE_MODELS": f"{MODEL},{MODEL}x"}, "model_list_not_single", id="list"),
    pytest.param({"LLMI_LIVE_MODELS": PAID_MODEL}, "model_not_free_variant", id="paid-model"),
    pytest.param({"LLMI_LIVE_MODELS": "openrouter/free"}, "model_not_free_variant", id="router"),
    pytest.param(
        {"LLMI_LIVE_MODELS": "synthetic-org/other:free"}, "model_not_free_verified", id="unlisted"
    ),
    pytest.param({"LLMI_LIVE_MAX_REQUESTS": "2"}, "setting_invalid", id="requests"),
    pytest.param({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "2048"}, "setting_invalid", id="output"),
    pytest.param({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "8"}, "setting_invalid", id="output-low"),
    pytest.param({"LLMI_LIVE_MAX_INPUT_TOKENS": "4096"}, "setting_invalid", id="input"),
    pytest.param({"LLMI_LIVE_TIMEOUT_SECONDS": "60"}, "setting_invalid", id="timeout"),
    pytest.param({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid", id="retries"),
    pytest.param({"LLMI_LIVE_MAX_CONCURRENCY": "2"}, "setting_invalid", id="concurrency"),
]


@pytest.mark.parametrize(("changes", "code"), SINGLE_REFUSALS)
def test_each_refusal_alone_blocks(changes, code):
    decision = decide(env_with(**changes))
    assert not decision.allowed
    assert code in codes(decision)
    text = json.dumps([r.message for r in decision.refusals])
    for secret in SECRETS:
        assert secret not in text
    for value in changes.values():
        if value and len(value) > 12:  # long values are never echoed
            assert value not in text


def test_the_setting_codes_match_the_shared_reader():
    """`setting_invalid` and `setting_missing` are the codes the shared `_Reader` emits."""
    assert "setting_invalid" in codes(decide(env_with(LLMI_LIVE_MAX_RETRIES="x")))


@pytest.mark.parametrize("var", openrouter_live.CLIENT_ENV_OVERRIDES)
def test_each_endpoint_override_variable_is_refused(var):
    decision = decide(env_with(**{var: "http://127.0.0.1:9"}))
    assert codes(decision) == {"client_env_override"}


def test_the_openrouter_key_shape_is_accepted_but_not_required():
    assert decide(env_with(OPENROUTER_API_KEY="sk-or-v1-" + "0" * 24)).allowed
    assert decide(env_with(OPENROUTER_API_KEY="synthetic-other-shape-" + "0" * 20)).allowed


def test_an_allowlisted_model_must_still_be_a_free_variant(monkeypatch):
    monkeypatch.setattr(openrouter_live, "FREE_MODELS", {PAID_MODEL: FreeModel(PAID_MODEL, ("x",))})
    assert "model_not_free_variant" in codes(decide(env_with(LLMI_LIVE_MODELS=PAID_MODEL)))


@pytest.mark.parametrize(
    ("name", "at", "code"),
    [
        ("FREE_MODEL_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=8), "free_model_evidence_stale"),
        (
            "FREE_MODEL_EVIDENCE_VERIFIED_AT",
            NOW + timedelta(hours=1),
            "free_model_evidence_not_yet_valid",
        ),
        ("ZERO_PRICE_ROUTING_VERIFIED_AT", NOW - timedelta(days=8), "free_routing_evidence_stale"),
        (
            "ZERO_PRICE_ROUTING_VERIFIED_AT",
            NOW + timedelta(hours=1),
            "free_routing_evidence_not_yet_valid",
        ),
    ],
)
def test_evidence_expires_after_seven_days(monkeypatch, name, at, code):
    monkeypatch.setattr(openrouter_live, name, at)
    assert codes(decide()) == {code}


def test_a_clock_without_a_timezone_is_refused():
    assert codes(decide(now=datetime(2026, 11, 11, 12, 0))) == {"clock_invalid"}


def test_the_attestation_date_is_taken_in_utc():
    late = datetime(2026, 11, 12, 1, 0, tzinfo=timezone(timedelta(hours=3)))  # 22:00 UTC on 11-11
    assert decide(now=late).allowed


# ---------------------------------------------------------------- the one request
def test_one_guarded_request_is_sent_and_recorded(telemetry, stub, live_http, wire):
    wire.steps.append(ok())
    gate = session()
    outcome = guarded(telemetry, live_http, gate).generate_text(text_request())
    assert outcome.status == "completed"
    assert len(wire.requests) == 1
    body = wire.request_json(0)
    assert body["provider"] == {
        "allow_fallbacks": False,
        "data_collection": "deny",
        "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
    }
    assert "models" not in body and "route" not in body
    assert gate.snapshot() == {
        "requests_started": 1,
        "max_requests": 1,
        "physical_attempts": 1,
        "provider_request_state": "sent",
        "halted": None,
    }
    (span,) = [s for s in exported(telemetry, stub) if s["kind"] == "CLIENT"]
    assert span["attributes"]["llminsight.provenance"] == "real"
    assert span["attributes"]["gen_ai.provider.name"] == "openrouter"
    assert json.dumps(span).count(PROMPT_SENTINEL) == 0


def test_a_second_request_is_refused_in_the_same_session(telemetry, live_http, wire):
    wire.steps.extend([ok(), ok()])
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    adapter.generate_text(text_request())
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1


def test_the_process_slot_is_shared_with_the_groq_guard(telemetry, live_http, wire):
    """After one real OpenRouter request, the process cannot make a Groq one, and vice versa."""
    wire.steps.append(ok())
    guarded(telemetry, live_http, session()).generate_text(text_request())
    assert groq_live._PROCESS_SLOT.locked()
    with pytest.raises(LiveRefusal) as refused:
        groq_live.FreePlanSession({})._reserve()
    assert codes(refused.value) == {"request_limit_reached"}


def test_a_slot_taken_by_groq_refuses_openrouter(telemetry, live_http, wire):
    assert groq_live._PROCESS_SLOT.acquire(blocking=False)
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert_untouched(wire, gate)


def test_the_environment_is_read_again_at_the_send(telemetry, live_http, wire):
    env = dict(ENV)
    gate = FreeModelSession(env, clock=lambda: NOW)
    adapter = guarded(telemetry, live_http, gate)
    env["LLMI_LIVE_CONSENT"] = ""
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"consent_missing"}
    assert_untouched(wire, gate)


def test_the_clock_is_read_again_at_the_send(telemetry, live_http, wire):
    times = iter([NOW + timedelta(days=3)])
    gate = FreeModelSession(dict(ENV), clock=lambda: next(times))
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert "account_attestation_stale" in codes(refused.value)
    assert_untouched(wire, gate)


def test_a_client_built_with_another_key_is_refused_at_the_send(telemetry, live_http, wire):
    gate = session()
    other = "synthetic-openrouter-other-" + "1" * 24
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate, key=other).generate_text(text_request())
    assert codes(refused.value) == {"credential_not_bound"}
    assert other not in str(refused.value) and FAKE_OPENROUTER_KEY not in str(refused.value)
    assert_untouched(wire, gate)


def test_a_model_other_than_the_policys_is_refused_at_the_send(telemetry, live_http, wire):
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(
            text_request(model="synthetic-org/other:free")
        )
    assert codes(refused.value) == {"model_not_allowed"}
    assert_untouched(wire, gate)


def test_an_output_cap_above_the_policy_is_refused_at_the_send(telemetry, live_http, wire):
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request(max_output_tokens=512))
    assert codes(refused.value) == {"output_limit_exceeded"}
    assert_untouched(wire, gate)


def test_a_client_changed_after_construction_is_refused_at_the_send(telemetry, live_http, wire):
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    live_http._transport._pool._retries = 2
    with pytest.raises((LiveRefusal, ConfigError)):
        adapter.generate_text(text_request())
    assert_untouched(wire, gate)


def test_tls_key_logging_is_refused_at_the_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keys.log"))
    http = build_openrouter_http_client()
    try:
        assert "TLS key logging must be off" in send_boundary_problems(http, pinned_request(http))
    finally:
        http.close()


def test_the_pinned_request_has_no_boundary_problem(live_http):
    assert send_boundary_problems(live_http, pinned_request(live_http)) == []


@pytest.mark.parametrize(
    "url",
    [
        ENDPOINT.replace("https://", "http://"),
        ENDPOINT.replace("openrouter.ai", "openrouter.ai.evil.invalid"),
        ENDPOINT.replace("openrouter.ai", "openrouter.ai:8443"),
        ENDPOINT + "/extra",
        "https://api.groq.com/openai/v1/chat/completions",
    ],
)
def test_a_request_to_anywhere_else_is_a_boundary_problem(live_http, url):
    request = pinned_request(live_http)
    request.url = httpx.URL(url)
    assert "the request must be POST to the pinned HTTPS OpenRouter endpoint" in (
        send_boundary_problems(live_http, request)
    )


def test_an_extra_header_is_a_boundary_problem(live_http):
    request = pinned_request(live_http)
    request.headers["HTTP-Referer"] = "https://example.invalid"
    assert {name.lower() for name in request.headers} != _SENT_HEADERS
    assert "the request headers must be exactly the pinned set" in (
        send_boundary_problems(live_http, request)
    )


# ---------------------------------------------------------------- the exact body
def routed(**provider: Any) -> dict[str, Any]:
    body = openrouter_adapter.request_body(MODEL, "x", 64)
    body["provider"].update(provider)
    return body


BODY_VIOLATIONS = [
    pytest.param(routed(allow_fallbacks=True), id="fallbacks-on"),
    pytest.param(routed(allow_fallbacks=0), id="fallbacks-zero-not-false"),
    pytest.param(routed(data_collection="allow"), id="data-collection"),
    pytest.param(routed(max_price={"prompt": 0, "completion": 0}), id="price-partial"),
    pytest.param(
        routed(max_price={"prompt": 1, "completion": 0, "request": 0, "image": 0}), id="price"
    ),
    pytest.param(routed(order=["x"]), id="order"),
    pytest.param(routed(sort="price"), id="sort"),
    pytest.param(
        {**openrouter_adapter.request_body(MODEL, "x", 64), "models": [MODEL]}, id="models"
    ),
    pytest.param(
        {**openrouter_adapter.request_body(MODEL, "x", 64), "route": "fallback"}, id="route"
    ),
    pytest.param(
        {**openrouter_adapter.request_body(MODEL, "x", 64), "reasoning": {"exclude": False}},
        id="reasoning",
    ),
    pytest.param({**openrouter_adapter.request_body(MODEL, "x", 64), "stream": True}, id="stream"),
    pytest.param(
        {
            k: v
            for k, v in openrouter_adapter.request_body(MODEL, "x", 64).items()
            if k != "provider"
        },
        id="no-routing",
    ),
]


@pytest.mark.parametrize("body", BODY_VIOLATIONS)
def test_any_other_body_is_not_the_pinned_request(body):
    assert openrouter_live._read_body(pinned_request(build_openrouter_http_client(), body)) is None
    policy = decide().policy
    assert policy is not None
    assert [r.code for r in openrouter_live._body_refusals(None, policy)] == ["request_not_allowed"]


def test_the_pinned_body_is_read_back():
    request = pinned_request(build_openrouter_http_client())
    body = openrouter_live._read_body(request)
    assert body is not None and (body.model, body.max_output_tokens) == (MODEL, 64)


# ---------------------------------------------------------------- after the call
HALTS = [
    pytest.param(fail(402), "payment_required_indicator", id="402"),
    pytest.param(Step(200, error_body(402)), "payment_required_indicator", id="200-with-402"),
    pytest.param(ok(usage=usage_with(cost=0.0002)), "cost_not_zero", id="cost"),
    pytest.param(ok(usage=usage_with(cost=None)), "cost_unverified", id="no-cost"),
    pytest.param(ok(usage=usage_with(cost="0")), "cost_unverified", id="cost-text"),
    pytest.param(ok(usage=False), "cost_unverified", id="no-usage"),
    pytest.param(ok(usage=usage_with(is_byok=True)), "byok_used", id="byok"),
    pytest.param(ok(usage=usage_with(is_byok=None)), "byok_unverified", id="no-byok"),
    pytest.param(ok(model=PAID_MODEL), "response_model_mismatch", id="paid-model"),
    pytest.param(
        ok(usage=usage_with(prompt_tokens=5000, total_tokens=5040)),
        "usage_exceeded_bound",
        id="usage",
    ),
    pytest.param(Step(302, {}), "redirect_refused", id="redirect"),
]


@pytest.mark.parametrize(("step", "halt"), HALTS)
def test_a_post_call_indicator_halts_and_never_resends(telemetry, live_http, wire, step, halt):
    wire.steps.extend([step, ok()])
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    adapter.generate_text(text_request())
    assert gate.snapshot()["halted"] == halt
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"session_halted"}
    assert len(wire.requests) == 1


@pytest.mark.parametrize(
    "step",
    [ok(), ok(finish="length"), fail(429), fail(503), Step(200, error_body(429)), fail(401)],
    ids=["ok", "length", "429", "503", "200-with-429", "401"],
)
def test_nothing_halts_on_an_ordinary_response(telemetry, live_http, wire, step):
    wire.steps.append(step)
    gate = session()
    guarded(telemetry, live_http, gate).generate_text(text_request())
    assert gate.snapshot()["halted"] is None


@pytest.mark.parametrize(
    ("exc", "state"),
    [
        (httpx.ConnectError, "not_sent"),
        (httpx.ConnectTimeout, "not_sent"),
        (httpx.ReadTimeout, "possibly_sent"),
        (httpx.RemoteProtocolError, "possibly_sent"),
    ],
)
def test_the_request_state_is_claimed_only_when_proven(telemetry, live_http, wire, exc, state):
    wire.steps.append(Step(raise_=exc))
    gate = session()
    outcome = guarded(telemetry, live_http, gate).generate_text(text_request())
    assert outcome.status == "error"
    snapshot = gate.snapshot()
    assert snapshot["provider_request_state"] == state and snapshot["physical_attempts"] == 1
    with pytest.raises(LiveRefusal):  # proven unsent or not, nothing reopens
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert len(wire.requests) == 1


def test_an_interruption_during_the_send_is_possibly_sent(telemetry, live_http, wire):
    wire.steps.append(Step(raise_=KeyboardInterrupt))
    gate = session()
    with pytest.raises(KeyboardInterrupt):
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert gate.snapshot()["provider_request_state"] == "possibly_sent"


def test_no_refusal_or_snapshot_holds_a_secret(telemetry, live_http, wire):
    gate = session(env_with(LLMI_LIVE_CONSENT="x"))
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    text = json.dumps([str(refused.value), gate.snapshot()])
    for secret in SECRETS:
        assert secret not in text


# ---------------------------------------------------------------- the operator template
def test_every_operator_setting_is_listed_in_the_env_example():
    from pathlib import Path

    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text(encoding="utf-8")
    example = example.replace("\r\n", "\n")
    for name in (
        "LLMI_OPENROUTER_MODE",
        "LLMI_OPENROUTER_ACCOUNT_STATE",
        "LLMI_OPENROUTER_ACCOUNT_CHECKED_ON",
        "OPENROUTER_API_KEY",
    ):
        assert f"\n{name}=\n" in example  # listed, and empty
    assert "No runner exists" not in example  # R127: the Groq comment is current
    assert openrouter_live.CONSENT_PHRASE in example and openrouter_live.ACCOUNT_STATE in example
