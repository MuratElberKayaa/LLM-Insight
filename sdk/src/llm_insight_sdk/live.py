"""Fail-closed permission and spending guard for real provider calls (Phase 6A, ADR-29).

Nothing in the SDK calls a provider by itself. An application (the live demo runner) asks
this module first:

1. `evaluate_policy(env, now=...)` reads the explicit local configuration and returns every
   refusal it finds. Real execution is allowed only when there are none. An API key alone
   never enables anything: the provider mode, a consent phrase, provider and model
   allowlists, request, token, time and money limits must all be set and valid.
2. `LiveSession.reserve(...)` admits one logical request: it bounds the worst-case cost of
   every allowed attempt **before** the request is sent and refuses when a limit would be
   exceeded. `LiveSession.settle(...)` then books what the attempts may have cost.

**Money.** `Decimal` only. Every figure here is an **estimate** from provider-reported
usage and the verified price list; the billed amount is known only to the provider's
invoice. An attempt without usage (any failure, a malformed body) is booked at its
worst-case bound, never at 0. An unknown price refuses the request; it is never priced at
0. A post-call estimate that is unknown or above its bound **halts the session** (no
further request), because the bound can no longer be trusted.

**Scope of the guarantee.** In-process only (like CR-08 and R67): the counters live in one
`LiveSession` object and reset when the process exits. They are not a distributed limit, a
billing-account limit or a substitute for the provider's own project budget. The input
bound assumes byte-level BPE tokenization (one token covers at least one UTF-8 byte) plus a
fixed framing allowance; the post-call check halts the session if the provider reports
more.

Messages name the setting and the rule, never a value (like `config.ConfigError`).
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

from llm_insight_sdk.instrumentation import TokenUsage
from llm_insight_sdk.providers import AttemptRecord

CONSENT_PHRASE = "openai-paid-calls"
SUPPORTED_PROVIDERS = frozenset({"openai"})
# Read by `openai` 3.28.0 from the process environment, never by this guard (Phase 6B):
# OPENAI_CUSTOM_HEADERS can replace the `Authorization` header (another credential than the
# OPENAI_API_KEY checked here) and, like the other two, route billing to another
# organization or project than the dedicated project key's. OPENAI_ADMIN_KEY (ADR-32) puts
# an organization-wide credential into the client, which the live run never needs.
CLIENT_ENV_OVERRIDES = (
    "OPENAI_CUSTOM_HEADERS",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_ADMIN_KEY",
)
PRICED_SERVICE_TIER = "default"

# One token covers at least one UTF-8 byte under byte-level BPE; the allowance covers the
# request framing the provider adds around the input (an assumption, checked after the call).
FRAMING_ALLOWANCE_TOKENS = 64
MIN_OUTPUT_TOKENS = 16  # the Responses API minimum for max_output_tokens

# Hard caps: the environment can lower a limit, never raise it above these.
MAX_REQUESTS_CAP = 20
MAX_INPUT_TOKENS_CAP = 8192
MAX_OUTPUT_TOKENS_CAP = 4096
MAX_REQUEST_COST_CAP = Decimal("1.00")
SESSION_BUDGET_CAP = Decimal("5.00")
TIMEOUT_RANGE = (1.0, 60.0)
RETRIES_RANGE = (0, 2)
CONCURRENCY_RANGE = (1, 4)
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 0
DEFAULT_MAX_CONCURRENCY = 1

_MONEY = Decimal("0.0000000001")  # NUMERIC(20,10), as the backend stores cost
_MILLION = Decimal(1_000_000)


# ---------------------------------------------------------------- verified prices (mirror)
PRICE_CATALOGUE_VERSION = "openai-2026-10-24"
PRICE_VERIFIED_AT = datetime(2026, 10, 24, 21, 40, tzinfo=UTC)
# Real execution is refused once the evidence is older than this: re-verify the catalogue
# (docs/PRICING.md §2) and release a new version before spending against it (R28).
PRICE_MAX_AGE = timedelta(days=14)
_PRICING_URL = "https://developers.openai.com/api/docs/pricing"


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens, Standard tier, global endpoint. `cache_write` None: no price."""

    provider: str
    model: str
    input: Decimal
    cached_input: Decimal
    output: Decimal
    cache_write: Decimal | None
    version: str
    source_url: str


