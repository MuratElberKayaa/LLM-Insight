"""Groq Free Plan guard and endpoint boundary (Phase 8A-3, ADR-44), fully offline.

The real client shape is used (`build_groq_http_client`), so the real boundary code runs, and only
`httpx.HTTPTransport.handle_request` is replaced by a SYNTHETIC fake (`wire`). `offline` fails any
test that attempts a non-loopback connection. The execution lock and the evidence are lifted only
inside the fixtures below; `test_as_shipped_*` restore the values the module actually ships with.
"""

from __future__ import annotations

import inspect
import json
import logging
import socket
import ssl
import threading
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import certifi
import httpcore
import httpx
import pytest

from llm_insight_sdk import groq_adapter, groq_live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.groq_adapter import (
    GROQ_TIMEOUT,
    GroqTextAdapter,
    ObservedGroqClient,
    build_groq_http_client,
)
from llm_insight_sdk.groq_live import (
    FreePlanModel,
    FreePlanSession,
    evaluate_free_plan_policy,
    send_boundary_problems,
)
from llm_insight_sdk.live import LiveRefusal
from llm_insight_sdk.providers import TextRequest
from tests.conftest import PROMPT_SENTINEL
from tests.groq_fixtures import (
    ENDPOINT,
    ERROR_SENTINEL,
    FAKE_GROQ_KEY,
    GENERATION_SENTINEL,
    MODEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    FakeGroq,
    Step,
    error_body,
    fail,
    ok,
)
from tests.openai_fixtures import block_network
from tests.unit.test_instrumentation import exported

REPO = Path(__file__).resolve().parents[3]
SHIPPED_LOCK = groq_adapter.REAL_EXECUTION_ENABLED
SHIPPED_VERIFIED_AT = groq_live.FREE_PLAN_EVIDENCE_VERIFIED_AT
SHIPPED_MODELS = dict(groq_live.FREE_PLAN_MODELS)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
TODAY = "2026-11-09"
ENV: dict[str, str] = {
    "LLMI_PROVIDER_MODE": "real",
    "LLMI_GROQ_MODE": "free-plan",
    "LLMI_LIVE_CONSENT": "groq-free-plan-single-call",
    "GROQ_API_KEY": FAKE_GROQ_KEY,
    "LLMI_GROQ_PLAN": "free",
    "LLMI_GROQ_PLAN_CHECKED_ON": TODAY,
    "LLMI_LIVE_PROVIDERS": "groq",
    "LLMI_LIVE_MODELS": MODEL,
    "LLMI_LIVE_MAX_REQUESTS": "1",
    "LLMI_LIVE_MAX_INPUT_TOKENS": "1024",
    "LLMI_LIVE_MAX_OUTPUT_TOKENS": "256",
}
SECRETS = (
    FAKE_GROQ_KEY,
    PROMPT_SENTINEL,
    OUTPUT_SENTINEL,
    REASONING_SENTINEL,
    ERROR_SENTINEL,
    GENERATION_SENTINEL,
)


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


@pytest.fixture(autouse=True)
def released(monkeypatch) -> None:
    """Test-only release of both locks, and a process slot of this test's own."""
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=1))
    monkeypatch.setattr(
        groq_live,
        "FREE_PLAN_MODELS",
        {MODEL: FreePlanModel(MODEL, ("https://example.invalid/synthetic",))},
    )
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())


@pytest.fixture
def wire(monkeypatch) -> FakeGroq:
    fake = FakeGroq([])
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: fake.handler(request)
    )
    return fake


@pytest.fixture
def live_http() -> Iterator[httpx.Client]:
    http = build_groq_http_client()
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
    return evaluate_free_plan_policy(ENV if env is None else env, now=now)


def codes(refused: Any) -> set[str]:
    return {r.code for r in refused.refusals}


def session(env: dict[str, str] | None = None, now: datetime = NOW) -> FreePlanSession:
    return FreePlanSession(dict(ENV) if env is None else env, clock=lambda: now)


def guarded(telemetry, http: httpx.Client, gate: FreePlanSession) -> GroqTextAdapter:
    return GroqTextAdapter(
        ObservedGroqClient(
            telemetry, FAKE_GROQ_KEY, http_client=http, provenance="real", send_gate=gate
        )
    )


def text_request(**kw: Any) -> TextRequest:
    kw.setdefault("model", MODEL)
    kw.setdefault("input_text", PROMPT_SENTINEL)
    kw.setdefault("max_output_tokens", 64)
    return TextRequest(**kw)


def client_spans(telemetry, stub) -> list[dict]:
    return [s for s in exported(telemetry, stub) if s["kind"] == "CLIENT"]


def assert_untouched(fake: FakeGroq, gate: FreePlanSession) -> None:
    assert fake.requests == []
    assert gate.snapshot() == {
        "requests_started": 0,
        "max_requests": 1,
        "physical_attempts": 0,
        "provider_request_state": "not_sent",
        "halted": None,
    }


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
    assert policy.timeout_seconds == groq_adapter.TOTAL_DEADLINE_SECONDS


def test_an_api_key_alone_enables_nothing():
    decision = decide({"GROQ_API_KEY": FAKE_GROQ_KEY})
    assert not decision.allowed and decision.policy is None
    assert {"provider_mode_not_real", "groq_mode_not_free_plan", "consent_missing"} <= codes(
        decision
    )


