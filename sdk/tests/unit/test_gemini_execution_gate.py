"""The independent Gemini execution switch (ADR-50), tested exactly as shipped.

No test here uses the `gemini_released` fixture: `gemini_live.REAL_EXECUTION_ENABLED` keeps its
shipped value unless a test sets it explicitly to show what the other gates still refuse. The
clock is inside the Free Tier evidence window and every other setting is valid, so the switch is
the only thing that can refuse. Only `httpx.HTTPTransport.handle_request` is replaced (`wire`),
and every non-loopback connection fails the test.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_insight_sdk import (
    gemini_live,
    groq_adapter,
    groq_live,
    openrouter_adapter,
    openrouter_live,
)
from llm_insight_sdk.live import LiveRefusal
from tests.conftest import DUMMY_KEY
from tests.gemini_fixtures import FAKE_GEMINI_KEY, MODEL
from tests.openai_fixtures import block_network
from tests.unit.test_gemini_live_guard import gemini_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))  # demo_app lives at the repository root, outside the SDK

from demo_app import gemini_scenarios, live_compare, live_gemini, live_gemini_content  # noqa: E402

NOW = gemini_live.FREE_TIER_VERIFIED_AT + timedelta(hours=2)  # valid evidence
SHIPPED = gemini_live.REAL_EXECUTION_ENABLED


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
def wire(monkeypatch) -> list[httpx.Request]:
    sent: list[httpx.Request] = []

    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise AssertionError("no Gemini request may be sent while the switch is off")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    return sent


class NeverBuilt:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any) -> Any:
        self.calls += 1
        raise AssertionError("no Gemini client may be built while the switch is off")


def valid_env(endpoint: str, **overrides: str | None) -> dict[str, str]:
    """Everything the Free Tier policy needs, valid on `NOW`, plus telemetry for the runners."""
    settings: dict[str, str | None] = {
        "LLMI_LIVE_MAX_INPUT_TOKENS": "1024",
        "LLMI_LIVE_MAX_OUTPUT_TOKENS": "1024",
        "LLMI_GEMINI_TIER_CHECKED_ON": NOW.date().isoformat(),
        **overrides,
    }
    env = gemini_env(**settings)
    env.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": f"X-Project-Key={DUMMY_KEY}",
            "OTEL_SERVICE_NAME": "gemini-execution-gate-test",
            "LLMI_SDK_SCHEDULE_DELAY_MILLIS": "50",
            "LLMI_DASHBOARD_PROJECT_ID": "1",
        }
    )
    return env


def codes(refusals: Any) -> set[str]:
    return {r["code"] if isinstance(r, dict) else r.code for r in refusals}


def decide(env: dict[str, str]) -> gemini_live.FreeTierDecision:
    return gemini_live.evaluate_free_tier_policy(env, now=NOW)


def emitted(out: io.StringIO) -> dict[str, Any]:
    text = out.getvalue()
    for secret in (FAKE_GEMINI_KEY, DUMMY_KEY):
        assert secret not in text
    (line,) = text.splitlines()
    return json.loads(line)


# ---------------------------------------------------------------- the shipped switch
def test_the_switch_ships_false():
    assert SHIPPED is False
    assert gemini_live.REAL_EXECUTION_ENABLED is False


def test_off_it_is_the_only_refusal_of_an_otherwise_valid_request(stub):
    """Valid evidence (clock inside the window), attestation, consent, key, model and caps."""
    decision = decide(valid_env(stub.endpoint))
    assert not decision.allowed and codes(decision.refusals) == {"real_execution_disabled"}


def test_valid_evidence_alone_cannot_enable_it(stub, monkeypatch):
    monkeypatch.setattr(gemini_live, "FREE_TIER_VERIFIED_AT", NOW - timedelta(minutes=1))
    assert "real_execution_disabled" in codes(decide(valid_env(stub.endpoint)).refusals)


def test_valid_consent_alone_cannot_enable_it(stub):
    env = valid_env(stub.endpoint, LLMI_LIVE_CONSENT=gemini_live.CONSENT_PHRASE)
    assert codes(decide(env).refusals) == {"real_execution_disabled"}


@pytest.mark.parametrize(
    "name",
    [
        "REAL_EXECUTION_ENABLED",
        "LLMI_GEMINI_REAL_EXECUTION_ENABLED",
        "LLMI_REAL_EXECUTION_ENABLED",
        "GEMINI_REAL_EXECUTION_ENABLED",
        "LLMI_GEMINI_EXECUTION",
    ],
)
def test_no_environment_variable_can_enable_it(stub, monkeypatch, name):
    for value in ("1", "true", "True", "yes", "on"):
        monkeypatch.setenv(name, value)
        env = valid_env(stub.endpoint, **{name: value})
        assert "real_execution_disabled" in codes(decide(env).refusals)
        assert "real_execution_disabled" in codes(
            gemini_live.evaluate_free_tier_policy(now=NOW).refusals
        )


def test_the_module_reads_the_switch_from_code_only():
    source = Path(gemini_live.__file__).read_text(encoding="utf-8")
    assert source.count("REAL_EXECUTION_ENABLED: bool = False") == 1
    for line in source.splitlines():
        if "REAL_EXECUTION_ENABLED" in line:
            assert "environ" not in line and "getenv" not in line and "r.raw(" not in line


def test_the_session_refuses_even_a_policy_built_another_way():
    """`run_single_request` takes a policy object; the session checks the switch itself."""
    policy = gemini_live.FreeTierPolicy(
        providers=frozenset({gemini_live.PROVIDER}),
        models=(MODEL,),
        max_requests=1,
        max_input_tokens=1024,
        max_output_tokens=1024,
        timeout_seconds=20.0,
        max_retries=0,
        max_concurrency=1,
        tier_checked_on=NOW.date(),
    )
    session = gemini_live.FreeTierSession(policy)
    with pytest.raises(LiveRefusal) as refused:
        session.reserve(
            provider=gemini_live.PROVIDER, model=MODEL, input_text="SYNTHETIC", max_output_tokens=64
        )
    assert codes(refused.value.refusals) == {"real_execution_disabled"}
    assert session.snapshot()["requests_started"] == 0


# ---------------------------------------------------------------- every runner stays blocked
def test_the_smoke_runner_is_blocked_before_any_client_or_send(stub, wire):
    never = NeverBuilt()
    out = io.StringIO()
    code = live_gemini.main(
        ["--execute"], valid_env(stub.endpoint), now=NOW, client_factory=never, out=out
    )
    doc = emitted(out)
    assert code == 2 and doc["provider_request_sent"] is False and doc["execution_allowed"] is False
    assert codes(doc["refusals"]) == {"real_execution_disabled"}
    assert never.calls == 0 and wire == [] and stub.spans() == []


def test_the_smoke_runner_shared_path_is_blocked_too(stub, wire):
    """The shared `run_single_request`, called directly with a policy, refuses at reservation."""
    import llm_insight_sdk as lli

    gemini_live_policy = gemini_live.FreeTierPolicy(
        providers=frozenset({gemini_live.PROVIDER}),
        models=(MODEL,),
        max_requests=1,
        max_input_tokens=1024,
        max_output_tokens=1024,
        timeout_seconds=20.0,
        max_retries=0,
        max_concurrency=1,
        tier_checked_on=NOW.date(),
    )
    never = NeverBuilt()
    result = live_gemini.run_single_request(
        policy=gemini_live_policy,
        config=lli.LLMInsightConfig.from_env(valid_env(stub.endpoint)),
        model=MODEL,
        input_text="SYNTHETIC",
        max_output_tokens=64,
        prompt_name="gate",
        prompt_version="v1",
        root_span="demo.gate",
        environ=valid_env(stub.endpoint),
        client_factory=never,
    )
    assert codes(result.refusals) == {"real_execution_disabled"}
    assert result.send_started is False and result.outcome is None
    assert never.calls == 0 and wire == [] and stub.spans() == []


def test_the_content_runner_is_blocked(stub, wire):
    scenario = next(iter(gemini_scenarios.SCENARIOS))
    never = NeverBuilt()
    out, err = io.StringIO(), io.StringIO()
    code = live_gemini_content.main(
        ["--scenario", scenario, "--execute"],
        valid_env(stub.endpoint),
        now=NOW,
        client_factory=never,
        out=out,
        err=err,
    )
    doc = emitted(out)
    assert code == 2 and "real_execution_disabled" in codes(doc["refusals"])
    assert never.calls == 0 and wire == [] and stub.spans() == []


@pytest.mark.parametrize("mode", [[], ["--execute"]])
def test_the_comparison_runner_is_blocked_and_reports_the_lock(stub, wire, mode):
    out = io.StringIO()
    argv = ["--provider", "gemini", "--scenario", "cmp-ticket-triage", *mode]
    code = live_compare.main(argv, valid_env(stub.endpoint), now=NOW, out=out)
    doc = emitted(out)
    assert codes(doc["refusals"]) == {"real_execution_disabled"}
    assert doc["constraints"]["real_execution_enabled"] is False
    assert doc["execution_allowed"] is False and doc["provider_request_state"] == "not_sent"
    assert code == (0 if not mode else 2)
    assert wire == [] and stub.spans() == []
    assert not groq_live._PROCESS_SLOT.locked()  # the shared slot is never taken


def test_the_comparison_send_path_rechecks_the_switch(stub, wire, monkeypatch):
    """Released at planning time, switched off before the send: refused, nothing sent."""
    monkeypatch.setattr(gemini_live, "REAL_EXECUTION_ENABLED", True)
    decision = live_compare.evaluate_gemini(valid_env(stub.endpoint), NOW)
    assert decision.policy is not None and decision.refusals == ()
    monkeypatch.setattr(gemini_live, "REAL_EXECUTION_ENABLED", False)
    import llm_insight_sdk as lli

    result = live_compare.run_gemini_request(
        live_compare.GEMINI,
        policy=decision.policy,
        config=lli.LLMInsightConfig.from_env(valid_env(stub.endpoint)),
        scenario=live_compare.comparison_scenarios.SCENARIOS["cmp-ticket-triage"],
        input_text="SYNTHETIC",
        environ=valid_env(stub.endpoint),
        clock=lambda: NOW,
    )
    assert codes(result.refusals) == {"real_execution_disabled"}
    assert result.request_state == "not_sent" and wire == [] and stub.spans() == []
    assert not groq_live._PROCESS_SLOT.locked()


# ---------------------------------------------------------------- the other gates still hold
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_GEMINI_BILLING_TIER": "paid"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-W44-3"}, "free_tier_unverified"),  # R123 (1)
        ({"SSLKEYLOGFILE": "SYNTHETIC-keys.log"}, "client_env_override"),  # R123 (2)
        ({"LLMI_LIVE_MODELS": "gemini-2.5-pro"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUESTS": "2"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "2048"}, "setting_invalid"),
        ({"LLMI_LIVE_TIMEOUT_SECONDS": "31"}, "setting_invalid"),
        ({"LLMI_LIVE_CONSENT": "groq-free-plan-single-call"}, "consent_missing"),
        ({"GOOGLE_GENAI_USE_VERTEXAI": "true"}, "client_env_override"),
    ],
)
def test_switched_on_every_free_tier_safeguard_still_refuses(stub, monkeypatch, overrides, code):
    monkeypatch.setattr(gemini_live, "REAL_EXECUTION_ENABLED", True)
    decision = decide(valid_env(stub.endpoint, **overrides))
    assert not decision.allowed and code in codes(decision.refusals)
    assert "real_execution_disabled" not in codes(decision.refusals)


def test_switched_on_expired_evidence_still_refuses(stub, monkeypatch):
    monkeypatch.setattr(gemini_live, "REAL_EXECUTION_ENABLED", True)
    stale = gemini_live.FREE_TIER_VERIFIED_AT + gemini_live.FREE_TIER_MAX_AGE + timedelta(minutes=1)
    env = valid_env(stub.endpoint, LLMI_GEMINI_TIER_CHECKED_ON=stale.date().isoformat())
    decision = gemini_live.evaluate_free_tier_policy(env, now=stale)
    assert codes(decision.refusals) == {"free_tier_evidence_stale"}


def test_the_shipped_evidence_and_allowlist_are_unchanged():
    assert gemini_live.FREE_TIER_VERIFIED_AT.isoformat() == "2026-10-28T13:00:00+00:00"
    assert list(gemini_live.FREE_TIER_MODELS) == [MODEL]
    assert gemini_live.MAX_REQUESTS_CAP == 1 and gemini_live.RETRIES_RANGE == (0, 0)
    assert gemini_live.MAX_OUTPUT_TOKENS_CAP == 1024


# ---------------------------------------------------------------- Groq and OpenRouter unchanged
def test_the_gemini_switch_releases_neither_groq_nor_openrouter(monkeypatch):
    monkeypatch.setattr(gemini_live, "REAL_EXECUTION_ENABLED", True)
    assert groq_adapter.REAL_EXECUTION_ENABLED is False
    assert openrouter_adapter.REAL_EXECUTION_ENABLED is False
    assert list(groq_live.FREE_PLAN_MODELS) == ["openai/gpt-oss-20b"]  # evidence only (8G)
    assert openrouter_live.FREE_MODELS == {}
    assert openrouter_live.ZERO_PRICE_ROUTING_VERIFIED_AT is None
    groq = groq_live.evaluate_free_plan_policy({}, now=NOW)
    openrouter = openrouter_live.evaluate_free_model_policy({}, now=NOW)
    assert "real_execution_disabled" in codes(groq.refusals)
    assert "real_execution_disabled" in codes(openrouter.refusals)
