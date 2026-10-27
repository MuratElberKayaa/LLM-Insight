"""Live execution guard (Phase 6A, ADR-29): fail-closed policy, Decimal cost bounds and the
per-process session ledger. Pure logic: no network, no provider, no clock dependency (every
test passes `now`)."""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from llm_insight_sdk import live
from llm_insight_sdk.instrumentation import TokenUsage
from llm_insight_sdk.providers import AttemptRecord
from tests.openai_fixtures import FAKE_API_KEY

NOW = live.PRICE_VERIFIED_AT + timedelta(days=1)
MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
T19 = TokenUsage(
    input_tokens=1200, output_tokens=300, cache_read_input_tokens=1000, reasoning_output_tokens=200
)


def live_env(**overrides: str | None) -> dict[str, str]:
    env = {
        "LLMI_PROVIDER_MODE": "real",
        "LLMI_LIVE_CONSENT": live.CONSENT_PHRASE,
        "OPENAI_API_KEY": FAKE_API_KEY,
        "LLMI_LIVE_PROVIDERS": "openai",
        "LLMI_LIVE_MODELS": MODEL,
        "LLMI_LIVE_MAX_REQUESTS": "3",
        "LLMI_LIVE_MAX_INPUT_TOKENS": "512",
        "LLMI_LIVE_MAX_OUTPUT_TOKENS": "64",
        "LLMI_LIVE_MAX_REQUEST_COST_USD": "0.01",
        "LLMI_REAL_LLM_BUDGET_USD": "0.05",
    }
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def codes(decision: live.PolicyDecision) -> set[str]:
    return {r.code for r in decision.refusals}


def policy(**overrides: str | None) -> live.LivePolicy:
    decision = live.evaluate_policy(live_env(**overrides), now=NOW)
    assert decision.allowed, decision.refusals
    assert decision.policy is not None
    return decision.policy


# ---------------------------------------------------------------- policy: default off
def test_complete_explicit_configuration_is_allowed():
    p = policy()
    assert p.providers == frozenset({"openai"}) and p.models == (MODEL,)
    assert (p.max_requests, p.max_input_tokens, p.max_output_tokens) == (3, 512, 64)
    assert p.max_request_cost_usd == Decimal("0.01") and p.session_budget_usd == Decimal("0.05")
    assert isinstance(p.max_request_cost_usd, Decimal)
    assert (p.timeout_seconds, p.max_retries, p.max_concurrency, p.max_attempts) == (20.0, 0, 1, 1)


def test_real_mode_is_off_by_default():
    decision = live.evaluate_policy({}, now=NOW)
    assert not decision.allowed and decision.policy is None
    assert {"provider_mode_not_real", "consent_missing", "api_key_missing"} <= codes(decision)


def test_an_api_key_alone_never_enables_execution():
    decision = live.evaluate_policy({"OPENAI_API_KEY": FAKE_API_KEY}, now=NOW)
    assert not decision.allowed
    assert "api_key_missing" not in codes(decision)
    assert {"provider_mode_not_real", "consent_missing", "setting_missing"} <= codes(decision)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"LLMI_PROVIDER_MODE": "mock"}, "provider_mode_not_real"),
        ({"LLMI_PROVIDER_MODE": None}, "provider_mode_not_real"),
        ({"LLMI_LIVE_CONSENT": "yes"}, "consent_missing"),
        ({"LLMI_LIVE_CONSENT": live.CONSENT_PHRASE.upper()}, "consent_missing"),
        ({"OPENAI_API_KEY": None}, "api_key_missing"),
        ({"OPENAI_API_KEY": "   "}, "api_key_missing"),
        ({"OPENAI_BASE_URL": "https://proxy.example.invalid/v1"}, "base_url_override"),
        ({"LLMI_LIVE_PROVIDERS": "openai,anthropic"}, "provider_not_allowed"),
        ({"LLMI_LIVE_PROVIDERS": ""}, "setting_missing"),
        ({"LLMI_LIVE_MODELS": "gpt-5.5"}, "model_price_unknown"),
        ({"LLMI_LIVE_MODELS": f"{MODEL},gpt-6-preview"}, "model_price_unknown"),
        ({"LLMI_LIVE_MODELS": " , "}, "setting_missing"),
        ({"LLMI_LIVE_MAX_REQUESTS": None}, "setting_missing"),
        ({"LLMI_LIVE_MAX_REQUESTS": "0"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUESTS": "21"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUESTS": "two"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_INPUT_TOKENS": "0"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_INPUT_TOKENS": "8193"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": None}, "setting_missing"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "8"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_OUTPUT_TOKENS": "4097"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": None}, "setting_missing"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "0"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "-0.01"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "NaN"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "Infinity"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "1.01"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_REQUEST_COST_USD": "ten cents"}, "setting_invalid"),
        ({"LLMI_REAL_LLM_BUDGET_USD": None}, "setting_missing"),
        ({"LLMI_REAL_LLM_BUDGET_USD": "5.01"}, "setting_invalid"),
        ({"LLMI_LIVE_TIMEOUT_SECONDS": "0.5"}, "setting_invalid"),
        ({"LLMI_LIVE_TIMEOUT_SECONDS": "nan"}, "setting_invalid"),
        ({"LLMI_LIVE_TIMEOUT_SECONDS": "61"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_RETRIES": "3"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_RETRIES": "-1"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_CONCURRENCY": "0"}, "setting_invalid"),
        ({"LLMI_LIVE_MAX_CONCURRENCY": "5"}, "setting_invalid"),
    ],
)
def test_each_gate_refuses_on_its_own(overrides, code):
    decision = live.evaluate_policy(live_env(**overrides), now=NOW)
    assert not decision.allowed
    assert code in codes(decision)