def test_the_real_environment_is_read_when_none_is_given(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    for name in groq_live.CLIENT_ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    assert evaluate_free_plan_policy(now=NOW).allowed


# ---------------------------------------------------------------- as shipped: disabled
@pytest.fixture
def as_shipped(monkeypatch) -> None:
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", SHIPPED_LOCK)
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", SHIPPED_VERIFIED_AT)
    monkeypatch.setattr(groq_live, "FREE_PLAN_MODELS", SHIPPED_MODELS)


def test_as_shipped_only_the_evidence_is_released():
    """Phase 8G evidence-only change: one model and its evidence date; execution stays locked."""
    assert SHIPPED_LOCK is False
    assert SHIPPED_VERIFIED_AT is not None
    assert SHIPPED_VERIFIED_AT.isoformat() == "2026-11-12T06:00:00+00:00"
    assert list(SHIPPED_MODELS) == [MODEL]


@pytest.mark.usefixtures("as_shipped")
def test_as_shipped_a_perfect_environment_is_refused():
    """Inside the released evidence window, the execution lock is the one remaining refusal."""
    assert SHIPPED_VERIFIED_AT is not None
    inside = SHIPPED_VERIFIED_AT + timedelta(hours=1)
    env = env_with(LLMI_GROQ_PLAN_CHECKED_ON=inside.date().isoformat())
    assert codes(decide(env, now=inside)) == {"real_execution_disabled"}


@pytest.mark.usefixtures("as_shipped")
def test_as_shipped_a_real_client_cannot_be_built(telemetry, live_http):
    with pytest.raises(ConfigError, match="not enabled"):
        guarded(telemetry, live_http, session())


def test_the_execution_lock_alone_refuses(monkeypatch):
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", False)
    assert codes(decide()) == {"real_execution_disabled"}


# ---------------------------------------------------------------- policy: each refusal alone
SINGLE_REFUSALS = [
    # provider mode, Groq mode: there is no paid mode
    pytest.param({"LLMI_PROVIDER_MODE": None}, "provider_mode_not_real", id="provider-mode-unset"),
    pytest.param({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real", id="provider-mode-mock"),
    pytest.param({"LLMI_GROQ_MODE": None}, "groq_mode_not_free_plan", id="groq-mode-unset"),
    pytest.param({"LLMI_GROQ_MODE": "paid"}, "groq_mode_not_free_plan", id="groq-mode-paid"),
    pytest.param(
        {"LLMI_GROQ_MODE": "developer"}, "groq_mode_not_free_plan", id="groq-mode-developer"
    ),
    pytest.param({"LLMI_GROQ_MODE": "FREE-PLAN"}, "groq_mode_not_free_plan", id="groq-mode-case"),
    # consent is specific to this provider
    pytest.param({"LLMI_LIVE_CONSENT": None}, "consent_missing", id="consent-unset"),
    pytest.param({"LLMI_LIVE_CONSENT": "yes"}, "consent_missing", id="consent-other"),
    pytest.param(
        {"LLMI_LIVE_CONSENT": "openai-paid-calls"}, "consent_missing", id="consent-openai"
    ),
    pytest.param(
        {"LLMI_LIVE_CONSENT": "gemini-free-tier-single-call"}, "consent_missing", id="consent-gem"
    ),
    pytest.param(
        {"LLMI_LIVE_CONSENT": "openrouter-free-model-single-call"},
        "consent_missing",
        id="consent-openrouter",
    ),
    pytest.param(
        {"LLMI_LIVE_CONSENT": "GROQ-FREE-PLAN-SINGLE-CALL"}, "consent_missing", id="consent-case"
    ),
    # the key
    pytest.param({"GROQ_API_KEY": None}, "api_key_missing", id="key-unset"),
    pytest.param({"GROQ_API_KEY": "  "}, "api_key_missing", id="key-blank"),
    pytest.param(
        {"GROQ_API_KEY": "sk-" + "a" * 24}, "api_key_wrong_provider", id="key-openai-shape"
    ),
    pytest.param(
        {"GROQ_API_KEY": "sk-or-v1-" + "a" * 24}, "api_key_wrong_provider", id="key-openrouter"
    ),
    pytest.param(
        {"GROQ_API_KEY": "AIza" + "a" * 24}, "api_key_wrong_provider", id="key-gemini-shape"
    ),
    pytest.param({"GROQ_API_KEY": "lli_" + "a" * 24}, "api_key_wrong_provider", id="key-project"),
    pytest.param({"GROQ_API_KEY": "has space"}, "api_key_invalid", id="key-with-space"),
    # the attestation
    pytest.param({"LLMI_GROQ_PLAN": None}, "free_plan_unverified", id="plan-unset"),
    pytest.param({"LLMI_GROQ_PLAN": "developer"}, "plan_not_free", id="plan-developer"),
    pytest.param({"LLMI_GROQ_PLAN": "paid"}, "plan_not_free", id="plan-paid"),
    pytest.param({"LLMI_GROQ_PLAN": "unknown"}, "plan_not_free", id="plan-unknown"),
    pytest.param({"LLMI_GROQ_PLAN": "Free"}, "plan_not_free", id="plan-case"),
    pytest.param({"LLMI_GROQ_PLAN_CHECKED_ON": None}, "free_plan_unverified", id="date-unset"),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "today"}, "free_plan_unverified", id="date-not-a-date"
    ),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "20261109"}, "free_plan_unverified", id="date-basic-format"
    ),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "2026-W45-1"}, "free_plan_unverified", id="date-iso-week"
    ),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "2026-13-40"}, "free_plan_unverified", id="date-impossible"
    ),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "٢٠٢٦-١١-٠٩"}, "free_plan_unverified", id="date-non-ascii"
    ),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "2026-11-10"},
        "free_plan_attestation_invalid",
        id="date-future",
    ),
    pytest.param(
        {"LLMI_GROQ_PLAN_CHECKED_ON": "2026-11-07"},
        "free_plan_attestation_stale",
        id="date-two-days-old",
    ),
    # provider and model
    pytest.param({"LLMI_LIVE_PROVIDERS": None}, "setting_missing", id="providers-unset"),
    pytest.param({"LLMI_LIVE_PROVIDERS": "openai"}, "provider_not_allowed", id="provider-other"),
    pytest.param(
        {"LLMI_LIVE_PROVIDERS": "groq,openrouter"}, "provider_not_allowed", id="provider-second"
    ),
    pytest.param({"LLMI_LIVE_MODELS": None}, "setting_missing", id="models-unset"),
    pytest.param(
        {"LLMI_LIVE_MODELS": "openai/gpt-oss-120b"},
        "model_not_free_plan_verified",
        id="model-unlisted",
    ),
    pytest.param(
        {"LLMI_LIVE_MODELS": "groq/compound"}, "model_not_free_plan_verified", id="model-compound"
    ),
    pytest.param(
        {"LLMI_LIVE_MODELS": MODEL.upper()}, "model_not_free_plan_verified", id="model-case"
    ),
    pytest.param(
        {"LLMI_LIVE_MODELS": f"{MODEL}:free"}, "model_not_free_plan_verified", id="model-suffix"
    ),
    # hard caps
    pytest.param({"LLMI_LIVE_MAX_REQUESTS": None}, "setting_missing", id="requests-unset"),
    pytest.param({"LLMI_LIVE_MAX_REQUESTS": "2"}, "setting_invalid", id="requests-two"),
    pytest.param({"LLMI_LIVE_MAX_REQUESTS": "0"}, "setting_invalid", id="requests-zero"),
    pytest.param({"LLMI_LIVE_MAX_INPUT_TOKENS": None}, "setting_missing", id="input-unset"),
    pytest.param({"LLMI_LIVE_MAX_INPUT_TOKENS": "1025"}, "setting_invalid", id="input-over"),
    pytest.param({"LLMI_LIVE_MAX_INPUT_TOKENS": "0"}, "setting_invalid", id="input-zero"),
    pytest.param({"LLMI_LIVE_MAX_OUTPUT_TOKENS": None}, "setting_missing", id="output-unset"),
    pytest.param({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "1025"}, "setting_invalid", id="output-over"),
    pytest.param({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "15"}, "setting_invalid", id="output-under"),
    pytest.param({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid", id="retries-one"),
    pytest.param({"LLMI_LIVE_MAX_CONCURRENCY": "2"}, "setting_invalid", id="concurrency-two"),
    pytest.param({"LLMI_LIVE_TIMEOUT_SECONDS": "31"}, "setting_invalid", id="timeout-over"),
    pytest.param({"LLMI_LIVE_TIMEOUT_SECONDS": "5"}, "setting_invalid", id="timeout-ignored"),
    pytest.param({"LLMI_LIVE_TIMEOUT_SECONDS": "nan"}, "setting_invalid", id="timeout-nan"),
]


@pytest.mark.parametrize(("changes", "code"), SINGLE_REFUSALS)
def test_each_setting_alone_refuses(changes, code):
    decision = decide(env_with(**changes))
    assert not decision.allowed
    assert codes(decision) == {code}


@pytest.mark.parametrize(("changes", "code"), SINGLE_REFUSALS)
def test_each_setting_refused_at_the_send_makes_no_provider_call(
    telemetry, stub, live_http, wire, changes, code
):
    gate = session(env_with(**changes))
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert codes(refused.value) == {code}
    assert_untouched(wire, gate)
    assert client_spans(telemetry, stub) == []


def test_two_models_are_refused_so_no_fallback_list_can_exist():
    decision = decide(env_with(LLMI_LIVE_MODELS=f"{MODEL},other/model"))
    assert "model_list_not_single" in codes(decision) and decision.policy is None


def test_retries_and_concurrency_default_to_their_only_allowed_value():
    policy = decide(env_with(LLMI_LIVE_MAX_RETRIES=None, LLMI_LIVE_MAX_CONCURRENCY=None)).policy
    assert policy is not None and (policy.max_retries, policy.max_concurrency) == (0, 1)


def test_the_fixed_timeout_may_be_stated_exactly():
    assert decide(env_with(LLMI_LIVE_TIMEOUT_SECONDS="30")).allowed


@pytest.mark.parametrize("var", groq_live.CLIENT_ENV_OVERRIDES)
def test_each_routing_variable_refuses(telemetry, stub, live_http, wire, var):
    env = env_with(**{var: "https://collector.example.invalid:1/"})
    assert codes(decide(env)) == {"client_env_override"}
    gate = session(env)
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert var in str(refused.value) and "collector" not in str(refused.value)
    assert_untouched(wire, gate)


def test_the_required_routing_variables_are_all_listed():
    """Written out: the test above takes its parameters from the list it is checking."""
    assert set(groq_live.CLIENT_ENV_OVERRIDES) >= {
        "GROQ_BASE_URL",
        "OPENROUTER_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "SSLKEYLOGFILE",
    }


def test_no_proxy_alone_does_not_refuse():
    assert decide(env_with(NO_PROXY="127.0.0.1")).allowed


def test_a_one_day_old_attestation_is_accepted():
    assert decide(env_with(LLMI_GROQ_PLAN_CHECKED_ON="2026-11-08")).allowed


def test_the_attestation_date_is_compared_in_utc():
    fourteen = timezone(timedelta(hours=14))
    local = datetime(2026, 11, 10, 1, 0, tzinfo=fourteen)  # 2026-11-09 11:00 UTC
    assert decide(env_with(LLMI_GROQ_PLAN_CHECKED_ON="2026-11-09"), now=local).allowed
    assert codes(decide(env_with(LLMI_GROQ_PLAN_CHECKED_ON="2026-11-10"), now=local)) == {
        "free_plan_attestation_invalid"
    }


def test_a_clock_without_a_timezone_is_refused():
    assert codes(decide(now=datetime(2026, 11, 9, 12, 0))) == {"clock_invalid"}  # noqa: DTZ001


# ---------------------------------------------------------------- evidence
def test_missing_evidence_refuses(monkeypatch):
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", None)
    assert codes(decide()) == {"free_plan_evidence_missing"}


@pytest.mark.parametrize(
    ("age", "code"),
    [
        pytest.param(timedelta(days=14), None, id="exactly-the-limit"),
        pytest.param(timedelta(days=14, seconds=1), "free_plan_evidence_stale", id="stale"),
        pytest.param(timedelta(days=90), "free_plan_evidence_stale", id="long-stale"),
        pytest.param(timedelta(seconds=-1), "free_plan_evidence_not_yet_valid", id="future"),
    ],
)
def test_the_evidence_window(monkeypatch, age, code):
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - age)
    decision = decide()
    assert codes(decision) == (set() if code is None else {code})
    assert decision.allowed is (code is None)


def test_stale_evidence_at_the_send_makes_no_provider_call(
    telemetry, stub, live_http, wire, monkeypatch
):
    monkeypatch.setattr(groq_live, "FREE_PLAN_EVIDENCE_VERIFIED_AT", NOW - timedelta(days=15))
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert codes(refused.value) == {"free_plan_evidence_stale"}
    assert_untouched(wire, gate)


# ---------------------------------------------------------------- re-evaluated before the send
def test_the_clock_is_read_again_before_the_send(telemetry, live_http, wire):
    moments = [NOW + timedelta(days=30)]
    gate = FreePlanSession(dict(ENV), clock=lambda: moments[0])
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert "free_plan_attestation_stale" in codes(refused.value)
    assert "free_plan_evidence_stale" in codes(refused.value)
    assert_untouched(wire, gate)


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        pytest.param("LLMI_LIVE_CONSENT", "", "consent_missing", id="consent-removed"),
        pytest.param("GROQ_API_KEY", "", "api_key_missing", id="key-removed"),
        pytest.param("HTTPS_PROXY", "http://127.0.0.1:9", "client_env_override", id="proxy-added"),
        pytest.param("LLMI_GROQ_PLAN", "developer", "plan_not_free", id="plan-changed"),
    ],
)
def test_the_environment_is_read_again_before_the_send(
    telemetry, live_http, wire, name, value, code
):
    env = dict(ENV)
    gate = session(env)
    llm = guarded(telemetry, live_http, gate)
    env[name] = value
    with pytest.raises(LiveRefusal) as refused:
        llm.generate_text(text_request())
    assert codes(refused.value) == {code}
    assert_untouched(wire, gate)


