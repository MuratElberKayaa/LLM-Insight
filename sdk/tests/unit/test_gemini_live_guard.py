"""Gemini Free Tier execution guard (Phase 6C-A, ADR-35): fail-closed policy, operator
attestation, hard caps and the per-process session. Pure logic: no network, no provider,
no clock dependency (every test passes `now`)."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from llm_insight_sdk import gemini_live, live
from llm_insight_sdk.instrumentation import TokenUsage
from llm_insight_sdk.providers import AttemptRecord
from tests.gemini_fixtures import FAKE_GEMINI_KEY, MODEL

NOW = gemini_live.FREE_TIER_VERIFIED_AT + timedelta(hours=2)
TODAY = NOW.date().isoformat()


def gemini_env(**overrides: str | None) -> dict[str, str]:
    env = {
        "LLMI_PROVIDER_MODE": "real",
        "LLMI_GEMINI_MODE": gemini_live.GEMINI_MODE,
        "LLMI_LIVE_CONSENT": gemini_live.CONSENT_PHRASE,
        "GEMINI_API_KEY": FAKE_GEMINI_KEY,
        "LLMI_GEMINI_BILLING_TIER": "free",
        "LLMI_GEMINI_TIER_CHECKED_ON": TODAY,
        "LLMI_LIVE_PROVIDERS": "gcp.gemini",
        "LLMI_LIVE_MODELS": MODEL,
        "LLMI_LIVE_MAX_REQUESTS": "1",
        "LLMI_LIVE_MAX_INPUT_TOKENS": "256",
        "LLMI_LIVE_MAX_OUTPUT_TOKENS": "256",
    }
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def codes(decision: gemini_live.FreeTierDecision) -> set[str]:
    return {r.code for r in decision.refusals}


def policy(**overrides: str | None) -> gemini_live.FreeTierPolicy:
    decision = gemini_live.evaluate_free_tier_policy(gemini_env(**overrides), now=NOW)
    assert decision.allowed, decision.refusals
    assert decision.policy is not None
    return decision.policy


# ---------------------------------------------------------------- policy: default off
def test_complete_explicit_configuration_is_allowed_with_the_hard_caps():
    p = policy()
    assert p.models == (MODEL,) and p.providers == frozenset({"gcp.gemini"})
    assert (p.max_requests, p.max_retries, p.max_attempts, p.max_concurrency) == (1, 0, 1, 1)
    assert p.timeout_seconds == gemini_live.DEFAULT_TIMEOUT_SECONDS
    assert p.tier_checked_on.isoformat() == TODAY


def test_real_execution_is_off_by_default():
    decision = gemini_live.evaluate_free_tier_policy({}, now=NOW)
    assert not decision.allowed and decision.policy is None
    assert {
        "provider_mode_not_real",
        "gemini_mode_not_free_tier",
        "consent_missing",
        "api_key_missing",
        "free_tier_unverified",
        "setting_missing",
    } <= codes(decision)


def test_an_api_key_alone_never_enables_execution():
    decision = gemini_live.evaluate_free_tier_policy({"GEMINI_API_KEY": FAKE_GEMINI_KEY}, now=NOW)
    assert not decision.allowed
    assert "api_key_missing" not in codes(decision)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real"),
        ({"LLMI_GEMINI_MODE": None}, "gemini_mode_not_free_tier"),
        ({"LLMI_GEMINI_MODE": "paid"}, "gemini_mode_not_free_tier"),
        ({"LLMI_GEMINI_MODE": "FREE-TIER"}, "gemini_mode_not_free_tier"),
        ({"LLMI_LIVE_CONSENT": None}, "consent_missing"),
        ({"LLMI_LIVE_CONSENT": live.CONSENT_PHRASE}, "consent_missing"),  # OpenAI's phrase
        ({"GEMINI_API_KEY": None}, "api_key_missing"),
        ({"GEMINI_API_KEY": "  "}, "api_key_missing"),
        ({"LLMI_GEMINI_BILLING_TIER": None}, "free_tier_unverified"),
        ({"LLMI_GEMINI_BILLING_TIER": "unknown"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_BILLING_TIER": "paid"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_BILLING_TIER": "tier1"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_BILLING_TIER": "postpay"}, "billing_tier_not_free"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": None}, "free_tier_unverified"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "yesterday"}, "free_tier_unverified"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-10-28T10:00"}, "free_tier_unverified"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-10-29"}, "free_tier_attestation_invalid"),
        ({"LLMI_GEMINI_TIER_CHECKED_ON": "2026-10-26"}, "free_tier_attestation_stale"),
        ({"LLMI_LIVE_PROVIDERS": "openai"}, "provider_not_allowed"),
        ({"LLMI_LIVE_PROVIDERS": "gcp.gemini,gcp.vertex_ai"}, "provider_not_allowed"),
        ({"LLMI_LIVE_PROVIDERS": None}, "setting_missing"),
        ({"LLMI_LIVE_MODELS": "gemini-3.5-flash-lite"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MODELS": "gemini-3.1-pro-preview"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MODELS": "gemini-4-argon"}, "model_not_free_tier_verified"),  # paid only
        ({"LLMI_LIVE_MODELS": "gpt-5.4-mini"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MODELS": f"{MODEL},gemini-2.5-pro"}, "model_not_free_tier_verified"),
        ({"LLMI_LIVE_MAX_REQUESTS": "2"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUESTS": None}, "setting_missing"),
        ({"LLMI_LIVE_MAX_RETRIES": "1"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_CONCURRENCY": "2"}, "setting_invalid"),
        ({"LLMI_LIVE_TIMEOUT_SECONDS": "31"}, "setting_invalid"),
        ({"LLMI_LIVE_TIMEOUT_SECONDS": "nan"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "2048"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "8"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_INPUT_TOKENS": "4096"}, "setting_invalid"),
    ],
)
def test_each_gate_refuses_on_its_own(overrides, code):
    decision = gemini_live.evaluate_free_tier_policy(gemini_env(**overrides), now=NOW)
    assert not decision.allowed
    assert code in codes(decision)
    for refusal in decision.refusals:
        assert FAKE_GEMINI_KEY not in refusal.message


@pytest.mark.parametrize("var", gemini_live.CLIENT_ENV_OVERRIDES)
def test_every_client_environment_override_is_refused(var):
    decision = gemini_live.evaluate_free_tier_policy(gemini_env(**{var: "SYNTHETIC"}), now=NOW)
    assert not decision.allowed and codes(decision) == {"client_env_override"}


def test_the_attestation_may_be_from_yesterday_but_not_older():
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    assert policy(LLMI_GEMINI_TIER_CHECKED_ON=yesterday).tier_checked_on.isoformat() == yesterday


def test_the_billing_tier_statement_is_case_insensitive_free_only():
    assert policy(LLMI_GEMINI_BILLING_TIER="Free").models == (MODEL,)


def test_free_tier_evidence_window():
    late = gemini_live.FREE_TIER_VERIFIED_AT + gemini_live.FREE_TIER_MAX_AGE + timedelta(seconds=1)
    env = gemini_env(LLMI_GEMINI_TIER_CHECKED_ON=late.date().isoformat())
    assert "free_tier_evidence_stale" in codes(gemini_live.evaluate_free_tier_policy(env, now=late))
    edge = gemini_live.FREE_TIER_VERIFIED_AT + gemini_live.FREE_TIER_MAX_AGE
    env = gemini_env(LLMI_GEMINI_TIER_CHECKED_ON=edge.date().isoformat())
    assert gemini_live.evaluate_free_tier_policy(env, now=edge).allowed
    early = gemini_live.FREE_TIER_VERIFIED_AT - timedelta(seconds=1)
    env = gemini_env(LLMI_GEMINI_TIER_CHECKED_ON=early.date().isoformat())
    assert "free_tier_evidence_not_yet_valid" in codes(
        gemini_live.evaluate_free_tier_policy(env, now=early)
    )


def test_openai_live_settings_never_enable_gemini_and_vice_versa():
    """The two guards are independent: each has its own consent phrase and allowlist."""
    openai_env = {
        "LLMI_PROVIDER_MODE": "real",
        "LLMI_LIVE_CONSENT": live.CONSENT_PHRASE,
        "LLMI_LIVE_PROVIDERS": "openai",
        "LLMI_LIVE_MODELS": "gpt-5.4-mini",
    }
    assert not gemini_live.evaluate_free_tier_policy(
        {**gemini_env(), **openai_env}, now=NOW
    ).allowed
    openai_now = live.PRICE_VERIFIED_AT + timedelta(days=1)
    assert not live.evaluate_policy(gemini_env(), now=openai_now).allowed


# ---------------------------------------------------------------- session
def _session(**overrides: str | None) -> gemini_live.FreeTierSession:
    return gemini_live.FreeTierSession(policy(**overrides))


def _reserve(s: gemini_live.FreeTierSession, **kw) -> gemini_live.FreeTierReservation:
    kw.setdefault("provider", "gcp.gemini")
    kw.setdefault("model", MODEL)
    kw.setdefault("input_text", "SYNTHETIC")
    kw.setdefault("max_output_tokens", 256)
    return s.reserve(**kw)


def test_the_session_admits_exactly_one_request():
    s = _session()
    r = _reserve(s)
    assert r.input_bound == len(b"SYNTHETIC") + live.FRAMING_ALLOWANCE_TOKENS
    s.settle(r, [AttemptRecord(1)])
    with pytest.raises(live.LiveRefusal) as exc:
        _reserve(s)
    assert exc.value.refusals[0].code == "request_limit_reached"
    assert s.snapshot() == {
        "requests_started": 1,
        "max_requests": 1,
        "physical_attempts": 1,
        "halted": None,
    }


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"provider": "openai"}, "provider_not_allowed"),
        ({"model": "gemini-3.5-flash-lite"}, "model_not_allowed"),
        ({"max_output_tokens": 257}, "output_limit_exceeded"),
        ({"input_text": "x" * 300}, "input_too_large"),
    ],
)
def test_reservation_refusals_send_nothing(kw, code):
    s = _session()
    with pytest.raises(live.LiveRefusal) as exc:
        _reserve(s, **kw)
    assert code in {r.code for r in exc.value.refusals}
    assert s.snapshot()["requests_started"] == 0


def test_concurrency_is_one_and_never_queued():
    s = _session()
    results: list[str] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        try:
            _reserve(s)
            results.append("ok")
        except live.LiveRefusal as exc:
            results.append(exc.refusals[0].code)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1 and len(results) == 4


@pytest.mark.parametrize(
    ("attempts", "halt"),
    [
        ([AttemptRecord(1, usage=TokenUsage(20, 32))], None),
        ([AttemptRecord(1)], None),  # a failed attempt: no usage, nothing to book
        ([AttemptRecord(1, usage=TokenUsage(10_000, 1))], "usage_exceeded_bound"),
        ([AttemptRecord(1, usage=TokenUsage(1, 257))], "usage_exceeded_bound"),
        ([AttemptRecord(1), AttemptRecord(2)], "attempts_exceeded_bound"),
    ],
)
def test_settlement_halts_when_the_provider_reports_more_than_the_bounds(attempts, halt):
    s = _session()
    r = _reserve(s)
    assert s.settle(r, attempts).halted == halt
    assert s.snapshot()["halted"] == halt
    with pytest.raises(ValueError):
        s.settle(r, attempts)  # each reservation is settled once
