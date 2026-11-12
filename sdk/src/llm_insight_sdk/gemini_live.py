"""Fail-closed permission guard for one real Gemini **Free Tier** call (Phase 6C-A, ADR-35).

Nothing in the SDK calls Gemini by itself. The live runner (`demo_app.live_gemini`) asks
this module first:

1. `evaluate_free_tier_policy(env, now=...)` returns every refusal it finds; a real call is
   allowed only when there are none. A `GEMINI_API_KEY` alone never enables anything.
2. `FreeTierSession.reserve(...)` admits the one request of the session and refuses any
   further or concurrent one; `settle(...)` records how many physical attempts happened and
   halts the session if the provider reported more than the bounds allow.

**What the Free Tier check is.** Google exposes no API that tells a client whether its
project is billed on the Free Tier. The tier is therefore an **operator attestation**: the
operator reads the *Billing Tier* column of the project in Google AI Studio and the model's
Free Tier limit on the rate-limit page, then states `LLMI_GEMINI_BILLING_TIER=free` and the
UTC date of that check (at most one day old). This is a local operator check, **not** a
proof from Google's billing backend; any other or missing statement refuses. A paid tier is
never used as a fallback: this guard has no paid mode at all, and the backend never applies a
price to a Gemini call (`unknown`, never 0).

**Limits.** Hard caps for Phase 6C-A: one request per session, concurrency one, zero
retries, a short timeout, an output ceiling (thinking tokens count against it) and an input
ceiling. The input bound is the UTF-8 byte count plus a framing allowance, as in
`live.input_token_bound` (a SentencePiece token covers at least one byte).

**Evidence window.** The model allowlist rests on the official pricing and model pages read
on `FREE_TIER_VERIFIED_AT`; after `FREE_TIER_MAX_AGE` the guard refuses until the pages are
re-read and the allowlist re-released.

**Execution switch (ADR-50).** `REAL_EXECUTION_ENABLED` is a reviewed code constant, false as
shipped, and no environment variable can change it. While it is false the policy refuses
(`real_execution_disabled`) whatever the evidence, attestation or consent, and the session refuses
every reservation, so neither Gemini runner nor the comparison runner can send. Releasing it is a
reviewed code change, separate from re-releasing the Free Tier evidence (R140).

Messages name the setting and the rule, never a value (like `live` and `config`).
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from llm_insight_sdk.live import LiveRefusal, Refusal, _Reader, input_token_bound
from llm_insight_sdk.providers import AttemptRecord

PROVIDER = "gcp.gemini"
CONSENT_PHRASE = "gemini-free-tier-single-call"
GEMINI_MODE = "free-tier"
BILLING_TIER_FREE = "free"
API_KEY_VAR = "GEMINI_API_KEY"

# Read by google-genai 2.30.1 (or google-auth) from the process environment. Each one can
# swap the credential (GOOGLE_API_KEY wins over GEMINI_API_KEY), route the call to Vertex AI
# / Gemini Enterprise or to another base URL, or replace the HTTP client by a record/replay
# client. The runner passes every value explicitly, but a set variable means the operator's
# shell is configured for something else, so it is refused rather than silently overridden.
CLIENT_ENV_OVERRIDES = (
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_ENTERPRISE",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GEMINI_BASE_URL",
    "GOOGLE_VERTEX_BASE_URL",
    "GOOGLE_GENAI_CLIENT_MODE",
    "GOOGLE_GENAI_REPLAYS_DIRECTORY",
    "GOOGLE_GENAI_REPLAY_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_API_USE_MTLS_ENDPOINT",
    "GOOGLE_API_USE_CLIENT_CERTIFICATE",
    # R123 (2): `ssl.create_default_context` honours it even though the client ignores the
    # environment, so TLS session keys would be written to that file.
    "SSLKEYLOGFILE",
    # The runner's TLS contexts trust exactly the pinned certifi bundle, so a CA setting would be
    # silently ignored; it is refused instead, as by the Groq and OpenRouter guards.
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
# R123 (1): exactly YYYY-MM-DD. `date.fromisoformat` alone also accepts other ISO 8601 forms of
# the same length, such as the week date `2026-W45-1`.
_UTC_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


# ADR-50: the independent execution switch. Enabling it is a reviewed code change; it is read only
# here and in `FreeTierSession.reserve`, never from the environment.
REAL_EXECUTION_ENABLED: bool = False


@dataclass(frozen=True)
class FreeTierModel:
    """A model confirmed on the official pages as Stable, Free Tier and generateContent."""

    model: str
    stability: str
    sources: tuple[str, ...]


FREE_TIER_VERIFIED_AT = datetime(2026, 10, 28, 13, 0, tzinfo=UTC)
FREE_TIER_MAX_AGE = timedelta(days=14)
ATTESTATION_MAX_AGE_DAYS = 1
FREE_TIER_MODELS: Mapping[str, FreeTierModel] = {
    "gemini-3.1-flash-lite": FreeTierModel(
        "gemini-3.1-flash-lite",
        "stable",
        (
            "https://ai.google.dev/gemini-api/docs/pricing",
            "https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite",
        ),
    ),
}

# Hard caps for Phase 6C-A: the environment can lower a limit, never raise it.
MAX_REQUESTS_CAP = 1
MAX_INPUT_TOKENS_CAP = 1024
MIN_OUTPUT_TOKENS = 16
MAX_OUTPUT_TOKENS_CAP = 1024
TIMEOUT_RANGE = (1.0, 30.0)
DEFAULT_TIMEOUT_SECONDS = 20.0
RETRIES_RANGE = (0, 0)
CONCURRENCY_RANGE = (1, 1)


@dataclass(frozen=True)
class FreeTierPolicy:
    providers: frozenset[str]
    models: tuple[str, ...]
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    max_concurrency: int
    tier_checked_on: date

    @property
    def max_attempts(self) -> int:
        return 1 + self.max_retries


@dataclass(frozen=True)
class FreeTierDecision:
    policy: FreeTierPolicy | None
    refusals: tuple[Refusal, ...]

    @property
    def allowed(self) -> bool:
        return self.policy is not None and not self.refusals


def _attestation(r: _Reader, now: datetime) -> date | None:
    tier = r.raw("LLMI_GEMINI_BILLING_TIER")
    if not tier:
        r.refuse(
            "free_tier_unverified",
            "LLMI_GEMINI_BILLING_TIER must state the project's AI Studio Billing Tier ('free')",
        )
    elif tier.lower() != BILLING_TIER_FREE:
        r.refuse(
            "billing_tier_not_free",
            "LLMI_GEMINI_BILLING_TIER is not 'free' (unknown or paid): no paid project is used",
        )
    raw = r.raw("LLMI_GEMINI_TIER_CHECKED_ON")
    try:
        checked = date.fromisoformat(raw) if _UTC_DATE.fullmatch(raw) else None
    except ValueError:
        checked = None
    if checked is None:
        r.refuse(
            "free_tier_unverified",
            "LLMI_GEMINI_TIER_CHECKED_ON must be the UTC date (YYYY-MM-DD) of the tier check",
        )
        return None
    today = now.astimezone(UTC).date()
    if checked > today:
        r.refuse("free_tier_attestation_invalid", "LLMI_GEMINI_TIER_CHECKED_ON is in the future")
        return None
    if (today - checked).days > ATTESTATION_MAX_AGE_DAYS:
        r.refuse(
            "free_tier_attestation_stale",
            f"LLMI_GEMINI_TIER_CHECKED_ON is older than {ATTESTATION_MAX_AGE_DAYS} day; "
            "re-check the Billing Tier in AI Studio",
        )
        return None
    return checked


def evaluate_free_tier_policy(
    env: Mapping[str, str] | None = None, *, now: datetime
) -> FreeTierDecision:
    """Every reason a real Gemini Free Tier call is not allowed, or none. Reads
    `GEMINI_API_KEY` for presence only; the value is never stored, compared or returned."""
    r = _Reader(os.environ if env is None else env)
    if not REAL_EXECUTION_ENABLED:
        r.refuse("real_execution_disabled", "real Gemini requests are not enabled in this version")
    if r.raw("LLMI_PROVIDER_MODE") != "real":
        r.refuse("provider_mode_not_real", "LLMI_PROVIDER_MODE must be 'real'")
    mode = r.raw("LLMI_GEMINI_MODE")
    if mode != GEMINI_MODE:
        r.refuse(
            "gemini_mode_not_free_tier",
            f"LLMI_GEMINI_MODE must be exactly '{GEMINI_MODE}' (no paid mode exists)",
        )
    if r.raw("LLMI_LIVE_CONSENT") != CONSENT_PHRASE:
        r.refuse("consent_missing", f"LLMI_LIVE_CONSENT must be exactly '{CONSENT_PHRASE}'")
    if not r.raw(API_KEY_VAR):
        r.refuse("api_key_missing", f"{API_KEY_VAR} is not set in the environment")
    for var in CLIENT_ENV_OVERRIDES:
        if r.raw(var):
            r.refuse(
                "client_env_override",
                f"{var} must not be set (Gemini Developer API with {API_KEY_VAR} only)",
            )
    checked_on = _attestation(r, now)

    providers = r.names("LLMI_LIVE_PROVIDERS")
    if any(p != PROVIDER for p in providers):
        r.refuse("provider_not_allowed", f"LLMI_LIVE_PROVIDERS may contain only '{PROVIDER}'")
    models = r.names("LLMI_LIVE_MODELS")
    unverified = [m for m in models if m not in FREE_TIER_MODELS]
    if unverified:
        r.refuse(
            "model_not_free_tier_verified",
            f"LLMI_LIVE_MODELS: {len(unverified)} model(s) are not on the verified Free Tier "
            "allowlist",
        )
    if now < FREE_TIER_VERIFIED_AT:
        r.refuse("free_tier_evidence_not_yet_valid", "the clock is before the Free Tier evidence")
    elif now - FREE_TIER_VERIFIED_AT > FREE_TIER_MAX_AGE:
        r.refuse(
            "free_tier_evidence_stale",
            f"the Free Tier model evidence is older than {FREE_TIER_MAX_AGE.days} days; "
            "re-read the official pricing and model pages first",
        )

    max_requests = r.integer("LLMI_LIVE_MAX_REQUESTS", 1, MAX_REQUESTS_CAP)
    max_input = r.integer("LLMI_LIVE_MAX_INPUT_TOKENS", 1, MAX_INPUT_TOKENS_CAP)
    max_output = r.integer("LLMI_LIVE_MAX_OUTPUT_TOKENS", MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS_CAP)
    timeout = r.number("LLMI_LIVE_TIMEOUT_SECONDS", *TIMEOUT_RANGE, DEFAULT_TIMEOUT_SECONDS)
    retries = r.integer("LLMI_LIVE_MAX_RETRIES", *RETRIES_RANGE, RETRIES_RANGE[0])
    concurrency = r.integer("LLMI_LIVE_MAX_CONCURRENCY", *CONCURRENCY_RANGE, CONCURRENCY_RANGE[0])
    values = (max_requests, max_input, max_output, timeout, retries, concurrency, checked_on)
    if any(v is None for v in values) or not providers or not models:
        return FreeTierDecision(None, tuple(r.refusals))
    policy = FreeTierPolicy(
        providers=frozenset(providers),
        models=models,
        max_requests=max_requests,  # type: ignore[arg-type]
        max_input_tokens=max_input,  # type: ignore[arg-type]
        max_output_tokens=max_output,  # type: ignore[arg-type]
        timeout_seconds=timeout,  # type: ignore[arg-type]
        max_retries=retries,  # type: ignore[arg-type]
        max_concurrency=concurrency,  # type: ignore[arg-type]
        tier_checked_on=checked_on,  # type: ignore[arg-type]
    )
    return FreeTierDecision(policy, tuple(r.refusals))


# ---------------------------------------------------------------- session
@dataclass(frozen=True)
class FreeTierReservation:
    reservation_id: str
    provider: str
    model: str
    input_bound: int
    max_output_tokens: int


@dataclass(frozen=True)
class FreeTierSettlement:
    attempts: int
    halted: str | None


@dataclass
class _Ledger:
    requests_started: int = 0
    attempts: int = 0
    halted: str | None = None
    open: set[str] = field(default_factory=set)


class FreeTierSession:
    """Per-process request and concurrency ledger for one `FreeTierPolicy`. No money: a
    Free Tier call has no charge to book, and an unknown cost is never booked as 0."""

    def __init__(self, policy: FreeTierPolicy) -> None:
        self.policy = policy
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(policy.max_concurrency)
        self._ledger = _Ledger()

    def reserve(
        self, *, provider: str, model: str, input_text: str, max_output_tokens: int
    ) -> FreeTierReservation:
        """Admit the session's request or raise `LiveRefusal` (nothing is sent)."""
        p = self.policy
        refusals: list[Refusal] = []
        # Checked again here, so a policy object obtained another way cannot send either.
        if not REAL_EXECUTION_ENABLED:
            refusals.append(
                Refusal(
                    "real_execution_disabled",
                    "real Gemini requests are not enabled in this version",
                )
            )
        if provider not in p.providers:
            refusals.append(Refusal("provider_not_allowed", "provider is not in the allowlist"))
        if model not in p.models or model not in FREE_TIER_MODELS:
            refusals.append(Refusal("model_not_allowed", "model is not in LLMI_LIVE_MODELS"))
        if not MIN_OUTPUT_TOKENS <= max_output_tokens <= p.max_output_tokens:
            refusals.append(
                Refusal("output_limit_exceeded", "max_output_tokens exceeds the configured limit")
            )
        bound_in = input_token_bound(input_text)
        if bound_in > p.max_input_tokens:
            refusals.append(
                Refusal("input_too_large", "the input bound exceeds LLMI_LIVE_MAX_INPUT_TOKENS")
            )
        if refusals:
            raise LiveRefusal(refusals)
        with self._lock:
            led = self._ledger
            if led.halted is not None:
                raise LiveRefusal([Refusal("session_halted", f"session halted ({led.halted})")])
            if led.requests_started >= p.max_requests:
                raise LiveRefusal(
                    [Refusal("request_limit_reached", "LLMI_LIVE_MAX_REQUESTS reached")]
                )
            if not self._slots.acquire(blocking=False):
                raise LiveRefusal(
                    [Refusal("concurrency_limit", "LLMI_LIVE_MAX_CONCURRENCY requests in flight")]
                )
            led.requests_started += 1
            reservation = FreeTierReservation(
                str(uuid.uuid4()), provider, model, bound_in, max_output_tokens
            )
            led.open.add(reservation.reservation_id)
            return reservation

    def settle(
        self, reservation: FreeTierReservation, attempts: Sequence[AttemptRecord]
    ) -> FreeTierSettlement:
        """Record the physical attempts of a reserved request (each reservation once)."""
        halt: str | None = None
        for record in attempts:
            usage = record.usage
            if usage is None:
                continue
            if (usage.input_tokens or 0) > reservation.input_bound or (
                usage.output_tokens or 0
            ) > reservation.max_output_tokens:
                halt = halt or "usage_exceeded_bound"
        if len(attempts) > self.policy.max_attempts:
            halt = halt or "attempts_exceeded_bound"
        with self._lock:
            led = self._ledger
            if reservation.reservation_id not in led.open:
                raise ValueError("reservation already settled or unknown")
            led.open.discard(reservation.reservation_id)
            led.attempts += len(attempts)
            if halt is not None and led.halted is None:
                led.halted = halt
            self._slots.release()
        return FreeTierSettlement(len(attempts), halt)

    def snapshot(self) -> dict[str, object]:
        """Safe counters only (no content, no key)."""
        with self._lock:
            led = self._ledger
            return {
                "requests_started": led.requests_started,
                "max_requests": self.policy.max_requests,
                "physical_attempts": led.attempts,
                "halted": led.halted,
            }