def test_a_refusal_leaves_the_slot_free(telemetry, live_http, wire):
    env = env_with(LLMI_LIVE_CONSENT=None)
    gate = session(env)
    llm = guarded(telemetry, live_http, gate)
    with pytest.raises(LiveRefusal):
        llm.generate_text(text_request())
    assert not groq_live._PROCESS_SLOT.locked()
    env["LLMI_LIVE_CONSENT"] = ENV["LLMI_LIVE_CONSENT"]
    wire.steps.append(ok())
    assert llm.generate_text(text_request()).status == "completed"
    assert len(wire.requests) == 1


# ---------------------------------------------------------------- the gate is mandatory
def test_real_provenance_without_a_gate_is_refused(telemetry, live_http):
    with pytest.raises(ConfigError, match="FreePlanSession"):
        ObservedGroqClient(telemetry, FAKE_GROQ_KEY, http_client=live_http, provenance="real")


def test_a_lookalike_gate_is_refused(telemetry, live_http):
    class Lookalike:
        def authorize_send(self, http, request):  # pragma: no cover - refused before use
            raise AssertionError

    with pytest.raises(ConfigError, match="FreePlanSession"):
        ObservedGroqClient(
            telemetry,
            FAKE_GROQ_KEY,
            http_client=live_http,
            provenance="real",
            send_gate=Lookalike(),  # type: ignore[arg-type]
        )


def test_a_subclassed_gate_is_refused(telemetry, live_http):
    class Permissive(FreePlanSession):
        pass

    with pytest.raises(ConfigError, match="FreePlanSession"):
        guarded(telemetry, live_http, Permissive(dict(ENV)))


def test_a_gate_removed_after_construction_stops_the_send(telemetry, live_http, wire):
    gate = session()
    llm = ObservedGroqClient(
        telemetry, FAKE_GROQ_KEY, http_client=live_http, provenance="real", send_gate=gate
    )
    llm._send_gate = None
    with pytest.raises(ConfigError, match="FreePlanSession"):
        llm.create_chat_completion(model=MODEL, input_text="x", max_completion_tokens=64)
    assert_untouched(wire, gate)


def test_synthetic_provenance_cannot_carry_the_gate(telemetry):
    fake = FakeGroq([])
    with pytest.raises(ConfigError, match="only to real"):
        ObservedGroqClient(
            telemetry,
            FAKE_GROQ_KEY,
            http_client=fake.http_client(),
            provenance="synthetic",
            send_gate=session(),
        )


def test_the_lock_is_checked_again_at_the_send(telemetry, live_http, wire, monkeypatch):
    gate = session()
    llm = guarded(telemetry, live_http, gate)
    monkeypatch.setattr(groq_adapter, "REAL_EXECUTION_ENABLED", False)
    with pytest.raises(ConfigError, match="not enabled"):
        llm.generate_text(text_request())
    assert_untouched(wire, gate)


# ---------------------------------------------------------------- endpoint boundary
def pinned_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def one_connection() -> httpx.Limits:
    return httpx.Limits(max_connections=1, max_keepalive_connections=0)


HEADERS = {
    # The key the policy validates (ENV): since Phase 8A-4 the gate refuses any other credential.
    "Authorization": f"Bearer {FAKE_GROQ_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "User-Agent": "llm-insight-sdk/test",
}


def body_bytes(**changes: Any) -> bytes:
    body: dict[str, Any] = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "SYNTHETIC"}],
        "max_completion_tokens": 64,
        "stream": False,
        "include_reasoning": False,
    }
    for name, value in changes.items():
        if value is ...:
            del body[name]
        else:
            body[name] = value
    return json.dumps(body).encode()


def pinned_request(http: httpx.Client, **changes: Any) -> httpx.Request:
    kw: dict[str, Any] = {
        "method": "POST",
        "url": ENDPOINT,
        "content": body_bytes(),
        "headers": HEADERS,
        "timeout": GROQ_TIMEOUT,
    }
    kw.update(changes)
    return http.build_request(**kw)


def client_kw(**changes: Any) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "verify": pinned_context(),
        "trust_env": False,
        "follow_redirects": False,
        "timeout": GROQ_TIMEOUT,
        "limits": one_connection(),
    }
    kw.update(changes)
    return kw


def transport(**changes: Any) -> httpx.HTTPTransport:
    kw: dict[str, Any] = {"verify": pinned_context(), "limits": one_connection()}
    kw.update(changes)
    return httpx.HTTPTransport(**kw)


def test_the_built_client_and_pinned_request_have_no_problem(live_http):
    assert send_boundary_problems(live_http, pinned_request(live_http)) == []


def test_the_control_used_below_has_no_problem():
    with httpx.Client(**client_kw()) as http:
        assert send_boundary_problems(http, pinned_request(http)) == []


def empty_trust_store() -> ssl.SSLContext:
    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def old_tls() -> ssl.SSLContext:
    context = pinned_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        context.minimum_version = ssl.TLSVersion.TLSv1_1
    return context


def unverified_tls() -> ssl.SSLContext:
    context = pinned_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _hook(request: httpx.Request) -> None:  # pragma: no cover - refused before use
    raise AssertionError


MOCK = httpx.MockTransport(lambda request: httpx.Response(200))
CLIENT_VIOLATIONS: list[Any] = [
    pytest.param(lambda: {"transport": MOCK}, "HTTPTransport", id="mock-transport"),
    pytest.param(lambda: {"proxy": "http://127.0.0.1:9"}, "mount", id="proxy"),
    pytest.param(lambda: {"mounts": {"https://": MOCK}}, "mount", id="mount"),
    pytest.param(lambda: {"follow_redirects": True}, "redirects", id="redirects"),
    pytest.param(lambda: {"trust_env": True}, "environment", id="trust-env"),
    pytest.param(lambda: {"base_url": "https://x.invalid"}, "base URL", id="base-url"),
    pytest.param(lambda: {"timeout": 600.0}, "timeouts", id="timeout"),
    pytest.param(lambda: {"auth": ("a", "b")}, "auth", id="auth"),
    pytest.param(lambda: {"event_hooks": {"request": [_hook]}}, "hook", id="event-hook"),
    pytest.param(lambda: {"headers": {"X-Other": "1"}}, "header", id="custom-header"),
    pytest.param(lambda: {"verify": False}, "TLS certificate", id="tls-off"),
    pytest.param(lambda: {"verify": unverified_tls()}, "TLS certificate", id="tls-no-verify"),
    pytest.param(lambda: {"verify": empty_trust_store()}, "certifi", id="tls-other-roots"),
    pytest.param(lambda: {"verify": old_tls()}, "TLS 1.2", id="tls-old-version"),
    pytest.param(lambda: {"transport": transport(http2=True)}, "HTTP/1.1", id="http2"),
    pytest.param(lambda: {"limits": httpx.Limits(max_connections=2)}, "one connection", id="pool"),
    pytest.param(
        lambda: {"limits": httpx.Limits(max_connections=1, max_keepalive_connections=1)},
        "kept alive",
        id="keepalive",
    ),
    pytest.param(lambda: {"transport": transport(retries=1)}, "retries", id="transport-retries"),
    pytest.param(lambda: {"transport": transport(uds="unused.sock")}, "Unix socket", id="uds"),
    pytest.param(
        lambda: {"transport": transport(local_address="127.0.0.1")}, "local address", id="local"
    ),
    pytest.param(
        lambda: {
            "transport": transport(socket_options=[(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)])
        },
        "socket option",
        id="socket-option",
    ),
]


@pytest.mark.parametrize(("changes", "expected"), CLIENT_VIOLATIONS)
def test_each_client_violation_is_a_boundary_problem(changes, expected):
    with httpx.Client(**client_kw(**changes())) as http:
        problems = send_boundary_problems(http, pinned_request(http))
    assert any(expected in p for p in problems), problems


def test_a_replaced_network_backend_is_a_boundary_problem():
    with httpx.Client(**client_kw()) as http:
        http._transport._pool._network_backend = object()
        assert send_boundary_problems(http, pinned_request(http)) == [
            "the default network backend must be used"
        ]


def test_a_proxy_on_a_plain_pool_is_a_boundary_problem():
    with httpx.Client(**client_kw()) as http:
        http._transport._pool._proxy = object()
        assert send_boundary_problems(http, pinned_request(http)) == [
            "no proxy, Unix socket, local address or socket option may be configured"
        ]


def test_tls_key_logging_is_a_boundary_problem(tmp_path):
    context = pinned_context()
    context.keylog_filename = str(tmp_path / "keys.log")
    with httpx.Client(**client_kw(verify=context)) as http:
        assert send_boundary_problems(http, pinned_request(http)) == ["TLS key logging must be off"]


def test_a_context_that_is_not_ssl_is_a_boundary_problem():
    assert groq_live._tls_problems(object()) == ["the TLS context must be a real ssl.SSLContext"]


