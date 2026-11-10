"""Fail-closed Free Plan guard and endpoint boundary for one real Groq request (Phase 8A-3, ADR-44).

**No real request can be made yet, for two independent reasons.** `REAL_EXECUTION_ENABLED` is
false, and no Free Plan evidence has been released (`FREE_PLAN_MODELS` is empty and
`FREE_PLAN_EVIDENCE_VERIFIED_AT` is None), so `evaluate_free_plan_policy` refuses in every
environment. Releasing either is a reviewed code change.

**How it is used.** `FreePlanSession` is the `send_gate` of a real `ObservedGroqClient`, which
cannot be built without one. `authorize_send` runs after the request is built and only the span's
creation follows it. Each time it re-evaluates the whole policy against the clock and environment,
then the endpoint boundary, then that the request carries exactly the `GROQ_API_KEY` the policy
just validated (Phase 8A-4), then the body that will actually be sent, and only then takes the
single request slot. Any failure raises `LiveRefusal` and nothing is sent.

**What the Free Plan check is.** Groq exposes no API that tells a client which plan its account is
on. The plan is an **operator attestation**: `LLMI_GROQ_PLAN=free` and the UTC date of that check,
at most one day old. It is not proof from Groq's billing system. There is no paid mode and no
fallback. The account's real plan is the actual boundary.

**Limits.** One request, concurrency one, zero retries, per session and per process. The process
slot is never released, so a second session cannot send. These counters do not survive the process
and do not limit other processes.

**Request state.** `not_sent` means proven unsent: refused here, or a failure while connecting,
before any request byte is written. `possibly_sent` covers every other failure and an interruption
once the send has begun. `sent` means a response arrived. No state ever allows a second request.

Messages name the setting and the rule, never a value (like `live` and `gemini_live`).
"""

from __future__ import annotations

import hmac
import json
import os
import re
import ssl
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

import certifi
import httpcore
import httpx

from llm_insight_sdk import groq_adapter
from llm_insight_sdk.groq_adapter import GroqCallError, GroqCompletion, RequestState
from llm_insight_sdk.live import LiveRefusal, Refusal, _Reader, input_token_bound

PROVIDER = groq_adapter.PROVIDER
CONSENT_PHRASE = "groq-free-plan-single-call"
GROQ_MODE = "free-plan"
PLAN_FREE = "free"
API_KEY_VAR = "GROQ_API_KEY"

# Set in the environment, each of these means the operator's shell is configured to send traffic,
# or expose it, somewhere else. The client ignores the environment (`trust_env=False`), except
# that `ssl.create_default_context` itself honours SSLKEYLOGFILE. A set variable is refused rather
# than silently overridden.
CLIENT_ENV_OVERRIDES = (
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
)
# A key of another service in GROQ_API_KEY. Groq's own format is undocumented (R110), so a
# required prefix would refuse valid keys; only known foreign shapes are refused.
_FOREIGN_KEY_PREFIXES = ("sk-", "AIza", "lli_")
_UTC_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class FreePlanModel:
    """A model confirmed on the official pages and on the operator's Free Plan Limits page."""

    model: str
    sources: tuple[str, ...]


# Released at the evidence gate, after blockers B1 and B2 of `reports/phase-8a-0.md` are cleared.
# Empty is the fail-closed state: nothing here was verified, so no model is listed.
FREE_PLAN_EVIDENCE_VERIFIED_AT: datetime | None = None
FREE_PLAN_MODELS: Mapping[str, FreePlanModel] = {}
FREE_PLAN_EVIDENCE_MAX_AGE = timedelta(days=14)
ATTESTATION_MAX_AGE_DAYS = 1

# Hard caps: the environment can lower a limit where a range exists, never raise it.
MAX_REQUESTS_CAP = 1
MAX_INPUT_TOKENS_CAP = 1024
MIN_OUTPUT_TOKENS = 16
MAX_OUTPUT_TOKENS_CAP = groq_adapter.MAX_OUTPUT_TOKENS
# The adapter's deadline is fixed, so any other value would be silently ignored.
TIMEOUT_RANGE = (groq_adapter.TOTAL_DEADLINE_SECONDS, groq_adapter.TOTAL_DEADLINE_SECONDS)
RETRIES_RANGE = (0, 0)
CONCURRENCY_RANGE = (1, 1)

_FREE_PLAN_SERVICE_TIERS = (None, "on_demand")
_BODY_KEYS = frozenset(
    {"model", "messages", "max_completion_tokens", "stream", "include_reasoning"}
)