def test_stale_or_future_price_evidence_refuses():
    late = live.PRICE_VERIFIED_AT + live.PRICE_MAX_AGE + timedelta(seconds=1)
    assert "price_catalogue_stale" in codes(live.evaluate_policy(live_env(), now=late))
    early = live.PRICE_VERIFIED_AT - timedelta(seconds=1)
    assert "price_not_yet_valid" in codes(live.evaluate_policy(live_env(), now=early))
    edge = live.PRICE_VERIFIED_AT + live.PRICE_MAX_AGE
    assert live.evaluate_policy(live_env(), now=edge).allowed


def test_unsatisfiable_limits_refuse_before_any_request():
    tiny_cap = live.evaluate_policy(live_env(LLMI_LIVE_MAX_REQUEST_COST_USD="0.0001"), now=NOW)
    assert "request_cost_bound_exceeds_limit" in codes(tiny_cap)
    tiny_budget = live.evaluate_policy(live_env(LLMI_REAL_LLM_BUDGET_USD="0.0001"), now=NOW)
    assert "session_budget_too_small" in codes(tiny_budget)
    # Retries multiply the worst case: 3 attempts no longer fit a cap that 1 attempt fits.
    bound = live.request_bound(policy(), live.VERIFIED_PRICES[("openai", MODEL)], 512)
    cap = str(bound * 2)
    assert live.evaluate_policy(live_env(LLMI_LIVE_MAX_REQUEST_COST_USD=cap), now=NOW).allowed
    with_retries = live_env(LLMI_LIVE_MAX_REQUEST_COST_USD=cap, LLMI_LIVE_MAX_RETRIES="2")
    assert "request_cost_bound_exceeds_limit" in codes(live.evaluate_policy(with_retries, now=NOW))


def test_refusals_never_contain_the_key_or_values():
    secret = "sk" + "-proj-" + "S3CRET" * 8  # assembled: no key-shaped literal
    env = live_env(
        OPENAI_API_KEY=secret,
        LLMI_LIVE_CONSENT="i-agree-" + secret,
        LLMI_LIVE_MAX_REQUEST_COST_USD="NaN-" + secret,
    )
    decision = live.evaluate_policy(env, now=NOW)
    assert not decision.allowed
    assert secret not in repr(decision) and "S3CRET" not in repr(decision)


# ---------------------------------------------------------------- cost arithmetic
def test_estimate_matches_the_backend_worked_example():
    price = live.VERIFIED_PRICES[("openai", SNAPSHOT)]
    assert live.estimate_cost(price, T19, "default") == Decimal("0.0015750000")


@pytest.mark.parametrize(
    ("usage", "tier", "price_key"),
    [
        (None, "default", SNAPSHOT),
        (T19, "flex", SNAPSHOT),
        (T19, "priority", SNAPSHOT),
        (T19, None, SNAPSHOT),
        (TokenUsage(input_tokens=10, output_tokens=5, source="estimated"), "default", SNAPSHOT),
        (TokenUsage(input_tokens=None, output_tokens=5), "default", SNAPSHOT),
        (
            TokenUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=11),
            "default",
            MODEL,
        ),
        (TokenUsage(input_tokens=10, output_tokens=5, reasoning_output_tokens=6), "default", MODEL),
        (
            TokenUsage(input_tokens=10, output_tokens=5, cache_write_input_tokens=2),
            "default",
            MODEL,
        ),
        (T19, "default", "gpt-5.5"),
    ],
)
def test_unknown_cost_is_none_never_zero(usage, tier, price_key):
    price = live.VERIFIED_PRICES.get(("openai", price_key))
    assert live.estimate_cost(price, usage, tier) is None


def test_reported_zero_usage_is_a_known_zero():
    price = live.VERIFIED_PRICES[("openai", MODEL)]
    zero = TokenUsage(input_tokens=0, output_tokens=0)
    assert live.estimate_cost(price, zero, "default") == Decimal("0E-10")