def test_a_keylog_variable_set_at_build_time_is_caught_by_the_boundary(monkeypatch, tmp_path):
    """`ssl.create_default_context` honours it even though the client ignores the environment."""
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "keys.log"))
    http = build_groq_http_client()
    try:
        assert "TLS key logging must be off" in send_boundary_problems(http, pinned_request(http))
    finally:
        http.close()


def test_a_client_of_another_type_is_a_boundary_problem(live_http):
    problems = send_boundary_problems(object(), pinned_request(live_http))
    assert "the HTTP client must be exactly an httpx.Client" in problems


REQUEST_VIOLATIONS = [
    pytest.param({"url": ENDPOINT.replace("https://", "http://")}, id="plain-http"),
    pytest.param({"url": ENDPOINT.replace("api.groq.com", "api.groq.com.evil.invalid")}, id="host"),
    pytest.param({"url": ENDPOINT.replace("api.groq.com", "api.groq.com:8443")}, id="port"),
    pytest.param({"url": ENDPOINT + "/extra"}, id="path"),
    pytest.param({"url": ENDPOINT + "?debug=1"}, id="query"),
    pytest.param({"url": ENDPOINT.replace("https://", "https://u:p@")}, id="userinfo"),
    pytest.param({"method": "GET"}, id="method"),
    pytest.param({"headers": {**HEADERS, "X-Extra": "1"}}, id="extra-header"),
    pytest.param(
        {"headers": {k: v for k, v in HEADERS.items() if k != "Content-Type"}}, id="missing-header"
    ),
    pytest.param(
        {"headers": [*HEADERS.items(), ("Authorization", "Bearer y")]}, id="two-authorizations"
    ),
    pytest.param({"headers": {**HEADERS, "Host": "evil.invalid"}}, id="host-header"),
    pytest.param({"timeout": httpx.Timeout(600.0)}, id="longer-timeout"),
    pytest.param({"extensions": {"sni_hostname": "evil.invalid"}}, id="sni-override"),
]


@pytest.mark.parametrize("changes", REQUEST_VIOLATIONS)
def test_each_request_violation_is_a_boundary_problem(live_http, changes):
    assert send_boundary_problems(live_http, pinned_request(live_http, **changes)) != []


@pytest.mark.parametrize("changes", REQUEST_VIOLATIONS)
def test_each_request_violation_makes_no_provider_call(live_http, wire, changes):
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(live_http, pinned_request(live_http, **changes))
    assert codes(refused.value) == {"endpoint_boundary_violation"}
    assert_untouched(wire, gate)


def test_mock_transport_under_real_provenance_makes_no_call(telemetry):
    fake = FakeGroq([])
    with pytest.raises(ConfigError, match="HTTPTransport"):
        ObservedGroqClient(
            telemetry,
            FAKE_GROQ_KEY,
            http_client=fake.http_client(),
            provenance="real",
            send_gate=session(),
        )
    assert fake.requests == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda http: setattr(http, "follow_redirects", True), id="redirects"),
        pytest.param(lambda http: setattr(http, "_trust_env", True), id="trust-env"),
        pytest.param(lambda http: http._mounts.update({"https://": MOCK}), id="proxy-mount"),
        pytest.param(lambda http: setattr(http, "_transport", MOCK), id="mock-transport"),
        pytest.param(
            lambda http: setattr(http._transport._pool._ssl_context, "check_hostname", False),
            id="tls-hostname",
        ),
    ],
)
def test_a_client_changed_while_the_request_is_built_is_refused(
    telemetry, stub, live_http, wire, monkeypatch, mutate
):
    """The adapter's own check has passed by then. Only the gate sees this change."""
    gate = session()
    llm = guarded(telemetry, live_http, gate)
    original = live_http.build_request

    def changing(method: str, url: str, **kw: Any) -> httpx.Request:
        request = original(method, url, **kw)
        mutate(live_http)
        return request

    monkeypatch.setattr(live_http, "build_request", changing)
    with pytest.raises(LiveRefusal) as refused:
        llm.generate_text(text_request())
    assert codes(refused.value) == {"endpoint_boundary_violation"}
    assert_untouched(wire, gate)
    assert client_spans(telemetry, stub) == []


# ---------------------------------------------------------------- the body that will be sent
BAD_BODIES = [
    pytest.param(b"{not json", id="not-json"),
    pytest.param(b"[]", id="not-an-object"),
    pytest.param(body_bytes(tools=[]), id="extra-field"),
    pytest.param(body_bytes(service_tier="flex"), id="service-tier"),
    pytest.param(body_bytes(stream=...), id="no-stream-field"),
    pytest.param(body_bytes(stream=True), id="streaming"),
    pytest.param(body_bytes(include_reasoning=True), id="reasoning"),
    pytest.param(body_bytes(messages="x"), id="messages-not-a-list"),
    pytest.param(body_bytes(messages=[]), id="no-message"),
    pytest.param(body_bytes(messages=["x"]), id="message-not-an-object"),
    pytest.param(
        body_bytes(messages=[{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]),
        id="two-messages",
    ),
    pytest.param(body_bytes(messages=[{"role": "system", "content": "a"}]), id="system-role"),
    pytest.param(body_bytes(messages=[{"role": "user"}]), id="message-no-content"),
    pytest.param(body_bytes(messages=[{"role": "user", "content": ["a"]}]), id="content-parts"),
    pytest.param(
        body_bytes(messages=[{"role": "user", "content": "a", "name": "n"}]), id="message-extra"
    ),
    pytest.param(body_bytes(model=5), id="model-not-text"),
    pytest.param(body_bytes(max_completion_tokens=True), id="limit-bool"),
    pytest.param(body_bytes(max_completion_tokens="64"), id="limit-text"),
]


@pytest.mark.parametrize("content", BAD_BODIES)
def test_any_body_other_than_the_pinned_request_is_refused(live_http, wire, content):
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(live_http, pinned_request(live_http, content=content))
    assert codes(refused.value) == {"request_not_allowed"}
    assert_untouched(wire, gate)


def test_an_unparsable_body_is_not_kept_on_the_refusal(live_http):
    with pytest.raises(LiveRefusal) as refused:
        session().authorize_send(
            live_http, pinned_request(live_http, content=b"{" + PROMPT_SENTINEL.encode())
        )
    assert refused.value.__context__ is None and refused.value.__cause__ is None
    assert PROMPT_SENTINEL not in str(refused.value)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        pytest.param({"model": "other/model"}, "model_not_allowed", id="other-model"),
        pytest.param({"max_completion_tokens": 15}, "output_limit_exceeded", id="output-low"),
        pytest.param({"max_completion_tokens": 257}, "output_limit_exceeded", id="output-high"),
        pytest.param(
            {"messages": [{"role": "user", "content": "x" * 961}]},
            "input_too_large",
            id="input-one-byte-over",
        ),
    ],
)
def test_the_bounds_are_read_from_the_body(live_http, wire, changes, code):
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(live_http, pinned_request(live_http, content=body_bytes(**changes)))
    assert codes(refused.value) == {code}
    assert_untouched(wire, gate)


def test_a_listed_model_other_than_the_configured_one_is_refused(live_http, wire, monkeypatch):
    gate = session()
    body = body_bytes(model="other/model")
    monkeypatch.setattr(
        groq_live,
        "FREE_PLAN_MODELS",
        {**groq_live.FREE_PLAN_MODELS, "other/model": FreePlanModel("other/model", ())},
    )
    with pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(live_http, pinned_request(live_http, content=body))
    assert codes(refused.value) == {"model_not_allowed"}  # listed, but not the configured model
    assert_untouched(wire, gate)


def test_the_largest_allowed_input_and_output_are_authorized(live_http):
    body = body_bytes(messages=[{"role": "user", "content": "x" * 960}], max_completion_tokens=256)
    assert session().authorize_send(live_http, pinned_request(live_http, content=body)) is not None


@pytest.mark.parametrize(
    ("request_kw", "code"),
    [
        pytest.param({"max_output_tokens": 15}, "output_limit_exceeded", id="output-below-floor"),
        pytest.param({"max_output_tokens": 257}, "output_limit_exceeded", id="output-above-cap"),
        pytest.param({"input_text": "x" * 961}, "input_too_large", id="input-over-bound"),
        pytest.param({"model": "other/model"}, "model_not_allowed", id="unlisted-model"),
    ],
)
def test_a_request_outside_the_bounds_makes_no_provider_call(
    telemetry, stub, live_http, wire, request_kw, code
):
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request(**request_kw))
    assert codes(refused.value) == {code}
    assert_untouched(wire, gate)
    assert client_spans(telemetry, stub) == []


# ---------------------------------------------------------------- one request
def test_one_authorized_request_reaches_the_pinned_endpoint_once(telemetry, stub, live_http, wire):
    wire.steps.append(ok())
    gate = session()
    outcome = guarded(telemetry, live_http, gate).generate_text(text_request())
    assert outcome.status == "completed" and len(outcome.attempts) == 1
    assert [str(r.url) for r in wire.requests] == [ENDPOINT]
    assert gate.snapshot() == {
        "requests_started": 1,
        "max_requests": 1,
        "physical_attempts": 1,
        "provider_request_state": "sent",
        "halted": None,
    }
    (span,) = client_spans(telemetry, stub)
    assert span["attributes"]["llminsight.provenance"] == "real"


def test_a_second_send_is_refused_after_a_completed_one(telemetry, live_http, wire):
    wire.steps.append(ok())
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    adapter.generate_text(text_request())
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1


def test_the_session_limit_holds_without_the_process_slot(telemetry, live_http, wire, monkeypatch):
    wire.steps.append(ok())
    adapter = guarded(telemetry, live_http, session())
    adapter.generate_text(text_request())
    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", threading.Lock())
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1