@dataclass(frozen=True)
class FreePlanPolicy:
    model: str
    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    max_concurrency: int
    plan_checked_on: date


@dataclass(frozen=True)
class FreePlanDecision:
    policy: FreePlanPolicy | None
    refusals: tuple[Refusal, ...]

    @property
    def allowed(self) -> bool:
        return self.policy is not None and not self.refusals


# ---------------------------------------------------------------- policy
def api_key_value(env: Mapping[str, str]) -> str:
    """`GROQ_API_KEY` exactly as the policy reads it (surrounding whitespace removed), or "".
    The runner gives the client this value, and the send gate checks that the request carries
    it, so the credential that is validated is the one that is sent."""
    return _Reader(env).raw(API_KEY_VAR)


def _check_api_key(r: _Reader) -> None:
    """Presence and shape only: the value is never stored, returned or quoted."""
    key = r.raw(API_KEY_VAR)
    if not key:
        r.refuse("api_key_missing", f"{API_KEY_VAR} is not set in the environment")
    elif key.startswith(_FOREIGN_KEY_PREFIXES):
        r.refuse("api_key_wrong_provider", f"{API_KEY_VAR} holds a key of another service")
    elif not groq_adapter._API_KEY.fullmatch(key):
        r.refuse("api_key_invalid", f"{API_KEY_VAR} must be visible ASCII characters")


def _attestation(r: _Reader, now: datetime) -> date | None:
    plan = r.raw("LLMI_GROQ_PLAN")
    if not plan:
        r.refuse("free_plan_unverified", "LLMI_GROQ_PLAN must state the account's plan ('free')")
    elif plan != PLAN_FREE:
        r.refuse("plan_not_free", "LLMI_GROQ_PLAN is not 'free': no paid plan is used")
    raw = r.raw("LLMI_GROQ_PLAN_CHECKED_ON")
    try:
        checked = date.fromisoformat(raw) if _UTC_DATE.fullmatch(raw) else None
    except ValueError:
        checked = None
    if checked is None:
        r.refuse(
            "free_plan_unverified",
            "LLMI_GROQ_PLAN_CHECKED_ON must be the UTC date (YYYY-MM-DD) of the plan check",
        )
        return None
    today = now.astimezone(UTC).date()
    if checked > today:
        r.refuse("free_plan_attestation_invalid", "LLMI_GROQ_PLAN_CHECKED_ON is in the future")
        return None
    if (today - checked).days > ATTESTATION_MAX_AGE_DAYS:
        r.refuse(
            "free_plan_attestation_stale",
            f"LLMI_GROQ_PLAN_CHECKED_ON is older than {ATTESTATION_MAX_AGE_DAYS} day; "
            "re-check the account's plan in the Groq console",
        )
        return None
    return checked


def _check_evidence(r: _Reader, now: datetime) -> None:
    verified_at = FREE_PLAN_EVIDENCE_VERIFIED_AT
    if verified_at is None:
        r.refuse("free_plan_evidence_missing", "no Free Plan evidence has been released")
    elif now < verified_at:
        r.refuse("free_plan_evidence_not_yet_valid", "the clock is before the Free Plan evidence")
    elif now - verified_at > FREE_PLAN_EVIDENCE_MAX_AGE:
        r.refuse(
            "free_plan_evidence_stale",
            f"the Free Plan evidence is older than {FREE_PLAN_EVIDENCE_MAX_AGE.days} days; "
            "re-read the official pages and the account's Limits page first",
        )


def _check_models(r: _Reader) -> str | None:
    models = r.names("LLMI_LIVE_MODELS")
    if len(models) > 1:
        r.refuse("model_list_not_single", "LLMI_LIVE_MODELS must name one model: no fallbacks")
    if any(m not in FREE_PLAN_MODELS for m in models):
        r.refuse(
            "model_not_free_plan_verified",
            f"LLMI_LIVE_MODELS: {sum(m not in FREE_PLAN_MODELS for m in models)} model(s) are "
            "not on the verified Free Plan allowlist",
        )
    return models[0] if len(models) == 1 else None