def _pair(alias: str, snapshot: str, input_: str, cached: str, output: str) -> list[ModelPrice]:
    prices = (Decimal(input_), Decimal(cached), Decimal(output), None, PRICE_CATALOGUE_VERSION)
    return [
        ModelPrice("openai", alias, *prices, _PRICING_URL),
        ModelPrice(
            "openai", snapshot, *prices, f"https://developers.openai.com/api/docs/models/{alias}"
        ),
    ]


# An exact copy of backend/app/services/pricing_catalog.py (ADR-01: the SDK never imports
# the backend). tests/integration/test_live_runner.py compares it with the seeded
# `model_prices` rows, so drift fails a test.
VERIFIED_PRICES: Mapping[tuple[str, str], ModelPrice] = {
    (p.provider, p.model): p
    for p in (
        *_pair("gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "0.75", "0.075", "4.50"),
        *_pair("gpt-5-mini", "gpt-5-mini-2025-08-07", "0.25", "0.025", "2.00"),
        *_pair("gpt-4.1-mini", "gpt-4.1-mini-2025-04-14", "0.40", "0.10", "1.60"),
    )
}


def worst_case_attempt_cost(price: ModelPrice, input_bound: int, output_bound: int) -> Decimal:
    """Upper bound of one attempt: every input token at the higher input rate, every output
    token (reasoning included) at the output rate; rounded up to 10 decimals."""
    with localcontext() as ctx:
        ctx.prec = 60
        rate_in = max(price.input, price.cached_input)
        total = (input_bound * rate_in + output_bound * price.output) / _MILLION
        return total.quantize(_MONEY, rounding=ROUND_CEILING)


def estimate_cost(
    price: ModelPrice | None, usage: TokenUsage | None, tier: str | None
) -> Decimal | None:
    """The backend's cost rule (docs/PRICING.md §4) on one attempt, or None when the backend
    would store `unknown`: no price, no provider-reported usage, inconsistent usage, a
    service tier other than `default`, or a cache write without a price."""
    if price is None or usage is None or usage.source != "provider_reported":
        return None
    if tier != PRICED_SERVICE_TIER or usage.input_tokens is None or usage.output_tokens is None:
        return None
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_write_input_tokens or 0
    reasoning = usage.reasoning_output_tokens or 0
    if cache_read + cache_write > usage.input_tokens or reasoning > usage.output_tokens:
        return None
    if cache_write and price.cache_write is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 60
        total = (
            (usage.input_tokens - cache_read - cache_write) * price.input
            + cache_read * price.cached_input
            + cache_write * (price.cache_write or Decimal(0))
            + usage.output_tokens * price.output
        ) / _MILLION
        return total.quantize(_MONEY, rounding=ROUND_HALF_EVEN)


# ---------------------------------------------------------------- policy
@dataclass(frozen=True)
class Refusal:
    code: str
    message: str


class LiveRefusal(RuntimeError):
    """Real execution was refused; nothing was sent to the provider."""

    def __init__(self, refusals: Sequence[Refusal]) -> None:
        self.refusals = tuple(refusals)
        super().__init__("; ".join(f"{r.code}: {r.message}" for r in self.refusals))


@dataclass(frozen=True)
class LivePolicy:
    providers: frozenset[str]
    models: tuple[str, ...]
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_request_cost_usd: Decimal
    session_budget_usd: Decimal
    timeout_seconds: float
    max_retries: int
    max_concurrency: int

    @property
    def max_attempts(self) -> int:
        return 1 + self.max_retries


@dataclass(frozen=True)
class PolicyDecision:
    policy: LivePolicy | None
    refusals: tuple[Refusal, ...]

    @property
    def allowed(self) -> bool:
        return self.policy is not None and not self.refusals


class _Reader:
    """Parses one setting at a time and collects refusals instead of raising."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = env
        self.refusals: list[Refusal] = []

    def raw(self, var: str) -> str:
        return (self.env.get(var) or "").strip()

    def refuse(self, code: str, message: str) -> None:
        self.refusals.append(Refusal(code, message))

    def integer(self, var: str, low: int, high: int, default: int | None = None) -> int | None:
        raw = self.raw(var)
        if not raw:
            if default is None:
                self.refuse("setting_missing", f"{var} must be set")
            return default
        try:
            value = int(raw)
        except ValueError:
            value = None
        if value is None or not low <= value <= high:
            self.refuse("setting_invalid", f"{var} must be an integer between {low} and {high}")
            return None
        return value

    def number(self, var: str, low: float, high: float, default: float) -> float | None:
        raw = self.raw(var)
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            value = None
        if value is None or not low <= value <= high:  # NaN fails both comparisons
            self.refuse("setting_invalid", f"{var} must be a number between {low} and {high}")
            return None
        return value

    def money(self, var: str, cap: Decimal) -> Decimal | None:
        raw = self.raw(var)
        if not raw:
            self.refuse("setting_missing", f"{var} must be set (USD, Decimal)")
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation:
            value = None
        if value is None or not value.is_finite() or not Decimal(0) < value <= cap:
            self.refuse("setting_invalid", f"{var} must be a decimal amount in (0, {cap}] USD")
            return None
        return value

    def names(self, var: str) -> tuple[str, ...]:
        items = tuple(dict.fromkeys(p.strip() for p in self.raw(var).split(",") if p.strip()))
        if not items:
            self.refuse("setting_missing", f"{var} must list at least one entry")
        return items


def evaluate_policy(env: Mapping[str, str] | None = None, *, now: datetime) -> PolicyDecision:
    """Every reason real execution is not allowed, or none. Reads `OPENAI_API_KEY` for
    presence only; the value is never stored, compared or returned."""
    r = _Reader(os.environ if env is None else env)
    if r.raw("LLMI_PROVIDER_MODE") != "real":
        r.refuse("provider_mode_not_real", "LLMI_PROVIDER_MODE must be 'real'")
    if r.raw("LLMI_LIVE_CONSENT") != CONSENT_PHRASE:
        r.refuse("consent_missing", f"LLMI_LIVE_CONSENT must be exactly '{CONSENT_PHRASE}'")
    if not r.raw("OPENAI_API_KEY"):
        r.refuse("api_key_missing", "OPENAI_API_KEY is not set in the environment")
    if r.raw("OPENAI_BASE_URL"):
        r.refuse("base_url_override", "OPENAI_BASE_URL must not be set (api.openai.com only)")
    for var in CLIENT_ENV_OVERRIDES:
        if r.raw(var):
            r.refuse(
                "client_env_override",
                f"{var} must not be set (credential and billing come from the project key only)",
            )

    providers = r.names("LLMI_LIVE_PROVIDERS")
    for provider in providers:
        if provider not in SUPPORTED_PROVIDERS:
            r.refuse("provider_not_allowed", "LLMI_LIVE_PROVIDERS may contain only 'openai'")
            break
    models = r.names("LLMI_LIVE_MODELS")
    unpriced = [m for m in models if ("openai", m) not in VERIFIED_PRICES]
    if unpriced:
        r.refuse(
            "model_price_unknown",
            f"LLMI_LIVE_MODELS: {len(unpriced)} model(s) have no verified price "
            f"(catalogue {PRICE_CATALOGUE_VERSION})",
        )
    if now < PRICE_VERIFIED_AT:
        r.refuse("price_not_yet_valid", "the clock is before the price catalogue's evidence")
    elif now - PRICE_VERIFIED_AT > PRICE_MAX_AGE:
        r.refuse(
            "price_catalogue_stale",
            f"price catalogue {PRICE_CATALOGUE_VERSION} is older than "
            f"{PRICE_MAX_AGE.days} days; re-verify prices before any paid call",
        )

    max_requests = r.integer("LLMI_LIVE_MAX_REQUESTS", 1, MAX_REQUESTS_CAP)
    max_input = r.integer("LLMI_LIVE_MAX_INPUT_TOKENS", 1, MAX_INPUT_TOKENS_CAP)
    max_output = r.integer("LLMI_LIVE_MAX_OUTPUT_TOKENS", MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS_CAP)
    request_cap = r.money("LLMI_LIVE_MAX_REQUEST_COST_USD", MAX_REQUEST_COST_CAP)
    budget = r.money("LLMI_REAL_LLM_BUDGET_USD", SESSION_BUDGET_CAP)
    timeout = r.number("LLMI_LIVE_TIMEOUT_SECONDS", *TIMEOUT_RANGE, DEFAULT_TIMEOUT_SECONDS)
    retries = r.integer("LLMI_LIVE_MAX_RETRIES", *RETRIES_RANGE, DEFAULT_MAX_RETRIES)
    concurrency = r.integer(
        "LLMI_LIVE_MAX_CONCURRENCY", *CONCURRENCY_RANGE, DEFAULT_MAX_CONCURRENCY
    )
    values = (max_requests, max_input, max_output, request_cap, budget, timeout, retries)
    if any(v is None for v in values) or concurrency is None or not providers or not models:
        return PolicyDecision(None, tuple(r.refusals))

    policy = LivePolicy(
        providers=frozenset(providers),
        models=models,
        max_requests=max_requests,  # type: ignore[arg-type]
        max_input_tokens=max_input,  # type: ignore[arg-type]
        max_output_tokens=max_output,  # type: ignore[arg-type]
        max_request_cost_usd=request_cap,  # type: ignore[arg-type]
        session_budget_usd=budget,  # type: ignore[arg-type]
        timeout_seconds=timeout,  # type: ignore[arg-type]
        max_retries=retries,  # type: ignore[arg-type]
        max_concurrency=concurrency,
    )
    # The limits must be satisfiable: a full-size request of every allowed model must fit
    # the request cap, and that request must fit the session budget.
    for model in models:
        price = VERIFIED_PRICES.get(("openai", model))
        if price is None:
            continue
        bound = request_bound(policy, price, policy.max_input_tokens)
        if bound > policy.max_request_cost_usd:
            r.refuse(
                "request_cost_bound_exceeds_limit",
                "the worst-case cost of a full-size request exceeds "
                "LLMI_LIVE_MAX_REQUEST_COST_USD; lower the token limits or retries",
            )
            break
        if bound > policy.session_budget_usd:
            r.refuse(
                "session_budget_too_small",
                "LLMI_REAL_LLM_BUDGET_USD is below the worst-case cost of one request",
            )
            break
    return PolicyDecision(policy, tuple(r.refusals))


def request_bound(policy: LivePolicy, price: ModelPrice, input_bound: int) -> Decimal:
    """Worst case of one logical request: every allowed attempt at full size."""
    return worst_case_attempt_cost(price, input_bound, policy.max_output_tokens) * (
        policy.max_attempts
    )


def input_token_bound(input_text: str) -> int:
    return len(input_text.encode("utf-8")) + FRAMING_ALLOWANCE_TOKENS


# ---------------------------------------------------------------- session ledger
@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    provider: str
    model: str
    price: ModelPrice
    input_bound: int
    max_output_tokens: int
    attempt_bound_usd: Decimal
    request_bound_usd: Decimal


@dataclass(frozen=True)
class Settlement:
    """`booked_usd`: what the session counts as possibly spent (estimates, plus bounds for
    attempts without a usable estimate). `estimated_cost_usd`: the sum of the attempts'
    usage-based estimates, or None if any attempt's cost is unknown."""

    booked_usd: Decimal
    estimated_cost_usd: Decimal | None
    attempts: int
    halted: str | None


@dataclass
class _Ledger:
    requests_started: int = 0
    outstanding_usd: Decimal = field(default_factory=Decimal)
    booked_usd: Decimal = field(default_factory=Decimal)
    halted: str | None = None
    open: set[str] = field(default_factory=set)


class LiveSession:
    """Per-process request, concurrency and spending ledger for one `LivePolicy`."""

    def __init__(
        self, policy: LivePolicy, prices: Mapping[tuple[str, str], ModelPrice] = VERIFIED_PRICES
    ) -> None:
        self.policy = policy
        self._prices = prices
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(policy.max_concurrency)
        self._ledger = _Ledger()

    def reserve(
        self, *, provider: str, model: str, input_text: str, max_output_tokens: int
    ) -> Reservation:
        """Admit one logical request or raise `LiveRefusal` (nothing is sent)."""
        p = self.policy
        refusals: list[Refusal] = []
        if provider not in p.providers:
            refusals.append(Refusal("provider_not_allowed", "provider is not in the allowlist"))
        if model not in p.models:
            refusals.append(Refusal("model_not_allowed", "model is not in LLMI_LIVE_MODELS"))
        price = self._prices.get((provider, model))
        if price is None:
            refusals.append(Refusal("model_price_unknown", "model has no verified price"))
        if not MIN_OUTPUT_TOKENS <= max_output_tokens <= p.max_output_tokens:
            refusals.append(
                Refusal("output_limit_exceeded", "max_output_tokens exceeds the configured limit")
            )
        bound_in = input_token_bound(input_text)
        if bound_in > p.max_input_tokens:
            refusals.append(
                Refusal("input_too_large", "the input bound exceeds LLMI_LIVE_MAX_INPUT_TOKENS")
            )
        if refusals or price is None:
            raise LiveRefusal(refusals)
        attempt_bound = worst_case_attempt_cost(price, bound_in, max_output_tokens)
        request_bound_usd = attempt_bound * p.max_attempts
        if request_bound_usd > p.max_request_cost_usd:
            raise LiveRefusal(
                [Refusal("request_cost_limit", "worst-case request cost exceeds the request cap")]
            )
        with self._lock:
            led = self._ledger
            if led.halted is not None:
                raise LiveRefusal([Refusal("session_halted", f"session halted ({led.halted})")])
            if led.requests_started >= p.max_requests:
                raise LiveRefusal(
                    [Refusal("request_limit_reached", "LLMI_LIVE_MAX_REQUESTS reached")]
                )
            if led.booked_usd + led.outstanding_usd + request_bound_usd > p.session_budget_usd:
                raise LiveRefusal(
                    [Refusal("session_budget_exhausted", "the session budget would be exceeded")]
                )
            if not self._slots.acquire(blocking=False):
                raise LiveRefusal(
                    [Refusal("concurrency_limit", "LLMI_LIVE_MAX_CONCURRENCY requests in flight")]
                )
            led.requests_started += 1
            led.outstanding_usd += request_bound_usd
            reservation = Reservation(
                reservation_id=str(uuid.uuid4()),
                provider=provider,
                model=model,
                price=price,
                input_bound=bound_in,
                max_output_tokens=max_output_tokens,
                attempt_bound_usd=attempt_bound,
                request_bound_usd=request_bound_usd,
            )
            led.open.add(reservation.reservation_id)
            return reservation

    def settle(self, reservation: Reservation, attempts: Sequence[AttemptRecord]) -> Settlement:
        """Book the physical attempts of a reserved request (each at most once)."""
        booked = Decimal(0)
        estimates: list[Decimal | None] = []
        halt: str | None = None
        for record in attempts:
            usage = record.usage
            if usage is None:
                booked += reservation.attempt_bound_usd  # failed or unreadable: may be billed
                estimates.append(None)
                continue
            price = self._prices.get(
                (reservation.provider, record.response_model or reservation.model)
            )
            estimate = estimate_cost(price, usage, record.service_tier)
            estimates.append(estimate)
            over_bound = (usage.input_tokens or 0) > reservation.input_bound or (
                usage.output_tokens or 0
            ) > reservation.max_output_tokens
            if estimate is None:
                halt = halt or "estimate_unknown"
                booked += reservation.attempt_bound_usd
            else:
                booked += estimate
                if over_bound or estimate > reservation.attempt_bound_usd:
                    halt = halt or "usage_exceeded_bound"
            if over_bound:
                halt = halt or "usage_exceeded_bound"
        if len(attempts) > self.policy.max_attempts:
            halt = halt or "attempts_exceeded_bound"
        known = [e for e in estimates if e is not None]
        estimated = sum(known, Decimal(0)) if len(known) == len(estimates) else None
        with self._lock:
            led = self._ledger
            if reservation.reservation_id not in led.open:
                raise ValueError("reservation already settled or unknown")
            led.open.discard(reservation.reservation_id)
            led.outstanding_usd -= reservation.request_bound_usd
            led.booked_usd += booked
            if halt is not None and led.halted is None:
                led.halted = halt
            self._slots.release()
        return Settlement(booked, estimated, len(attempts), halt)

    def snapshot(self) -> dict[str, object]:
        """Safe counters only (no content, no key)."""
        with self._lock:
            led = self._ledger
            return {
                "requests_started": led.requests_started,
                "max_requests": self.policy.max_requests,
                "booked_usd": format(led.booked_usd, "f"),  # never "0E-10"
                "outstanding_usd": format(led.outstanding_usd, "f"),
                "session_budget_usd": str(self.policy.session_budget_usd),
                "halted": led.halted,
            }