def test_a_second_session_in_the_process_is_refused(telemetry, live_http, wire):
    wire.steps.append(ok())
    guarded(telemetry, live_http, session()).generate_text(text_request())
    other = session()
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, other).generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1
    assert other.snapshot()["requests_started"] == 0


class ReadFailsMidBody(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b'{"partial":'
        raise httpx.ReadTimeout("SYNTHETIC")


FAILURES = [
    pytest.param(fail(400), "invalid_request", "sent", id="http-400"),
    pytest.param(fail(401), "authentication_failed", "sent", id="http-401"),
    pytest.param(fail(429, "rate_limit_exceeded"), "rate_limited", "sent", id="http-429"),
    pytest.param(fail(500), "provider_error", "sent", id="http-500"),
    pytest.param(fail(503), "provider_error", "sent", id="http-503"),
    pytest.param(Step(raw=b"{bad"), "malformed_response", "sent", id="malformed-body"),
    pytest.param(Step(stream=ReadFailsMidBody()), "timeout", "sent", id="timeout-in-body"),
    pytest.param(Step(raise_=httpx.ReadTimeout), "timeout", "possibly_sent", id="read-timeout"),
    pytest.param(Step(raise_=httpx.WriteTimeout), "timeout", "possibly_sent", id="write-timeout"),
    pytest.param(Step(raise_=httpx.ReadError), "connection_error", "possibly_sent", id="read"),
    pytest.param(Step(raise_=httpx.WriteError), "connection_error", "possibly_sent", id="write"),
    pytest.param(
        Step(raise_=httpx.RemoteProtocolError), "connection_error", "possibly_sent", id="protocol"
    ),
    pytest.param(Step(raise_=httpx.ConnectError), "connection_error", "not_sent", id="connect"),
    pytest.param(Step(raise_=httpx.ConnectTimeout), "timeout", "not_sent", id="connect-timeout"),
    pytest.param(Step(raise_=httpx.PoolTimeout), "timeout", "not_sent", id="pool-timeout"),
]


@pytest.mark.parametrize(("step", "category", "state"), FAILURES)
def test_a_failure_is_never_retried_and_its_state_is_recorded(
    telemetry, live_http, wire, step, category, state
):
    wire.steps.append(step)
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    outcome = adapter.generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", category)
    assert len(wire.requests) == 1 and len(outcome.attempts) == 1
    snapshot = gate.snapshot()
    assert snapshot["provider_request_state"] == state
    assert snapshot["physical_attempts"] == 1 and snapshot["halted"] is None
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1


def test_an_interruption_during_the_send_is_possibly_sent(telemetry, live_http, wire):
    wire.steps.append(Step(raise_=KeyboardInterrupt))
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    with pytest.raises(KeyboardInterrupt):
        adapter.generate_text(text_request())
    assert gate.snapshot()["provider_request_state"] == "possibly_sent"
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1


def test_an_interruption_before_the_send_is_proven_unsent(telemetry, live_http, wire, monkeypatch):
    def interrupted() -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(groq_adapter, "new_call_id", interrupted)
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    with pytest.raises(KeyboardInterrupt):
        adapter.generate_text(text_request())
    snapshot = gate.snapshot()
    assert snapshot["provider_request_state"] == "not_sent"
    assert (snapshot["physical_attempts"], wire.requests) == (0, [])
    with pytest.raises(LiveRefusal) as refused:  # still no resend
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}


def test_a_connect_failure_uses_the_only_attempt(telemetry, live_http, wire):
    wire.steps.extend([Step(raise_=httpx.ConnectError), ok()])
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    adapter.generate_text(text_request())
    with pytest.raises(LiveRefusal):
        adapter.generate_text(text_request())
    assert len(wire.requests) == 1 and len(wire.steps) == 1


# ---------------------------------------------------------------- redirects
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_not_followed_and_halts_the_session(telemetry, live_http, wire, status):
    wire.steps.append(
        Step(status, body={}, headers={"location": "https://collector.example.invalid/steal"})
    )
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    outcome = adapter.generate_text(text_request())
    assert (outcome.status, outcome.error_category) == ("error", "http_error")
    assert [str(r.url) for r in wire.requests] == [ENDPOINT]
    assert gate.snapshot()["halted"] == "redirect_refused"
    with pytest.raises(LiveRefusal) as refused:
        adapter.generate_text(text_request())
    assert codes(refused.value) == {"session_halted"}
    assert len(wire.requests) == 1


# ---------------------------------------------------------------- post-call halts
HALTS = [
    pytest.param(ok(service_tier="flex"), "service_tier_not_free_plan", id="tier-flex"),
    pytest.param(ok(service_tier="performance"), "service_tier_not_free_plan", id="tier-perf"),
    pytest.param(ok(service_tier=7), "service_tier_not_free_plan", id="tier-not-text"),
    pytest.param(ok(service_tier="bad tier!"), "service_tier_not_free_plan", id="tier-bad-chars"),
    pytest.param(ok(model="other/model"), "response_model_mismatch", id="model-mismatch"),
    pytest.param(fail(400, code="blocked_api_access"), "paid_plan_indicator", id="blocked-400"),
    pytest.param(
        Step(200, error_body(code="blocked_api_access")), "paid_plan_indicator", id="blocked-200"
    ),
    pytest.param(fail(402), "paid_plan_indicator", id="payment-required"),
    pytest.param(
        ok(usage={"prompt_tokens": 20, "completion_tokens": 65, "total_tokens": 85}),
        "usage_exceeded_bound",
        id="output-over-request",
    ),
    pytest.param(
        ok(usage={"prompt_tokens": 5000, "completion_tokens": 1, "total_tokens": 5001}),
        "usage_exceeded_bound",
        id="input-over-bound",
    ),
]


@pytest.mark.parametrize(("step", "halt"), HALTS)
def test_a_post_call_indicator_halts_and_never_resends(telemetry, live_http, wire, step, halt):
    wire.steps.append(step)
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
    [
        pytest.param(ok(), id="on-demand"),
        pytest.param(ok(service_tier=None), id="null-tier"),
        pytest.param(ok(usage=None), id="no-usage"),
        pytest.param(
            ok(usage={"prompt_tokens": 98, "completion_tokens": 64, "total_tokens": 162}),
            id="exactly-at-the-bounds",
        ),
    ],
)
def test_nothing_halts_on_an_ordinary_response(telemetry, live_http, wire, step):
    wire.steps.append(step)
    gate = session()
    guarded(telemetry, live_http, gate).generate_text(text_request())
    assert gate.snapshot()["halted"] is None


# ---------------------------------------------------------------- concurrency
def test_a_concurrent_send_is_refused_while_one_is_in_flight(
    telemetry, live_http, wire, monkeypatch
):
    in_flight, release = threading.Event(), threading.Event()

    def blocking(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        in_flight.set()
        assert release.wait(10)
        return wire.handler(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocking)
    wire.steps.append(ok())
    gate = session()
    adapter = guarded(telemetry, live_http, gate)
    results: list[object] = []
    worker = threading.Thread(target=lambda: results.append(adapter.generate_text(text_request())))
    worker.start()
    assert in_flight.wait(10)
    try:
        with pytest.raises(LiveRefusal) as refused:
            adapter.generate_text(text_request())
    finally:
        release.set()
        worker.join(10)
    assert codes(refused.value) == {"concurrency_limit"}
    assert len(results) == 1 and len(wire.requests) == 1


def test_the_slot_is_taken_while_the_session_is_locked(live_http, monkeypatch):
    gate = session()
    locked: list[bool] = []

    class Slot:
        def acquire(self, blocking: bool) -> bool:
            locked.append(gate._lock.locked())
            return True

    monkeypatch.setattr(groq_live, "_PROCESS_SLOT", Slot())
    gate.authorize_send(live_http, pinned_request(live_http))
    assert locked == [True]


def test_only_one_of_many_threads_is_authorized(live_http):
    gate = session()
    barrier = threading.Barrier(8)
    granted: list[object] = []
    refused: list[set[str]] = []

    def attempt() -> None:
        barrier.wait(10)
        try:
            granted.append(gate.authorize_send(live_http, pinned_request(live_http)))
        except LiveRefusal as exc:
            refused.append(codes(exc))

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(granted) == 1 and len(refused) == 7
    assert all(found == {"concurrency_limit"} for found in refused)


# ---------------------------------------------------------------- the permit
def test_a_permit_allows_exactly_one_transmission(live_http):
    gate = session()
    permit = gate.authorize_send(live_http, pinned_request(live_http))
    permit.transmission_starting()
    with pytest.raises(LiveRefusal) as refused:
        permit.transmission_starting()
    assert codes(refused.value) == {"permit_already_used"}
    assert gate.snapshot()["physical_attempts"] == 1


def test_a_finished_permit_cannot_start_a_transmission(live_http):
    permit = session().authorize_send(live_http, pinned_request(live_http))
    permit.finish(None)
    with pytest.raises(LiveRefusal):
        permit.transmission_starting()


def test_a_response_before_a_transmission_does_not_claim_sent(live_http):
    gate = session()
    permit = gate.authorize_send(live_http, pinned_request(live_http))
    permit.response_received()
    assert gate.snapshot()["provider_request_state"] == "not_sent"


def test_a_late_connect_failure_cannot_erase_a_received_response(live_http):
    gate = session()
    permit = gate.authorize_send(live_http, pinned_request(live_http))
    permit.transmission_starting()
    permit.response_received()
    permit.connection_not_established()
    assert gate.snapshot()["provider_request_state"] == "sent"


def test_finishing_twice_changes_nothing(live_http):
    gate = session()
    permit = gate.authorize_send(live_http, pinned_request(live_http))
    permit.finish(None)
    permit.finish(groq_adapter.GroqCallError("402", "payment_required", http_status=402))
    assert gate.snapshot()["halted"] is None


def test_an_unfinished_permit_keeps_the_session_closed(live_http):
    gate = session()
    gate.authorize_send(live_http, pinned_request(live_http))
    with pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(live_http, pinned_request(live_http))
    assert codes(refused.value) == {"concurrency_limit"}


def test_a_failure_while_settling_cannot_change_the_outcome(
    telemetry, live_http, wire, monkeypatch
):
    monkeypatch.setattr(groq_live.FreePlanPermit, "finish", _raises_runtime_error)
    wire.steps.append(ok())
    gate = session()
    outcome = guarded(telemetry, live_http, gate).generate_text(text_request())
    assert outcome.status == "completed"
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    assert codes(refused.value) == {"concurrency_limit"}
    assert len(wire.requests) == 1


def _raises_runtime_error(self: object, outcome: object) -> None:
    raise RuntimeError("SYNTHETIC")


# ---------------------------------------------------------------- privacy
def _serialized(*items: object) -> str:
    return json.dumps(items, default=repr)


def test_a_refusal_carries_no_key_or_content(caplog, telemetry, stub, live_http, wire):
    caplog.set_level(logging.DEBUG)
    gate = session(env_with(LLMI_LIVE_CONSENT="wrong"))
    with pytest.raises(LiveRefusal) as refused:
        guarded(telemetry, live_http, gate).generate_text(text_request())
    text = _serialized(str(refused.value), repr(refused.value), gate.snapshot(), repr(gate))
    text += caplog.text + _serialized(client_spans(telemetry, stub))
    for secret in SECRETS:
        assert secret not in text
    assert refused.value.__cause__ is None and refused.value.__context__ is None


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("sk-" + "SYNTHETICFOREIGN" * 2, id="openai"),
        pytest.param("AIza" + "SYNTHETICFOREIGN" * 2, id="gemini"),
        pytest.param("has SYNTHETICFOREIGN space", id="invalid"),
    ],
)
def test_a_bad_key_is_never_quoted(value):
    decision = decide(env_with(GROQ_API_KEY=value))
    text = _serialized([(r.code, r.message) for r in decision.refusals], repr(decision))
    assert "SYNTHETICFOREIGN" not in text


