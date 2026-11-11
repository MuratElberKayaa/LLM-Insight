"""Fail-closed free-model guard and endpoint boundary for one real OpenRouter request (ADR-47).

**No real request can be made yet, for three independent reasons.**
`openrouter_adapter.REAL_EXECUTION_ENABLED` is false; no free-model evidence is released
(`FREE_MODELS` is empty and `FREE_MODEL_EVIDENCE_VERIFIED_AT` is None); and the zero-price routing
constraint has not been verified (`ZERO_PRICE_ROUTING_VERIFIED_AT` is None). So
`evaluate_free_model_policy` refuses in every environment. Releasing any of them is a reviewed
code change.

**How it is used.** `FreeModelSession` is the `send_gate` of a real `ObservedOpenRouterClient`,
which cannot be built without one. `authorize_send` runs after the request is built and only the
span's creation follows it. Each time it re-evaluates the whole policy against the clock and
environment, then the endpoint boundary (the audited Groq checks plus OpenRouter's URL and host),
then that the request carries exactly the `OPENROUTER_API_KEY` the policy just validated, then the
body that will actually be sent, including its routing constraints, and only then takes the
single request slot. Any failure raises `LiveRefusal` and nothing is sent.

**What the free-only check is.** OpenRouter documents a key endpoint that reports
`is_free_tier`, but nothing here calls it: no provider request is made except the one authorized
inference request. The account state is an **operator attestation**:
`LLMI_OPENROUTER_ACCOUNT_STATE=no-purchased-credits` (also meaning no BYOK and no automatic top-up)
and the UTC date of that check, at most one day old. It is not proof from OpenRouter's billing.
The request itself never allows a fallback (no `models` list, `allow_fallbacks: false`), refuses
data collection, and caps every price at 0; the response must then report a cost of exactly 0
credits and no BYOK, or the session halts. The account's real state is the actual boundary.

**Limits.** One request, concurrency one, zero retries. The process slot is the Groq guard's own
(`groq_live._PROCESS_SLOT`), taken once and never released, so one process makes at most one real
provider request across both providers. The counters do not survive the process.

Messages name the setting and the rule, never a value.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import httpx

from llm_insight_sdk import groq_adapter, groq_live, openrouter_adapter
from llm_insight_sdk.groq_adapter import RequestState
from llm_insight_sdk.live import LiveRefusal, Refusal, _Reader, input_token_bound
from llm_insight_sdk.openrouter_adapter import OpenRouterCallError, OpenRouterCompletion

PROVIDER = openrouter_adapter.PROVIDER
CONSENT_PHRASE = "openrouter-free-model-single-call"
OPENROUTER_MODE = "free-models"
ACCOUNT_STATE = "no-purchased-credits"
API_KEY_VAR = "OPENROUTER_API_KEY"
ACCOUNT_STATE_VAR = "LLMI_OPENROUTER_ACCOUNT_STATE"
ACCOUNT_CHECKED_ON_VAR = "LLMI_OPENROUTER_ACCOUNT_CHECKED_ON"

# The same refused settings as the Groq guard: base URLs, proxies, CA overrides, TLS key logging.
CLIENT_ENV_OVERRIDES = groq_live.CLIENT_ENV_OVERRIDES
# Keys of other services. OpenRouter's own keys start `sk-or-`, which is not required (the format
# is not a documented contract), but a plain OpenAI-style `sk-` key is refused.
_FOREIGN_KEY_PREFIXES = ("gsk_", "AIza", "lli_")
_OPENAI_KEY_PREFIX = "sk-"
_OPENROUTER_KEY_PREFIX = "sk-or-"
_UTC_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class FreeModel:
    """A `:free` model confirmed on OpenRouter's model page and in the operator's account."""

    model: str
    sources: tuple[str, ...]


# Released at the evidence gate. Empty and None are the fail-closed state: nothing was verified.
FREE_MODEL_EVIDENCE_VERIFIED_AT: datetime | None = None
FREE_MODELS: Mapping[str, FreeModel] = {}
# OpenRouter documents `max_price` but not how a cap of 0 behaves for a `:free` model or which
# error it returns when no endpoint qualifies. Until that is verified, no request is allowed.
ZERO_PRICE_ROUTING_VERIFIED_AT: datetime | None = None
FREE_MODEL_EVIDENCE_MAX_AGE = timedelta(days=7)  # OpenRouter's free list "changes frequently"
ATTESTATION_MAX_AGE_DAYS = 1

MAX_REQUESTS_CAP = 1
MAX_INPUT_TOKENS_CAP = 1024
MIN_OUTPUT_TOKENS = 16
MAX_OUTPUT_TOKENS_CAP = openrouter_adapter.MAX_OUTPUT_TOKENS
TIMEOUT_RANGE = (
    openrouter_adapter.TOTAL_DEADLINE_SECONDS,
    openrouter_adapter.TOTAL_DEADLINE_SECONDS,
)
RETRIES_RANGE = (0, 0)
CONCURRENCY_RANGE = (1, 1)

_BODY_KEYS = frozenset(openrouter_adapter.REQUEST_FIELDS)
_ROUTING_JSON = json.dumps(openrouter_adapter.PROVIDER_ROUTING, sort_keys=True)
_REASONING_JSON = json.dumps({"exclude": True}, sort_keys=True)


@dataclass(frozen=True)
class FreeModelPolicy:
    model: str
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    max_concurrency: int
    account_checked_on: date


@dataclass(frozen=True)
class FreeModelDecision:
    policy: FreeModelPolicy | None
    refusals: tuple[Refusal, ...]

    @property
    def allowed(self) -> bool:
        return self.policy is not None and not self.refusals


# ---------------------------------------------------------------- policy
def api_key_value(env: Mapping[str, str]) -> str:
    """`OPENROUTER_API_KEY` exactly as the policy reads it, or "". The runner gives the client
    this value, and the send gate checks that the request carries it."""
    return _Reader(env).raw(API_KEY_VAR)


def _check_api_key(r: _Reader) -> None:
    key = r.raw(API_KEY_VAR)
    if not key:
        r.refuse("api_key_missing", f"{API_KEY_VAR} is not set in the environment")
    elif key.startswith(_FOREIGN_KEY_PREFIXES) or (
        key.startswith(_OPENAI_KEY_PREFIX) and not key.startswith(_OPENROUTER_KEY_PREFIX)
    ):
        r.refuse("api_key_wrong_provider", f"{API_KEY_VAR} holds a key of another service")
    elif not groq_adapter._API_KEY.fullmatch(key):
        r.refuse("api_key_invalid", f"{API_KEY_VAR} must be visible ASCII characters")


def _attestation(r: _Reader, now: datetime) -> date | None:
    state = r.raw(ACCOUNT_STATE_VAR)
    if not state:
        r.refuse(
            "account_state_unverified",
            f"{ACCOUNT_STATE_VAR} must state the account's state ('{ACCOUNT_STATE}')",
        )
    elif state != ACCOUNT_STATE:
        r.refuse(
            "account_not_free_only",
            f"{ACCOUNT_STATE_VAR} is not '{ACCOUNT_STATE}': no credits, BYOK or top-up is used",
        )
    raw = r.raw(ACCOUNT_CHECKED_ON_VAR)
    try:
        checked = date.fromisoformat(raw) if _UTC_DATE.fullmatch(raw) else None
    except ValueError:
        checked = None
    if checked is None:
        r.refuse(
            "account_state_unverified",
            f"{ACCOUNT_CHECKED_ON_VAR} must be the UTC date (YYYY-MM-DD) of the account check",
        )
        return None
    today = now.astimezone(UTC).date()
    if checked > today:
        r.refuse("account_attestation_invalid", f"{ACCOUNT_CHECKED_ON_VAR} is in the future")
        return None
    if (today - checked).days > ATTESTATION_MAX_AGE_DAYS:
        r.refuse(
            "account_attestation_stale",
            f"{ACCOUNT_CHECKED_ON_VAR} is older than {ATTESTATION_MAX_AGE_DAYS} day; "
            "re-check the account's credits, BYOK and top-up settings",
        )
        return None
    return checked


def _check_dated(
    r: _Reader, verified_at: datetime | None, now: datetime, prefix: str, what: str
) -> None:
    if verified_at is None:
        r.refuse(f"{prefix}_missing", f"no {what} has been released")
    elif now < verified_at:
        r.refuse(f"{prefix}_not_yet_valid", f"the clock is before the {what}")
    elif now - verified_at > FREE_MODEL_EVIDENCE_MAX_AGE:
        r.refuse(
            f"{prefix}_stale",
            f"the {what} is older than {FREE_MODEL_EVIDENCE_MAX_AGE.days} days; "
            "re-read OpenRouter's pages first",
        )


def _check_models(r: _Reader) -> str | None:
    models = r.names("LLMI_LIVE_MODELS")
    if len(models) > 1:
        r.refuse("model_list_not_single", "LLMI_LIVE_MODELS must name one model: no fallbacks")
    if any(not openrouter_adapter.is_free_model_id(m) for m in models):
        r.refuse(
            "model_not_free_variant",
            "LLMI_LIVE_MODELS must name one explicit 'org/name:free' model (no router)",
        )
    if any(m not in FREE_MODELS for m in models):
        r.refuse(
            "model_not_free_verified",
            f"LLMI_LIVE_MODELS: {sum(m not in FREE_MODELS for m in models)} model(s) are not on "
            "the verified free-model allowlist",
        )
    return models[0] if len(models) == 1 else None


def evaluate_free_model_policy(
    env: Mapping[str, str] | None = None, *, now: datetime
) -> FreeModelDecision:
    """Every reason a real OpenRouter free-model request is not allowed, or none."""
    if now.utcoffset() is None:
        return FreeModelDecision(None, (Refusal("clock_invalid", "the clock has no timezone"),))
    r = _Reader(os.environ if env is None else env)
    if not openrouter_adapter.REAL_EXECUTION_ENABLED:
        r.refuse(
            "real_execution_disabled", "real OpenRouter requests are not enabled in this version"
        )
    if r.raw("LLMI_PROVIDER_MODE") != "real":
        r.refuse("provider_mode_not_real", "LLMI_PROVIDER_MODE must be 'real'")
    if r.raw("LLMI_OPENROUTER_MODE") != OPENROUTER_MODE:
        r.refuse(
            "openrouter_mode_not_free",
            f"LLMI_OPENROUTER_MODE must be exactly '{OPENROUTER_MODE}' (no paid mode exists)",
        )
    if r.raw("LLMI_LIVE_CONSENT") != CONSENT_PHRASE:
        r.refuse("consent_missing", f"LLMI_LIVE_CONSENT must be exactly '{CONSENT_PHRASE}'")
    _check_api_key(r)
    for var in CLIENT_ENV_OVERRIDES:
        if r.raw(var):
            r.refuse(
                "client_env_override", f"{var} must not be set (direct OpenRouter endpoint only)"
            )
    checked_on = _attestation(r, now)
    _check_dated(
        r, FREE_MODEL_EVIDENCE_VERIFIED_AT, now, "free_model_evidence", "free-model evidence"
    )
    _check_dated(
        r,
        ZERO_PRICE_ROUTING_VERIFIED_AT,
        now,
        "free_routing_evidence",
        "zero-price routing evidence",
    )

    providers = r.names("LLMI_LIVE_PROVIDERS")
    if any(p != PROVIDER for p in providers):
        r.refuse("provider_not_allowed", f"LLMI_LIVE_PROVIDERS may contain only '{PROVIDER}'")
    model = _check_models(r)

    max_requests = r.integer("LLMI_LIVE_MAX_REQUESTS", 1, MAX_REQUESTS_CAP)
    max_input = r.integer("LLMI_LIVE_MAX_INPUT_TOKENS", 1, MAX_INPUT_TOKENS_CAP)
    max_output = r.integer("LLMI_LIVE_MAX_OUTPUT_TOKENS", MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS_CAP)
    timeout = r.number("LLMI_LIVE_TIMEOUT_SECONDS", *TIMEOUT_RANGE, TIMEOUT_RANGE[0])
    retries = r.integer("LLMI_LIVE_MAX_RETRIES", *RETRIES_RANGE, RETRIES_RANGE[0])
    concurrency = r.integer("LLMI_LIVE_MAX_CONCURRENCY", *CONCURRENCY_RANGE, CONCURRENCY_RANGE[0])
    if (
        max_requests is None
        or max_input is None
        or max_output is None
        or timeout is None
        or retries is None
        or concurrency is None
        or checked_on is None
        or model is None
        or not providers
    ):
        return FreeModelDecision(None, tuple(r.refusals))
    policy = FreeModelPolicy(
        model=model,
        max_requests=max_requests,
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        timeout_seconds=timeout,
        max_retries=retries,
        max_concurrency=concurrency,
        account_checked_on=checked_on,
    )
    return FreeModelDecision(policy, tuple(r.refusals))


# ---------------------------------------------------------------- endpoint boundary
def _request_problems(request: httpx.Request) -> list[str]:
    problems = []
    if str(request.url) != openrouter_adapter.ENDPOINT_URL or request.method != "POST":
        problems.append("the request must be POST to the pinned HTTPS OpenRouter endpoint")
    headers = request.headers
    if (
        {name.lower() for name in headers} != groq_adapter._SENT_HEADERS
        or len(headers.get_list("authorization")) != 1
        or headers.get("host") != openrouter_adapter.OPENROUTER_HOST
    ):
        problems.append("the request headers must be exactly the pinned set")
    if request.extensions != {"timeout": openrouter_adapter.OPENROUTER_TIMEOUT.as_dict()}:
        problems.append("the request must carry only the bounded OpenRouter timeouts")
    return problems


def _credential_bound(request: httpx.Request, env: Mapping[str, str]) -> bool:
    """Whether the request's one `Authorization` header is exactly `Bearer` and the key the policy
    just validated from `env`, compared in constant time."""
    key = api_key_value(env)
    values = request.headers.get_list("authorization")
    if not key or len(values) != 1 or not values[0].isascii() or not key.isascii():
        return False
    return hmac.compare_digest(values[0].encode("ascii"), f"Bearer {key}".encode("ascii"))


def send_boundary_problems(http: object, request: httpx.Request) -> list[str]:
    """Every reason this request may not leave on this client; empty when it may. The client,
    transport, pool, connection and TLS checks are the audited Groq ones (ADR-43, ADR-44)."""
    read = groq_adapter._attr
    problems = openrouter_adapter.live_openrouter_client_problems(http)
    transport = read(http, "_transport")
    pool = read(transport, "_pool")
    problems.extend(groq_adapter.transport_problems(transport))
    problems.extend(groq_live._connection_problems(pool))
    problems.extend(groq_live._tls_problems(read(pool, "_ssl_context")))
    problems.extend(_request_problems(request))
    return list(dict.fromkeys(problems))


# ---------------------------------------------------------------- what will be sent
@dataclass(frozen=True)
class _Body:
    model: str
    input_text: str
    max_output_tokens: int


def _canonical(value: object) -> str | None:
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return None


def _read_body(request: httpx.Request) -> _Body | None:
    """The exact request the adapter builds, or None. The routing and reasoning objects are
    compared in canonical JSON, so `false` and `0` are not confused."""
    try:
        body: Any = json.loads(request.content)
    except (ValueError, RecursionError, httpx.RequestNotRead):
        return None
    if not isinstance(body, dict) or set(body) != _BODY_KEYS:
        return None
    messages = body["messages"]
    if (
        body["stream"] is not False
        or _canonical(body["reasoning"]) != _REASONING_JSON
        or _canonical(body["provider"]) != _ROUTING_JSON
        or not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], dict)
        or set(messages[0]) != {"role", "content"}
        or messages[0]["role"] != "user"
    ):
        return None
    model, text, limit = body["model"], messages[0]["content"], body["max_tokens"]
    if (
        not isinstance(model, str)
        or not isinstance(text, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
    ):
        return None
    return _Body(model, text, limit)


def _body_refusals(body: _Body | None, policy: FreeModelPolicy) -> list[Refusal]:
    if body is None:
        return [Refusal("request_not_allowed", "the request body is not the pinned request")]
    refusals = []
    if (
        body.model != policy.model
        or body.model not in FREE_MODELS
        or not openrouter_adapter.is_free_model_id(body.model)
    ):
        refusals.append(Refusal("model_not_allowed", "model is not in LLMI_LIVE_MODELS"))
    if not MIN_OUTPUT_TOKENS <= body.max_output_tokens <= policy.max_output_tokens:
        refusals.append(
            Refusal("output_limit_exceeded", "the output limit is outside the configured range")
        )
    if input_token_bound(body.input_text) > policy.max_input_tokens:
        refusals.append(
            Refusal("input_too_large", "the input bound exceeds LLMI_LIVE_MAX_INPUT_TOKENS")
        )
    return refusals


def halt_reason(outcome: object, input_bound: int, output_bound: int) -> str | None:
    """A sign the free-only route, the attestation or the bounds no longer hold. It only stops
    the session: the request has already been made, and a halt never causes another one."""
    if isinstance(outcome, OpenRouterCallError):
        if outcome.category == "payment_required":
            return "payment_required_indicator"
        if outcome.http_status is not None and 300 <= outcome.http_status < 400:
            return "redirect_refused"
        return None
    if not isinstance(outcome, OpenRouterCompletion):
        return None
    if outcome.verdict.span_error == "openrouter.response.model_mismatch":
        return "response_model_mismatch"
    signals = outcome.signals
    if signals.reported_cost == "nonzero":
        return "cost_not_zero"
    if signals.reported_cost != "zero":
        return "cost_unverified"
    if signals.is_byok == "true":
        return "byok_used"
    if signals.is_byok != "false":
        return "byok_unverified"
    usage = outcome.usage
    if usage is not None and (
        (usage.input_tokens or 0) > input_bound or (usage.output_tokens or 0) > output_bound
    ):
        return "usage_exceeded_bound"
    return None


# ---------------------------------------------------------------- session
_SessionState = Literal["idle", "in_flight", "finished"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FreeModelPermit:
    """One authorized physical send. Holds bounds only, never the request or its content."""

    __slots__ = ("_finished", "_input_bound", "_output_bound", "_session", "_started")

    def __init__(self, session: FreeModelSession, input_bound: int, output_bound: int) -> None:
        self._session = session
        self._input_bound = input_bound
        self._output_bound = output_bound
        self._started = False
        self._finished = False

    def transmission_starting(self) -> None:
        session = self._session
        with session._lock:
            if self._started or self._finished:
                raise LiveRefusal([Refusal("permit_already_used", "a permit allows one send")])
            self._started = True
            session._attempts += 1
            session._request_state = "possibly_sent"

    def connection_not_established(self) -> None:
        session = self._session
        with session._lock:
            if session._request_state == "possibly_sent":
                session._request_state = "not_sent"

    def response_received(self) -> None:
        session = self._session
        with session._lock:
            if self._started:
                session._request_state = "sent"

    def finish(self, outcome: object) -> None:
        halt = halt_reason(outcome, self._input_bound, self._output_bound)
        session = self._session
        with session._lock:
            if self._finished:
                return
            self._finished = True
            session._state = "finished"
            if halt is not None and session._halted is None:
                session._halted = halt


class FreeModelSession:
    """Implements `groq_adapter.SendGate` for OpenRouter: the single request and its recorded
    fate. `env` is kept by reference and read again before the send."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._env = os.environ if env is None else env
        self._clock = clock
        self._lock = threading.Lock()
        self._state: _SessionState = "idle"
        self._request_state: RequestState = "not_sent"
        self._attempts = 0
        self._halted: str | None = None

    def __repr__(self) -> str:
        return f"FreeModelSession(state={self._state!r})"

    def authorize_send(self, http: httpx.Client, request: httpx.Request) -> FreeModelPermit:
        """A permit for this exact request, or `LiveRefusal`. A refusal leaves the slot free."""
        env = dict(self._env)
        decision = evaluate_free_model_policy(env, now=self._clock())
        if decision.policy is None or decision.refusals:
            raise LiveRefusal(decision.refusals)
        problems = send_boundary_problems(http, request)
        if problems:
            raise LiveRefusal([Refusal("endpoint_boundary_violation", p) for p in problems])
        if not _credential_bound(request, env):
            raise LiveRefusal(
                [
                    Refusal(
                        "credential_not_bound",
                        f"the request does not carry the {API_KEY_VAR} the policy validated",
                    )
                ]
            )
        body = _read_body(request)
        refusals = _body_refusals(body, decision.policy)
        if refusals or body is None:
            raise LiveRefusal(refusals)
        with self._lock:
            self._reserve()
        return FreeModelPermit(self, input_token_bound(body.input_text), body.max_output_tokens)

    def _reserve(self) -> None:
        if self._halted is not None:
            raise LiveRefusal([Refusal("session_halted", f"session halted ({self._halted})")])
        if self._state == "in_flight":
            raise LiveRefusal([Refusal("concurrency_limit", "a request is already in flight")])
        # Looked up on each call, so the one slot is shared with the Groq guard.
        if self._state == "finished" or not groq_live._PROCESS_SLOT.acquire(blocking=False):
            raise LiveRefusal([Refusal("request_limit_reached", "the one request has been used")])
        self._state = "in_flight"

    def snapshot(self) -> dict[str, object]:
        """Safe counters only (no content, no key)."""
        with self._lock:
            return {
                "requests_started": 0 if self._state == "idle" else 1,
                "max_requests": MAX_REQUESTS_CAP,
                "physical_attempts": self._attempts,
                "provider_request_state": self._request_state,
                "halted": self._halted,
            }