def evaluate_free_plan_policy(
    env: Mapping[str, str] | None = None, *, now: datetime
) -> FreePlanDecision:
    """Every reason a real Groq Free Plan request is not allowed, or none."""
    if now.utcoffset() is None:
        return FreePlanDecision(None, (Refusal("clock_invalid", "the clock has no timezone"),))
    r = _Reader(os.environ if env is None else env)
    if not groq_adapter.REAL_EXECUTION_ENABLED:
        r.refuse("real_execution_disabled", "real Groq requests are not enabled in this version")
    if r.raw("LLMI_PROVIDER_MODE") != "real":
        r.refuse("provider_mode_not_real", "LLMI_PROVIDER_MODE must be 'real'")
    if r.raw("LLMI_GROQ_MODE") != GROQ_MODE:
        r.refuse(
            "groq_mode_not_free_plan",
            f"LLMI_GROQ_MODE must be exactly '{GROQ_MODE}' (no paid mode exists)",
        )
    if r.raw("LLMI_LIVE_CONSENT") != CONSENT_PHRASE:
        r.refuse("consent_missing", f"LLMI_LIVE_CONSENT must be exactly '{CONSENT_PHRASE}'")
    _check_api_key(r)
    for var in CLIENT_ENV_OVERRIDES:
        if r.raw(var):
            r.refuse("client_env_override", f"{var} must not be set (direct Groq endpoint only)")
    checked_on = _attestation(r, now)
    _check_evidence(r, now)

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
        return FreePlanDecision(None, tuple(r.refusals))
    policy = FreePlanPolicy(
        model=model,
        max_requests=max_requests,
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        timeout_seconds=timeout,
        max_retries=retries,
        max_concurrency=concurrency,
        plan_checked_on=checked_on,
    )
    return FreePlanDecision(policy, tuple(r.refusals))


# ---------------------------------------------------------------- endpoint boundary
@lru_cache(maxsize=1)
def _certifi_roots() -> frozenset[bytes]:
    # Not `create_default_context`, which would open any SSLKEYLOGFILE.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    return frozenset(context.get_ca_certs(binary_form=True))


def _tls_problems(tls: object) -> list[str]:
    # A subclass could answer the checks below with whatever it chooses.
    if type(tls) is not ssl.SSLContext:
        return ["the TLS context must be a real ssl.SSLContext"]
    problems = []
    if frozenset(tls.get_ca_certs(binary_form=True)) != _certifi_roots():
        problems.append("TLS must trust exactly the pinned certifi bundle")
    if tls.minimum_version < ssl.TLSVersion.TLSv1_2:
        problems.append("TLS 1.2 or newer is required")
    if tls.keylog_filename is not None:
        problems.append("TLS key logging must be off")
    return problems


def _connection_problems(pool: object) -> list[str]:
    read = groq_adapter._attr
    problems = []
    if type(read(pool, "_network_backend")) is not httpcore.SyncBackend:
        problems.append("the default network backend must be used")
    if not groq_adapter._is_exactly(read(pool, "_max_connections"), 1):
        problems.append("the HTTP client must be limited to one connection")
    if not groq_adapter._is_exactly(read(pool, "_max_keepalive_connections"), 0):
        problems.append("connections must not be kept alive")
    if read(pool, "_http1") is not True or read(pool, "_http2") is not False:
        problems.append("only HTTP/1.1 may be used")
    return problems


def _request_problems(request: httpx.Request) -> list[str]:
    problems = []
    if str(request.url) != groq_adapter.ENDPOINT_URL or request.method != "POST":
        problems.append("the request must be POST to the pinned HTTPS Groq endpoint")
    headers = request.headers
    if (
        {name.lower() for name in headers} != groq_adapter._SENT_HEADERS
        or len(headers.get_list("authorization")) != 1
        or headers.get("host") != groq_adapter.GROQ_HOST
    ):
        problems.append("the request headers must be exactly the pinned set")
    if request.extensions != {"timeout": groq_adapter.GROQ_TIMEOUT.as_dict()}:
        problems.append("the request must carry only the bounded Groq timeouts")
    return problems


def _credential_bound(request: httpx.Request, env: Mapping[str, str]) -> bool:
    """Whether the request's one `Authorization` header is exactly `Bearer` and the key the policy
    just validated from `env`. Compared in constant time; neither value is kept or returned."""
    key = api_key_value(env)
    values = request.headers.get_list("authorization")
    if not key or len(values) != 1 or not values[0].isascii() or not key.isascii():
        return False
    return hmac.compare_digest(values[0].encode("ascii"), f"Bearer {key}".encode("ascii"))


def send_boundary_problems(http: object, request: httpx.Request) -> list[str]:
    """Every reason this request may not leave on this client; empty when it may. Reads
    configuration only. Adds to `live_groq_client_problems`, which it runs first. The transport
    and pool are checked again here, so neither layer relies on the other, and no check depends
    on the outcome of another: an unexpected pool is refused and still inspected."""
    read = groq_adapter._attr
    problems = groq_adapter.live_groq_client_problems(http)
    transport = read(http, "_transport")
    pool = read(transport, "_pool")
    problems.extend(groq_adapter.transport_problems(transport))
    problems.extend(_connection_problems(pool))
    problems.extend(_tls_problems(read(pool, "_ssl_context")))
    problems.extend(_request_problems(request))
    return list(dict.fromkeys(problems))