def test_a_sent_request_exports_only_metadata(telemetry, stub, live_http, wire, caplog):
    caplog.set_level(logging.DEBUG)
    wire.steps.append(ok())
    gate = session()
    guarded(telemetry, live_http, gate).generate_text(text_request())
    (span,) = client_spans(telemetry, stub)
    text = _serialized(span, gate.snapshot()) + caplog.text
    for secret in SECRETS:
        assert secret not in text
    assert not any("plan" in key or "attest" in key for key in span["attributes"])


def test_the_snapshot_holds_only_safe_counters():
    assert set(session().snapshot()) == {
        "requests_started",
        "max_requests",
        "physical_attempts",
        "provider_request_state",
        "halted",
    }


# ---------------------------------------------------------------- what does not exist
def test_no_custom_endpoint_can_be_supplied():
    for callable_ in (ObservedGroqClient.__init__, FreePlanSession.__init__):
        names = set(inspect.signature(callable_).parameters)
        assert not names & {"base_url", "url", "endpoint", "host", "proxy", "verify"}


def test_no_paid_setting_exists():
    assert groq_live.GROQ_MODE == "free-plan" and groq_live.PLAN_FREE == "free"
    source = Path(groq_live.__file__).read_text(encoding="utf-8")
    assert "fallback_model" not in source and "service_tier=" not in source


def test_the_guard_creates_no_http_client_and_sends_nothing():
    source = Path(groq_live.__file__).read_text(encoding="utf-8")
    assert "httpx.Client(" not in source and ".send(" not in source
    assert "httpx.HTTPTransport(" not in source


def test_every_operator_setting_is_listed_in_the_env_example():
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    for name in ("LLMI_GROQ_MODE", "LLMI_GROQ_PLAN", "LLMI_GROQ_PLAN_CHECKED_ON", "GROQ_API_KEY"):
        assert f"\n{name}=\n" in example.replace("\r\n", "\n")


# ---------------------------------------------------------------- F1, F2: the audited pool only
# The expected messages are written out so that they do not follow a change in the code.
POOL_PROBLEM = "the direct HTTPTransport must use exactly the audited httpcore.ConnectionPool"
STRUCTURE_PROBLEM = "the connection pool must have exactly the audited attributes"
TRANSPORT_PROBLEM = "the default direct HTTPTransport (no proxy, no mock) must be used"
UNSET_VALUE_PROBLEM = "no proxy, Unix socket, local address or socket option may be configured"
IDLE_PROBLEM = "the connection pool must hold no connection or queued request"
UNSET_ATTRIBUTES = ("_proxy", "_uds", "_local_address", "_socket_options")
AUDITED_POOL_ATTRIBUTES = {
    "_ssl_context",
    "_proxy",
    "_max_connections",
    "_max_keepalive_connections",
    "_keepalive_expiry",
    "_http1",
    "_http2",
    "_retries",
    "_local_address",
    "_uds",
    "_network_backend",
    "_socket_options",
    "_connections",
    "_requests",
    "_optional_thread_lock",
}


def missing_problem(name: str) -> str:
    return f"the connection pool attribute {name} is missing or unreadable"


def _substitute_class(*bases: type, **extra: Any) -> Any:
    """A class named `ConnectionPool`. Its `handle_request` records only that it was reached."""

    def handle_request(self: Any, request: Any) -> Any:
        type(self).reached.append(1)
        raise httpcore.ConnectError("SYNTHETIC")

    namespace = {"handle_request": handle_request, "reached": [], **extra}
    return type("ConnectionPool", bases, namespace)


def _faithful_copy(genuine: Any, cls: Any) -> Any:
    """`cls` holding every value of the genuine pool, so only its identity can be wrong."""
    substitute = cls.__new__(cls)
    vars(substitute).update(vars(genuine))
    return substitute


def lookalike_pool(genuine: Any) -> Any:
    return _faithful_copy(genuine, _substitute_class())


def subclassed_pool(genuine: Any) -> Any:
    return _faithful_copy(genuine, _substitute_class(httpcore.ConnectionPool))


def spoofed_class_pool(genuine: Any) -> Any:
    """`isinstance` and `__class__` both report the audited type for this object."""
    spoof = property(lambda self: httpcore.ConnectionPool)
    return _faithful_copy(genuine, _substitute_class(__class__=spoof))


FAITHFUL_SUBSTITUTES = [
    pytest.param(lookalike_pool, id="lookalike"),
    pytest.param(subclassed_pool, id="subclass"),
    pytest.param(spoofed_class_pool, id="spoofed-class"),
]
UNEXPECTED_POOLS = [
    *FAITHFUL_SUBSTITUTES,
    pytest.param(lambda genuine: None, id="none"),
    pytest.param(lambda genuine: object(), id="plain-object"),
    pytest.param(lambda genuine: 0, id="number"),
]


@contextmanager
def preserved_pool(http: httpx.Client) -> Iterator[Any]:
    """Yields the genuine pool, and restores it and its contents so the client can be closed."""
    genuine = http._transport._pool
    saved = dict(vars(genuine))
    try:
        yield genuine
    finally:
        http._transport._pool = genuine
        vars(genuine).clear()
        vars(genuine).update(saved)
        genuine._connections.clear()
        genuine._requests.clear()


@contextmanager
def client_with_pool(make: Callable[[Any], Any]) -> Iterator[httpx.Client]:
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        http._transport._pool = make(genuine)
        yield http


@pytest.fixture(params=["client-validation", "send-boundary-alone"])
def layer(request, monkeypatch) -> Callable[[httpx.Client], list[str]]:
    """Each check must hold in the layer named here, without help from the other one."""
    if request.param == "client-validation":
        return groq_adapter.live_groq_client_problems
    monkeypatch.setattr(groq_adapter, "live_groq_client_problems", lambda http: [])
    return lambda http: send_boundary_problems(http, pinned_request(http))


def changing_during_build(monkeypatch, http: httpx.Client, change: Callable[[], Any]) -> None:
    """Applies `change` after the adapter's own check, so only the gate can see it."""
    original = http.build_request

    def changing(method: str, url: str, **kw: Any) -> httpx.Request:
        request = original(method, url, **kw)
        change()
        return request

    monkeypatch.setattr(http, "build_request", changing)


def assert_nothing_attempted(gate: FreePlanSession) -> None:
    assert gate.snapshot()["physical_attempts"] == 0
    assert gate.snapshot()["requests_started"] == 0
    assert gate.snapshot()["provider_request_state"] == "not_sent"


# F1: identity
def test_the_audited_pool_is_accepted_by_both_layers():
    with httpx.Client(**client_kw()) as http:
        assert type(http._transport._pool) is httpcore.ConnectionPool
        assert groq_adapter.live_groq_client_problems(http) == []
        assert send_boundary_problems(http, pinned_request(http)) == []


def test_the_audited_attribute_list_is_the_installed_pool(live_http):
    assert set(vars(live_http._transport._pool)) == AUDITED_POOL_ATTRIBUTES
    assert set(groq_adapter._POOL_INSTANCE_ATTRIBUTES) == AUDITED_POOL_ATTRIBUTES