def test_worst_case_is_an_upper_bound_rounded_up():
    price = live.VERIFIED_PRICES[("openai", MODEL)]
    assert live.worst_case_attempt_cost(price, 512, 64) == Decimal("0.0006720000")
    tiny = live.ModelPrice(
        "openai", "x", Decimal("0.0000001"), Decimal(0), Decimal(0), None, "v", "u"
    )
    assert live.worst_case_attempt_cost(tiny, 1, 0) == Decimal("1E-10")  # ceil, never 0
    # The bound covers the worst token mix the provider could report within the limits.
    worst = TokenUsage(input_tokens=512, output_tokens=64, reasoning_output_tokens=64)
    assert live.estimate_cost(price, worst, "default") <= live.worst_case_attempt_cost(
        price, 512, 64
    )


def test_input_bound_counts_utf8_bytes_plus_framing():
    assert live.input_token_bound("abc") == 3 + live.FRAMING_ALLOWANCE_TOKENS
    assert live.input_token_bound("ğüş") == 6 + live.FRAMING_ALLOWANCE_TOKENS


def test_mirror_has_only_verified_catalogue_rows():
    assert len(live.VERIFIED_PRICES) == 6
    for price in live.VERIFIED_PRICES.values():
        assert price.version == live.PRICE_CATALOGUE_VERSION
        assert price.cache_write is None
        assert price.source_url.startswith("https://developers.openai.com/api/docs/")
        for value in (price.input, price.cached_input, price.output):
            assert isinstance(value, Decimal) and value > 0


# ---------------------------------------------------------------- session ledger
def reserve(session: live.LiveSession, **kw) -> live.Reservation:
    kw.setdefault("provider", "openai")
    kw.setdefault("model", MODEL)
    kw.setdefault("input_text", "SYNTHETIC input")
    kw.setdefault("max_output_tokens", 64)
    return session.reserve(**kw)


def refusal_code(session: live.LiveSession, **kw) -> str:
    with pytest.raises(live.LiveRefusal) as info:
        reserve(session, **kw)
    (refusal,) = info.value.refusals
    return refusal.code


def success(**kw) -> AttemptRecord:
    kw.setdefault("usage", TokenUsage(input_tokens=20, output_tokens=10))
    kw.setdefault("response_model", SNAPSHOT)
    kw.setdefault("service_tier", "default")
    return AttemptRecord(kw.pop("attempt", 1), **kw)


def test_success_books_the_estimate_and_releases_the_reservation():
    session = live.LiveSession(policy())
    r = reserve(session)
    assert session.snapshot()["outstanding_usd"] == str(r.request_bound_usd)
    settled = session.settle(r, [success()])
    expected = Decimal("0.0000600000")  # (20 * 0.75 + 10 * 4.50) / 1e6
    assert settled.booked_usd == settled.estimated_cost_usd == expected
    assert settled.halted is None and settled.attempts == 1
    snap = session.snapshot()
    assert snap["booked_usd"] == str(expected) and Decimal(str(snap["outstanding_usd"])) == 0


def test_failed_attempts_are_booked_at_their_bound_never_zero():
    session = live.LiveSession(policy())
    r = reserve(session)
    settled = session.settle(r, [AttemptRecord(1, error_category="timeout")])
    assert settled.booked_usd == r.attempt_bound_usd > 0
    assert settled.estimated_cost_usd is None


def test_retries_reserve_and_book_every_attempt():
    session = live.LiveSession(policy(LLMI_LIVE_MAX_RETRIES="1"))
    r = reserve(session)
    assert r.request_bound_usd == r.attempt_bound_usd * 2
    settled = session.settle(
        r, [AttemptRecord(1, error_category="rate_limited"), success(attempt=2)]
    )
    assert settled.booked_usd == r.attempt_bound_usd + Decimal("0.0000600000")
    assert settled.estimated_cost_usd is None  # the failed attempt's cost is unknown


def test_request_count_limit():
    session = live.LiveSession(policy(LLMI_LIVE_MAX_REQUESTS="1"))
    session.settle(reserve(session), [success()])
    assert refusal_code(session) == "request_limit_reached"