# ---------------------------------------------------------------- what will be sent
@dataclass(frozen=True)
class _Body:
    model: str
    input_text: str
    max_output_tokens: int


def _read_body(request: httpx.Request) -> _Body | None:
    """The exact request the adapter builds, or None. A JSONDecodeError would keep the whole body
    in `.doc`, so it is not allowed to outlive this function."""
    try:
        body: Any = json.loads(request.content)
    except (ValueError, RecursionError, httpx.RequestNotRead):
        return None
    if not isinstance(body, dict) or set(body) != _BODY_KEYS:
        return None
    messages = body["messages"]
    if (
        body["stream"] is not False
        or body["include_reasoning"] is not False
        or not isinstance(messages, list)
        or len(messages) != 1
        or not isinstance(messages[0], dict)
        or set(messages[0]) != {"role", "content"}
        or messages[0]["role"] != "user"
    ):
        return None
    model, text, limit = body["model"], messages[0]["content"], body["max_completion_tokens"]
    if (
        not isinstance(model, str)
        or not isinstance(text, str)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
    ):
        return None
    return _Body(model, text, limit)


def _body_refusals(body: _Body | None, policy: FreePlanPolicy) -> list[Refusal]:
    if body is None:
        return [Refusal("request_not_allowed", "the request body is not the pinned request")]
    refusals = []
    if body.model != policy.model or body.model not in FREE_PLAN_MODELS:
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


def _halt_reason(outcome: object, input_bound: int, output_bound: int) -> str | None:
    """A sign the Free Plan attestation or the bounds no longer hold. It only stops the session:
    the request has already been made, and a halt never causes another one."""
    if isinstance(outcome, GroqCallError):
        if outcome.category in ("precondition_failed", "payment_required"):
            return "paid_plan_indicator"
        if outcome.http_status is not None and 300 <= outcome.http_status < 400:
            return "redirect_refused"
        return None
    if not isinstance(outcome, GroqCompletion):
        return None
    if outcome.service_tier not in _FREE_PLAN_SERVICE_TIERS or outcome.service_tier_withheld:
        return "service_tier_not_free_plan"
    if outcome.verdict.span_error == "groq.response.model_mismatch":
        return "response_model_mismatch"
    usage = outcome.usage
    if usage is not None and (
        (usage.input_tokens or 0) > input_bound or (usage.output_tokens or 0) > output_bound
    ):
        return "usage_exceeded_bound"
    return None


# ---------------------------------------------------------------- session
# Taken once and never released: one real Groq request per process, across sessions.
_PROCESS_SLOT = threading.Lock()

_SessionState = Literal["idle", "in_flight", "finished"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FreePlanPermit:
    """One authorized physical send. Holds bounds only, never the request or its content."""

    __slots__ = ("_finished", "_input_bound", "_output_bound", "_session", "_started")

    def __init__(self, session: FreePlanSession, input_bound: int, output_bound: int) -> None:
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
        halt = _halt_reason(outcome, self._input_bound, self._output_bound)
        session = self._session
        with session._lock:
            if self._finished:
                return
            self._finished = True
            session._state = "finished"
            if halt is not None and session._halted is None:
                session._halted = halt


class FreePlanSession:
    """Implements `groq_adapter.SendGate`: the single request and its recorded fate. `env` is kept
    by reference and read again before the send; the API key is never copied from it."""

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
        return f"FreePlanSession(state={self._state!r})"

    def authorize_send(self, http: httpx.Client, request: httpx.Request) -> FreePlanPermit:
        """A permit for this exact request, or `LiveRefusal`. A refusal leaves the slot free.
        One copy of the environment is read for the whole authorization, so the policy and the
        credential check below see the same values."""
        env = dict(self._env)
        decision = evaluate_free_plan_policy(env, now=self._clock())
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
        return FreePlanPermit(self, input_token_bound(body.input_text), body.max_output_tokens)

    def _reserve(self) -> None:
        if self._halted is not None:
            raise LiveRefusal([Refusal("session_halted", f"session halted ({self._halted})")])
        if self._state == "in_flight":
            raise LiveRefusal([Refusal("concurrency_limit", "a request is already in flight")])
        if self._state == "finished" or not _PROCESS_SLOT.acquire(blocking=False):
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