@pytest.mark.parametrize("make", UNEXPECTED_POOLS)
def test_an_unexpected_pool_is_refused_by_each_layer(layer, make):
    with client_with_pool(make) as http:
        assert POOL_PROBLEM in layer(http)


@pytest.mark.parametrize("make", FAITHFUL_SUBSTITUTES)
def test_identity_alone_refuses_a_faithful_substitute(make):
    """Every value matches the audited pool, so no other check can be what refuses it."""
    with client_with_pool(make) as http:
        assert send_boundary_problems(http, pinned_request(http)) == [POOL_PROBLEM]


def test_a_missing_pool_is_refused_by_each_layer(layer):
    with httpx.Client(**client_kw()) as http, preserved_pool(http):
        del http._transport._pool
        problems = layer(http)
    assert POOL_PROBLEM in problems
    assert STRUCTURE_PROBLEM in problems


def test_a_genuine_proxy_pool_is_refused_by_each_layer(layer):
    proxy = transport(proxy=httpx.Proxy("http://127.0.0.1:9"))
    with httpx.Client(**client_kw(transport=proxy)) as http:
        assert isinstance(http._transport._pool, httpcore.ConnectionPool)
        assert POOL_PROBLEM in layer(http)


def test_a_pool_whose_attributes_cannot_be_listed_is_refused(layer):
    def unreadable(self: Any) -> Any:
        raise RuntimeError("SYNTHETIC")

    cls = _substitute_class(__dict__=property(unreadable))
    with client_with_pool(lambda genuine: cls.__new__(cls)) as http:
        problems = layer(http)
    assert POOL_PROBLEM in problems
    assert STRUCTURE_PROBLEM in problems


def test_no_other_check_is_skipped_because_the_pool_type_is_wrong(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_1
    context.keylog_filename = str(tmp_path / "keys.log")

    def unsafe(genuine: Any) -> Any:
        substitute = lookalike_pool(genuine)
        vars(substitute).update(
            _proxy=object(),
            _ssl_context=context,
            _network_backend=object(),
            _max_connections=2,
            _max_keepalive_connections=1,
            _http2=True,
        )
        return substitute

    with client_with_pool(unsafe) as http:
        problems = send_boundary_problems(http, pinned_request(http))
    for expected in (
        POOL_PROBLEM,
        UNSET_VALUE_PROBLEM,
        "TLS must trust exactly the pinned certifi bundle",
        "TLS 1.2 or newer is required",
        "TLS key logging must be off",
        "the default network backend must be used",
        "the HTTP client must be limited to one connection",
        "connections must not be kept alive",
        "only HTTP/1.1 may be used",
    ):
        assert expected in problems, expected


def test_retries_and_tls_are_still_checked_on_a_wrong_pool():
    def unsafe(genuine: Any) -> Any:
        substitute = lookalike_pool(genuine)
        vars(substitute).update(_retries=1, _ssl_context=unverified_tls())
        return substitute

    with client_with_pool(unsafe) as http:
        problems = groq_adapter.live_groq_client_problems(http)
    assert "transport retries must be disabled" in problems
    assert "TLS certificate and hostname verification must be enabled" in problems


def _never(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - refused before use
    raise AssertionError


PATCHED_POOLS = [
    pytest.param(lambda p: setattr(p, "handle_request", _never), STRUCTURE_PROBLEM, id="send"),
    pytest.param(lambda p: setattr(p, "_added", 1), STRUCTURE_PROBLEM, id="added-attribute"),
    pytest.param(lambda p: delattr(p, "_keepalive_expiry"), STRUCTURE_PROBLEM, id="removed-other"),
    pytest.param(lambda p: p._connections.append(object()), IDLE_PROBLEM, id="seeded-connection"),
    pytest.param(lambda p: p._requests.append(object()), IDLE_PROBLEM, id="queued-request"),
    pytest.param(lambda p: setattr(p, "_connections", None), IDLE_PROBLEM, id="connections-none"),
    pytest.param(lambda p: delattr(p, "_requests"), IDLE_PROBLEM, id="requests-missing"),
    pytest.param(lambda p: setattr(p, "_requests", ()), IDLE_PROBLEM, id="requests-not-a-list"),
    pytest.param(
        lambda p: setattr(p, "_retries", False),
        "transport retries must be disabled",
        id="retries-bool",
    ),
]


@pytest.mark.parametrize(("patch", "expected"), PATCHED_POOLS)
def test_a_monkey_patched_audited_pool_is_refused_by_each_layer(layer, patch, expected):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        patch(genuine)
        assert expected in layer(http)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        pytest.param("_max_connections", True, "one connection", id="connections-bool"),
        pytest.param("_max_keepalive_connections", False, "kept alive", id="keepalive-bool"),
    ],
)
def test_a_limit_must_be_exactly_an_integer(name, value, expected):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        setattr(genuine, name, value)
        problems = send_boundary_problems(http, pinned_request(http))
    assert any(expected in p for p in problems), problems


def test_a_monkey_patched_transport_is_refused_by_each_layer(layer):
    with httpx.Client(**client_kw()) as http:
        http._transport.handle_request = _never
        try:
            assert TRANSPORT_PROBLEM in layer(http)
        finally:
            del http._transport.handle_request


def _substitute_transport(base: type) -> Any:
    """A transport holding only the genuine pool, so only its type can be wrong."""

    def handle_request(self: Any, request: httpx.Request) -> httpx.Response:
        type(self).reached.append(1)
        raise httpx.ConnectError("SYNTHETIC")

    namespace = {"handle_request": handle_request, "reached": []}
    return type("HTTPTransport", (base,), namespace)


@pytest.mark.parametrize(
    "base",
    [pytest.param(object, id="lookalike"), pytest.param(httpx.HTTPTransport, id="subclass")],
)
def test_a_substitute_transport_holding_the_audited_pool_is_refused(layer, wire, base):
    cls = _substitute_transport(base)
    gate = session()
    with httpx.Client(**client_kw()) as http:
        genuine = http._transport
        substitute = cls.__new__(cls)
        substitute._pool = genuine._pool
        http._transport = substitute
        try:
            assert TRANSPORT_PROBLEM in layer(http)
            with pytest.raises(LiveRefusal):
                gate.authorize_send(http, pinned_request(http))
        finally:
            http._transport = genuine
    assert cls.reached == []
    assert_untouched(wire, gate)


def test_a_subclassed_tls_context_is_refused():
    """It would answer the certificate and key-log checks with whatever it chooses."""

    class Lying(ssl.SSLContext):
        pass

    context = Lying(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    with httpx.Client(**client_kw(verify=context)) as http:
        assert send_boundary_problems(http, pinned_request(http)) == [
            "the TLS context must be a real ssl.SSLContext"
        ]


# F2: the four attributes that must exist and be None
@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
def test_a_missing_pool_attribute_is_refused_by_each_layer(layer, name):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        delattr(genuine, name)
        problems = layer(http)
    assert missing_problem(name) in problems
    assert UNSET_VALUE_PROBLEM not in problems


@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
def test_a_pool_attribute_present_as_none_is_accepted(layer, name):
    with httpx.Client(**client_kw()) as http:
        assert vars(http._transport._pool)[name] is None
        assert layer(http) == []


@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(False, id="false"),
        pytest.param(0, id="zero"),
        pytest.param("", id="empty-text"),
        pytest.param((), id="empty-tuple"),
        pytest.param([], id="empty-list"),
        pytest.param("set", id="text"),
        pytest.param(object(), id="object"),
    ],
)
def test_an_unexpected_pool_attribute_value_is_refused_by_each_layer(layer, name, value):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        setattr(genuine, name, value)
        problems = layer(http)
    assert UNSET_VALUE_PROBLEM in problems
    assert missing_problem(name) not in problems


@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
def test_an_unreadable_pool_attribute_is_refused_by_each_layer(layer, name):
    def unreadable(self: Any) -> Any:
        raise RuntimeError("SYNTHETIC")

    cls = _substitute_class(**{name: property(unreadable)})
    with client_with_pool(lambda genuine: _faithful_copy(genuine, cls)) as http:
        assert missing_problem(name) in layer(http)


# Refused before any send
@pytest.mark.parametrize("make", UNEXPECTED_POOLS)
def test_an_unexpected_pool_refuses_at_the_gate_before_any_send(wire, make):
    gate = session()
    with client_with_pool(make) as http, pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(http, pinned_request(http))
    assert codes(refused.value) == {"endpoint_boundary_violation"}
    assert_untouched(wire, gate)


@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
def test_a_missing_attribute_refuses_at_the_gate_before_any_send(wire, name):
    gate = session()
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        delattr(genuine, name)
        with pytest.raises(LiveRefusal) as refused:
            gate.authorize_send(http, pinned_request(http))
    assert codes(refused.value) == {"endpoint_boundary_violation"}
    assert_untouched(wire, gate)


@pytest.mark.parametrize("make", FAITHFUL_SUBSTITUTES)
def test_a_real_client_cannot_be_built_on_a_substitute_pool(telemetry, stub, make):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        substitute = make(genuine)
        http._transport._pool = substitute
        with pytest.raises(ConfigError, match="httpcore.ConnectionPool"):
            guarded(telemetry, http, session())
    assert type(substitute).reached == []
    assert client_spans(telemetry, stub) == []