def test_session_budget_is_never_exceeded():
    bound = live.worst_case_attempt_cost(
        live.VERIFIED_PRICES[("openai", MODEL)], live.input_token_bound("SYNTHETIC input"), 64
    )
    # a budget of two small requests (the full-size request still fits, or policy refuses)
    full = live.request_bound(policy(), live.VERIFIED_PRICES[("openai", MODEL)], 512)
    budget = max(bound * 2, full)
    session = live.LiveSession(
        policy(LLMI_REAL_LLM_BUDGET_USD=str(budget), LLMI_LIVE_MAX_REQUESTS="10")
    )
    for _ in range(int(budget // bound)):
        session.settle(reserve(session), [AttemptRecord(1, error_category="timeout")])
    assert refusal_code(session) == "session_budget_exhausted"


def test_outstanding_reservations_count_against_the_budget():
    bound = live.worst_case_attempt_cost(
        live.VERIFIED_PRICES[("openai", MODEL)], live.input_token_bound("SYNTHETIC input"), 64
    )
    full = live.request_bound(policy(), live.VERIFIED_PRICES[("openai", MODEL)], 512)
    session = live.LiveSession(
        policy(LLMI_REAL_LLM_BUDGET_USD=str(full), LLMI_LIVE_MAX_CONCURRENCY="2")
    )
    held = [reserve(session) for _ in range(int(full // bound))]  # unsettled, in flight
    assert len(held) == 1  # the concurrency limit is 2: the budget is what refuses
    assert refusal_code(session) == "session_budget_exhausted"


def test_concurrency_limit_is_enforced_across_threads():
    session = live.LiveSession(policy(LLMI_LIVE_MAX_CONCURRENCY="2", LLMI_LIVE_MAX_REQUESTS="20"))
    barrier = threading.Barrier(8)
    admitted: list[live.Reservation] = []
    refused: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            r = reserve(session)
        except live.LiveRefusal as exc:
            with lock:
                refused.append(exc.refusals[0].code)
        else:
            with lock:
                admitted.append(r)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(admitted) == 2 and refused == ["concurrency_limit"] * 6
    for r in admitted:
        session.settle(r, [success()])
    reserve(session)  # slots are released after settlement


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"model": "gpt-4.1-mini"}, "model_not_allowed"),
        ({"provider": "anthropic"}, "provider_not_allowed"),
        ({"max_output_tokens": 65}, "output_limit_exceeded"),
        ({"max_output_tokens": 15}, "output_limit_exceeded"),
        ({"input_text": "x" * 449}, "input_too_large"),  # 449 + 64 > 512
    ],
)
def test_request_level_refusals(kw, code):
    session = live.LiveSession(policy())
    with pytest.raises(live.LiveRefusal) as info:
        reserve(session, **kw)
    assert code in {r.code for r in info.value.refusals}
    assert session.snapshot()["requests_started"] == 0


def test_request_cost_cap_is_rechecked_per_request():
    """evaluate_policy refuses limits whose full-size request breaks the cap, so this check
    in reserve() is defence in depth: exercised with a policy built directly."""
    unchecked = live.LivePolicy(
        providers=frozenset({"openai"}),
        models=(MODEL,),
        max_requests=5,
        max_input_tokens=200,
        max_output_tokens=64,
        max_request_cost_usd=Decimal("0.0003"),
        session_budget_usd=Decimal("1"),
        timeout_seconds=20.0,
        max_retries=0,
        max_concurrency=1,
    )
    session = live.LiveSession(unchecked)
    assert refusal_code(session, input_text="x" * 130) == "request_cost_limit"
    assert reserve(session, input_text="x", max_output_tokens=16).request_bound_usd <= Decimal(
        "0.0003"
    )


@pytest.mark.parametrize(
    ("record", "halt"),
    [
        (success(usage=TokenUsage(input_tokens=5000, output_tokens=10)), "usage_exceeded_bound"),
        (success(usage=TokenUsage(input_tokens=20, output_tokens=65)), "usage_exceeded_bound"),
        (success(service_tier="priority"), "estimate_unknown"),
        (success(response_model="gpt-5.4-mini-2099-01-01"), "estimate_unknown"),
    ],
)
def test_untrustworthy_outcomes_halt_the_session(record, halt):
    session = live.LiveSession(policy())
    r = reserve(session)
    settled = session.settle(r, [record])
    assert settled.halted == halt
    price = live.VERIFIED_PRICES.get(("openai", record.response_model))
    estimate = live.estimate_cost(price, record.usage, record.service_tier)
    # the best estimate when there is one, otherwise the bound; never 0
    assert settled.booked_usd == (estimate if estimate is not None else r.attempt_bound_usd)
    assert settled.booked_usd > 0
    assert refusal_code(session) == "session_halted"


def test_more_attempts_than_reserved_are_all_booked_and_halt():
    session = live.LiveSession(policy())
    r = reserve(session)
    settled = session.settle(r, [AttemptRecord(1), AttemptRecord(2)])
    assert settled.booked_usd == r.attempt_bound_usd * 2
    assert settled.halted == "attempts_exceeded_bound"


def test_a_reservation_settles_once():
    session = live.LiveSession(policy())
    r = reserve(session)
    session.settle(r, [success()])
    with pytest.raises(ValueError):
        session.settle(r, [success()])