@pytest.mark.parametrize("make", FAITHFUL_SUBSTITUTES)
def test_a_pool_swapped_after_construction_is_never_reached(telemetry, stub, make):
    """No fake transport here: the real `HTTPTransport` would hand the request to the pool."""
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        gate = session()
        llm = guarded(telemetry, http, gate)
        substitute = make(genuine)
        http._transport._pool = substitute
        with pytest.raises(ConfigError, match="httpcore.ConnectionPool"):
            llm.generate_text(text_request())
    assert type(substitute).reached == []
    assert_nothing_attempted(gate)
    assert client_spans(telemetry, stub) == []


@pytest.mark.parametrize("make", FAITHFUL_SUBSTITUTES)
def test_a_pool_swapped_while_the_request_is_built_is_never_reached(
    telemetry, stub, monkeypatch, make
):
    """The adapter's own check has passed by then. Only the send boundary sees this change."""
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        gate = session()
        llm = guarded(telemetry, http, gate)
        substitute = make(genuine)
        changing_during_build(
            monkeypatch, http, lambda: setattr(http._transport, "_pool", substitute)
        )
        with pytest.raises(LiveRefusal) as refused:
            llm.generate_text(text_request())
    assert POOL_PROBLEM in str(refused.value)
    assert type(substitute).reached == []
    assert_nothing_attempted(gate)
    assert client_spans(telemetry, stub) == []


@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
def test_an_attribute_removed_after_construction_is_refused_before_the_send(
    telemetry, stub, wire, name
):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        gate = session()
        llm = guarded(telemetry, http, gate)
        delattr(genuine, name)
        with pytest.raises(ConfigError, match=name):
            llm.generate_text(text_request())
    assert_untouched(wire, gate)
    assert client_spans(telemetry, stub) == []


@pytest.mark.parametrize("name", UNSET_ATTRIBUTES)
def test_an_attribute_removed_while_the_request_is_built_is_refused(
    telemetry, stub, wire, monkeypatch, name
):
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        gate = session()
        llm = guarded(telemetry, http, gate)
        changing_during_build(monkeypatch, http, lambda: delattr(genuine, name))
        with pytest.raises(LiveRefusal) as refused:
            llm.generate_text(text_request())
    assert codes(refused.value) == {"endpoint_boundary_violation"}
    assert_untouched(wire, gate)
    assert client_spans(telemetry, stub) == []


def test_a_retry_or_second_request_stays_impossible_on_the_audited_pool(telemetry, live_http, wire):
    """The control: the correction refuses substitutes without loosening the one-request limit."""
    wire.steps.append(ok())
    gate = session()
    llm = guarded(telemetry, live_http, gate)
    llm.generate_text(text_request())
    with pytest.raises(LiveRefusal) as refused:
        llm.generate_text(text_request())
    assert codes(refused.value) == {"request_limit_reached"}
    assert len(wire.requests) == 1
    assert gate.snapshot()["physical_attempts"] == 1


# Nothing leaks through a refusal of this kind
def _leaky_pool(genuine: Any) -> Any:
    """Every read of the four attributes raises an error holding credentials and content."""

    def leak(self: Any) -> Any:
        raise RuntimeError(f"{FAKE_GROQ_KEY} Authorization: Bearer x {PROMPT_SENTINEL}")

    properties = {name: property(leak) for name in UNSET_ATTRIBUTES}
    return _faithful_copy(genuine, _substitute_class(**properties))


def _assert_no_leak(value: BaseException, gate: FreePlanSession, caplog, spans: list[dict]) -> None:
    text = _serialized(str(value), repr(value), gate.snapshot(), repr(gate), spans) + caplog.text
    for secret in (*SECRETS, "Bearer", "Authorization"):
        assert secret not in text
    assert value.__cause__ is None and value.__context__ is None


def test_a_pool_refusal_at_the_send_carries_no_key_or_content(caplog, telemetry, stub, monkeypatch):
    caplog.set_level(logging.DEBUG)
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        gate = session()
        llm = guarded(telemetry, http, gate)
        substitute = _leaky_pool(genuine)
        changing_during_build(
            monkeypatch, http, lambda: setattr(http._transport, "_pool", substitute)
        )
        with pytest.raises(LiveRefusal) as refused:
            llm.generate_text(text_request())
    assert type(substitute).reached == []
    _assert_no_leak(refused.value, gate, caplog, client_spans(telemetry, stub))


def test_a_pool_refusal_of_the_client_carries_no_key_or_content(caplog, telemetry, stub):
    caplog.set_level(logging.DEBUG)
    with httpx.Client(**client_kw()) as http, preserved_pool(http) as genuine:
        gate = session()
        llm = guarded(telemetry, http, gate)
        http._transport._pool = _leaky_pool(genuine)
        with pytest.raises(ConfigError) as refused:
            llm.generate_text(text_request())
    _assert_no_leak(refused.value, gate, caplog, client_spans(telemetry, stub))


# ---------------------------------------------------------------- 8A-4: the credential is bound
OTHER_KEY = "synthetic" + "-groq-other-" + "1" * 24


def test_a_client_holding_another_key_than_the_validated_one_is_refused(
    caplog, telemetry, stub, live_http, wire
):
    caplog.set_level(logging.DEBUG)
    gate = session()
    llm = GroqTextAdapter(
        ObservedGroqClient(
            telemetry, OTHER_KEY, http_client=live_http, provenance="real", send_gate=gate
        )
    )
    with pytest.raises(LiveRefusal) as refused:
        llm.generate_text(text_request())
    assert codes(refused.value) == {"credential_not_bound"}
    assert_untouched(wire, gate)
    assert not groq_live._PROCESS_SLOT.locked()
    for secret in (FAKE_GROQ_KEY, OTHER_KEY):
        assert secret not in str(refused.value) and secret not in caplog.text
    assert client_spans(telemetry, stub) == []


def test_the_key_validated_at_the_send_is_compared_not_the_one_at_construction(
    telemetry, live_http, wire
):
    env = env_with()
    gate = session(env)
    llm = guarded(telemetry, live_http, gate)
    env["GROQ_API_KEY"] = OTHER_KEY  # still a valid shape, but not the key the client holds
    with pytest.raises(LiveRefusal) as refused:
        llm.generate_text(text_request())
    assert codes(refused.value) == {"credential_not_bound"}
    assert_untouched(wire, gate)


def test_surrounding_whitespace_is_read_as_the_policy_reads_it(telemetry, live_http, wire):
    wire.steps.append(ok())
    gate = session(env_with(GROQ_API_KEY=f" {FAKE_GROQ_KEY}\n"))
    assert guarded(telemetry, live_http, gate).generate_text(text_request()).status == "completed"
    assert wire.requests[0].headers.get_list("authorization") == [f"Bearer {FAKE_GROQ_KEY}"]


def test_api_key_value_reads_exactly_what_the_policy_validates():
    assert groq_live.api_key_value({"GROQ_API_KEY": f"\t{FAKE_GROQ_KEY} "}) == FAKE_GROQ_KEY
    assert groq_live.api_key_value({}) == ""
    assert groq_live.api_key_value({"GROQ_API_KEY": "   "}) == ""


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(f"Bearer  {FAKE_GROQ_KEY}", id="two-spaces"),
        pytest.param(f"bearer {FAKE_GROQ_KEY}", id="lowercase-scheme"),
        pytest.param(f"Bearer {FAKE_GROQ_KEY} ", id="trailing-space"),
        pytest.param(f"Bearer {FAKE_GROQ_KEY[:-1]}", id="truncated"),
        pytest.param(f"Bearer {FAKE_GROQ_KEY}x", id="extended"),
        pytest.param(f"Basic {FAKE_GROQ_KEY}", id="other-scheme"),
        pytest.param(FAKE_GROQ_KEY, id="no-scheme"),
    ],
)
def test_only_the_exact_bearer_credential_is_authorized(live_http, authorization):
    headers = {**HEADERS, "Authorization": authorization}
    gate = session()
    with pytest.raises(LiveRefusal) as refused:
        gate.authorize_send(live_http, pinned_request(live_http, headers=headers))
    assert "credential_not_bound" in codes(refused.value)
    assert not groq_live._PROCESS_SLOT.locked()


def test_a_non_ascii_credential_is_never_authorized(live_http):
    headers = {**HEADERS, "Authorization": "Bearer key-é".encode("latin-1")}
    gate = session(env_with(GROQ_API_KEY="key-é"))
    with pytest.raises(LiveRefusal):
        gate.authorize_send(live_http, pinned_request(live_http, headers=headers))
    assert not groq_live._PROCESS_SLOT.locked()


def test_the_exact_validated_credential_is_authorized(live_http):
    assert session().authorize_send(live_http, pinned_request(live_http)) is not None


def test_the_credential_helper_never_binds_an_empty_key(live_http):
    """The policy refuses a missing key first; the helper must still never match `Bearer `."""
    headers = {**HEADERS, "Authorization": "Bearer "}
    request = pinned_request(live_http, headers=headers)
    assert groq_live._credential_bound(request, {}) is False
    assert groq_live._credential_bound(request, {"GROQ_API_KEY": "   "}) is False


def test_the_credential_helper_refuses_non_ascii_without_raising(live_http):
    request = pinned_request(
        live_http, headers={**HEADERS, "Authorization": "Bearer kéy".encode("latin-1")}
    )
    assert groq_live._credential_bound(request, {"GROQ_API_KEY": "kéy"}) is False
    request = pinned_request(live_http)
    assert groq_live._credential_bound(request, {"GROQ_API_KEY": "kéy"}) is False
    assert groq_live._credential_bound(request, {"GROQ_API_KEY": FAKE_GROQ_KEY}) is True
