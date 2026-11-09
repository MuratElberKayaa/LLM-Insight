"""Groq chat completions adapter (Phase 8A-2, ADR-42, ADR-43): one CLIENT span per physical
`POST https://api.groq.com/openai/v1/chat/completions` request.

**Offline only at this checkpoint.** `provenance="real"` is refused until the free-plan guard of
the next checkpoint exists (`REAL_EXECUTION_ENABLED`), and `synthetic` provenance runs only over
an `httpx.MockTransport`, so nothing in this module can reach Groq yet.

Needs the optional dependency: `pip install "llm-insight-sdk[groq]"` (httpx 0.28.1, certifi).

**Surface.** One user message, non-streaming, at most `MAX_OUTPUT_TOKENS`. The request body is
exactly `model`, `messages`, `max_completion_tokens`, `stream: false` and
`include_reasoning: false`. Every other parameter is refused before anything is sent: tools,
function calling, streaming, response formats, service tiers, sampling settings and request
extensions. There is no retry, redirect, continuation or fallback, and no default model. The
compound systems (`groq/compound*`) are refused because they run server-side tools and several
models inside one request.

**Telemetry** (semconv pin `9ffc7e00c639`, `llminsight.attrmap.v1`, no backend change): provider
`groq`, operation `chat`, the request and response model, the response `id`, the finish reason
verbatim, `server.address`/`server.port` and `gen_ai.request.max_tokens`. Never: the prompt, the
output, `message.reasoning`, headers, the API key, provider error text, timing fields, `x_groq`
or `failed_generation`. A response field that is not named here is never read.

**Usage** (`usage_from_groq_body`). `prompt_tokens` and `completion_tokens` map to input and
output; `prompt_tokens_details.cached_tokens` and `completion_tokens_details.reasoning_tokens`,
which Groq does not document for this endpoint, map to the subset counters when present. Missing
counts make the usage unknown (`missing`). Non-counts, `prompt + completion != total`, a subset
above its total or a multi-model `usage_breakdown` make it unknown (`inconsistent`). Nothing is
estimated.

**Outcomes.** Groq documents only the finish reason `stop`, so every other value is handled from
the response alone: `stop` with text is `completed`; `length` is `incomplete`; `content_filter`
is `blocked`; `stop` with no text or an empty `choices` is `failed`/`empty_response`; `error` is
`failed`/`response_failed`; anything else, or a tool call, is `failed`/`unexpected_status`. A
response naming another model than the one requested is `failed`
(`groq.response.model_mismatch`), never `completed`. A 200 whose Content-Type is not exactly
`application/json` (`is_json_content_type`) is refused before its body is read. A body that is not
a chat completion, oversized or missing its model is `error`/`malformed_response`, and an `error`
object in a 200 response is `error`/`provider_error`.

**Failures** reach callers as `GroqCallError`, which holds only status, category and validated
keywords. It is raised outside the handling of the library's exception, so it does not chain to
one: an `httpx` error holds the request, and with it the `Authorization` header.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Literal

import certifi
import httpx

from llm_insight_sdk import _guards, instrumentation
from llm_insight_sdk._version import __version__
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.instrumentation import (
    InferenceRecorder,
    RequestParams,
    TokenUsage,
    new_call_id,
)
from llm_insight_sdk.providers import AttemptRecord, OutcomeStatus, TextOutcome, TextRequest
from llm_insight_sdk.telemetry import Telemetry

logger = logging.getLogger("llm_insight_sdk")

PROVIDER = "groq"
OPERATION = "chat"
GROQ_HOST = "api.groq.com"
GROQ_PORT = 443
ENDPOINT_URL = f"https://{GROQ_HOST}/openai/v1/chat/completions"

# The free-plan guard does not exist yet. Enabling this is a reviewed code change.
REAL_EXECUTION_ENABLED: bool = False

MAX_OUTPUT_TOKENS = 1024
MAX_INPUT_BYTES = 32_768
MAX_RESPONSE_BYTES = 1_048_576
MAX_ERROR_BODY_BYTES = 65_536
MAX_TOKEN_COUNT = 2**31 - 1
GROQ_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=1.0)
# httpx timeouts apply to each operation, so a slowly dripped body needs its own deadline.
TOTAL_DEADLINE_SECONDS = 30.0

# One optional `org/` segment, no `:variant` suffix, no query or further path.
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,127})?")
_COMPOUND_PREFIXES = ("groq/compound", "compound-")
_API_KEY = re.compile(r"[\x21-\x7e]{1,512}")
_SENT_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "host",
        "user-agent",
    }
)
_CLIENT_DEFAULT_HEADERS = frozenset({"accept", "accept-encoding", "connection", "user-agent"})
_BLOCKED_API_ACCESS = "blocked_api_access"
# RFC 9110 §8.3: media-type = type "/" subtype parameters. Each repetition starts at its own ";",
# so the pattern has one parse and cannot backtrack exponentially; the length is bounded as well.
_MAX_CONTENT_TYPE_LENGTH = 256
_TCHARS = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_QUOTED_STRING = r'"(?:[\t \x21\x23-\x5b\x5d-\x7e\x80-\xff]|\\[\t \x21-\x7e\x80-\xff])*"'
_PARAMETER_FORM = rf"{_TCHARS}=(?:{_TCHARS}|{_QUOTED_STRING})"
# ASCII matters: without it, IGNORECASE would let U+017F stand in for the "s" of "json".
_MEDIA_TYPE = re.compile(
    rf"application/json((?:[ \t]*;(?:[ \t]*{_PARAMETER_FORM})?)*)", re.IGNORECASE | re.ASCII
)
_PARAMETER = re.compile(rf"({_TCHARS})=({_TCHARS}|{_QUOTED_STRING})")
_STATUS_CATEGORIES = {
    401: "authentication_failed",
    402: "payment_required",
    403: "permission_denied",
    404: "not_found",
    408: "timeout",
    429: "rate_limited",
}

AUDITED_LIBRARY_VERSIONS = {"httpx": "0.28.1", "httpcore": "1.0.9", "certifi": "2026.10.19"}

Provenance = Literal["real", "synthetic"]
UsageCheck = Literal["consistent", "missing", "inconsistent"]
_MISSING = object()


class UnsupportedRequestError(ValueError):
    """The request uses a feature the Groq adapter does not support. Nothing was sent."""


class GroqCallError(Exception):
    """A failed physical request. Holds codes only, never provider text or request data."""

    def __init__(
        self,
        error_type: str,
        category: str,
        *,
        http_status: int | None = None,
        provider_reason: str | None = None,
    ) -> None:
        super().__init__(error_type)
        self.error_type = error_type  # read by `instrumentation.classify_error`
        self.category = category
        self.http_status = http_status
        self.provider_reason = provider_reason


def _malformed() -> GroqCallError:
    return GroqCallError("groq.response.malformed", "malformed_response")


# ---------------------------------------------------------------- usage
def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_TOKEN_COUNT else None


@dataclass(frozen=True)
class UsageReading:
    """The mapped usage (None = unknown, never 0) and why it is unknown."""

    usage: TokenUsage | None
    check: UsageCheck


def _detail(usage: Mapping[str, Any], group: str, name: str, ceiling: int) -> int | None:
    """A subset count: None when absent. Raises ValueError when present but not usable."""
    details = usage.get(group)
    if details is None:
        return None
    if not isinstance(details, dict):
        raise ValueError(group)
    raw = details.get(name)
    if raw is None:
        return None
    value = _count(raw)
    if value is None or value > ceiling:
        raise ValueError(group)
    return value


def usage_from_groq_body(body: Mapping[str, Any]) -> UsageReading:
    """Provider-reported usage of a chat completion body (module docstring)."""
    usage = body.get("usage")
    if usage is None:
        return UsageReading(None, "missing")
    if not isinstance(usage, dict):
        return UsageReading(None, "inconsistent")
    if body.get("usage_breakdown") or usage.get("usage_breakdown"):
        return UsageReading(None, "inconsistent")  # several models inside one request
    raw = [usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")]
    counts = [_count(value) for value in raw]
    if any(r is not None and c is None for r, c in zip(raw, counts, strict=True)):
        return UsageReading(None, "inconsistent")
    prompt, completion, total = counts
    if prompt is None or completion is None or total is None:
        return UsageReading(None, "missing")
    if prompt + completion != total:
        return UsageReading(None, "inconsistent")
    try:
        cached = _detail(usage, "prompt_tokens_details", "cached_tokens", prompt)
        reasoning = _detail(usage, "completion_tokens_details", "reasoning_tokens", completion)
    except ValueError:
        return UsageReading(None, "inconsistent")
    return UsageReading(
        TokenUsage(
            input_tokens=prompt,
            output_tokens=completion,
            cache_read_input_tokens=cached,
            reasoning_output_tokens=reasoning,
            source="provider_reported",
        ),
        "consistent",
    )


# ---------------------------------------------------------------- response parsing
def _keyword(value: object) -> str | None:
    return _guards.check(value, _guards.KEYWORD) if isinstance(value, str) else None


@dataclass(frozen=True)
class GroqVerdict:
    status: OutcomeStatus
    reason: str | None  # the provider's finish reason, verbatim
    category: str | None  # providers.ERROR_CATEGORIES, None when completed/incomplete
    span_error: str | None  # span error.type, None when the span stays UNSET
    finish_reason: str | None  # recorded on the span


def assess_choice(
    *, has_choice: bool, finish: object, text: str | None, tool_calls: bool
) -> GroqVerdict:
    """What the single choice of a readable response means (module docstring)."""
    if not has_choice:
        return GroqVerdict("failed", None, "empty_response", "groq.response.empty", None)
    reason = _keyword(finish)
    unexpected = GroqVerdict(
        "failed", reason, "unexpected_status", "groq.response.unexpected_finish", reason
    )
    if tool_calls:
        return unexpected  # no tools are ever sent, so a tool call is not an answer
    if reason == "stop":
        if text is not None and text.strip():
            return GroqVerdict("completed", None, None, None, reason)
        return GroqVerdict("failed", reason, "empty_response", "groq.response.empty", reason)
    if reason == "length":
        return GroqVerdict("incomplete", reason, None, None, reason)
    if reason == "content_filter":
        return GroqVerdict("blocked", reason, "content_blocked", None, reason)
    if reason == "error":
        return GroqVerdict("failed", reason, "response_failed", "groq.response.failed", reason)
    return unexpected


class GroqCompletion:
    """A readable chat completion: validated metadata and, for the application only, the text.

    Deliberately not a dataclass: `dataclasses.asdict(outcome)` copies this object instead of
    expanding it, so the text cannot appear in serialized metadata."""

    __slots__ = (
        "_text",
        "id_withheld",
        "response_id",
        "response_model",
        "service_tier",
        "usage",
        "verdict",
    )

    def __init__(
        self,
        *,
        text: str | None,
        response_id: str | None,
        id_withheld: bool,
        response_model: str,
        service_tier: str | None,
        usage: TokenUsage | None,
        verdict: GroqVerdict,
    ) -> None:
        self._text = text
        self.response_id = response_id
        self.id_withheld = id_withheld
        self.response_model = response_model
        self.service_tier = service_tier
        self.usage = usage
        self.verdict = verdict

    @property
    def text(self) -> str | None:
        return self._text

    def __repr__(self) -> str:
        return f"GroqCompletion(status={self.verdict.status!r})"


_UNREADABLE = object()


def _decode(raw: bytes) -> object:
    """Parsed JSON or `_UNREADABLE`. A JSONDecodeError keeps the whole body in `.doc`, so it is
    not allowed to outlive this function."""
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return _UNREADABLE


def _error_keywords(body: object) -> tuple[str, ...]:
    """The validated `error.code` and `error.type`. The error's message is never read."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return ()
    found = (_keyword(error.get("code")), _keyword(error.get("type")))
    return tuple(k for k in found if k is not None)


def _read_choice(choice: object) -> tuple[str | None, object, bool]:
    """(text, finish reason, has tool calls) of the single choice. `message.reasoning` is never
    read."""
    if not isinstance(choice, dict):
        raise _malformed()
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise _malformed()
    content = message.get("content")
    finish = choice.get("finish_reason")
    if not (content is None or isinstance(content, str)) or not (
        finish is None or isinstance(finish, str)
    ):
        raise _malformed()
    return content, finish, bool(message.get("tool_calls"))


def parse_completion(raw: bytes, requested_model: str) -> GroqCompletion:
    """Parse a 200 body. Raises `GroqCallError` for an error object or an unusable shape."""
    body = _decode(raw)
    if not isinstance(body, dict):
        raise _malformed()
    if body.get("error") is not None:
        keywords = _error_keywords(body)
        raise GroqCallError(
            "groq.response.error",
            "precondition_failed" if _BLOCKED_API_ACCESS in keywords else "provider_error",
            http_status=200,
            provider_reason=keywords[0] if keywords else None,
        )
    if body.get("object") != "chat.completion":
        raise _malformed()
    reported_model = body.get("model")
    response_model = (
        _guards.check(reported_model, _guards.MODEL) if isinstance(reported_model, str) else None
    )
    if response_model is None:
        raise _malformed()
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) > 1:
        raise _malformed()

    text, finish, tool_calls = _read_choice(choices[0]) if choices else (None, None, False)
    verdict = assess_choice(
        has_choice=bool(choices), finish=finish, text=text, tool_calls=tool_calls
    )
    if response_model != requested_model:
        verdict = GroqVerdict(
            "failed",
            verdict.reason,
            "unexpected_status",
            "groq.response.model_mismatch",
            verdict.finish_reason,
        )

    reported_id = body.get("id")
    response_id = _guards.check(reported_id, _guards.RESPONSE_ID)
    return GroqCompletion(
        text=text,
        response_id=response_id,
        id_withheld=isinstance(reported_id, str) and response_id is None,
        response_model=response_model,
        service_tier=_keyword(body.get("service_tier")),
        usage=usage_from_groq_body(body).usage,
        verdict=verdict,
    )


def _read_limited(response: httpx.Response, limit: int, deadline: float) -> bytes:
    """The response body, or `GroqCallError`/`httpx.ReadTimeout` before reading past `limit`
    bytes or `deadline`. A compressed body is refused, so `limit` is the real size."""
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise _malformed()
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isdecimal() or int(declared) > limit):
        raise GroqCallError("groq.response.oversized", "malformed_response")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > limit:
            raise GroqCallError("groq.response.oversized", "malformed_response")
        if time.monotonic() > deadline:
            raise httpx.ReadTimeout("the response deadline passed")
        chunks.append(chunk)
    return b"".join(chunks)


def _unquote(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value[1:-1]) if value.startswith('"') else value


def is_json_content_type(value: str) -> bool:
    """Whether one Content-Type value is exactly `application/json` under RFC 9110: the type and
    subtype are case-insensitive, and parameters must be well formed. A `charset`, if present,
    must appear once and be UTF-8, the only encoding RFC 8259 allows between systems."""
    if len(value) > _MAX_CONTENT_TYPE_LENGTH:
        return False
    match = _MEDIA_TYPE.fullmatch(value)
    if match is None:
        return False
    charsets = [
        _unquote(content).lower()
        for name, content in _PARAMETER.findall(match[1])
        if name.lower() == "charset"
    ]
    return charsets in ([], ["utf-8"])


def _has_json_content_type(response: httpx.Response) -> bool:
    """Exactly one Content-Type header, holding a JSON media type. Checked before the body."""
    values = response.headers.get_list("content-type")
    return len(values) == 1 and is_json_content_type(values[0])


def status_category(status: int, keywords: tuple[str, ...] = ()) -> str:
    """Provider-neutral category of a non-200 response, from the status and keywords only."""
    if _BLOCKED_API_ACCESS in keywords:
        return "precondition_failed"
    if status in _STATUS_CATEGORIES:
        return _STATUS_CATEGORIES[status]
    if status in (400, 413, 422):
        return "invalid_request"
    if status == 498 or status >= 500:
        return "provider_error"
    return "http_error"


def _http_failure(response: httpx.Response, deadline: float) -> GroqCallError:
    """A non-200 response as a `GroqCallError`. An unreadable error body leaves the status."""
    try:
        body = _decode(_read_limited(response, MAX_ERROR_BODY_BYTES, deadline))
    except (GroqCallError, httpx.HTTPError):
        body = _UNREADABLE
    keywords = _error_keywords(body)
    status = response.status_code
    return GroqCallError(
        str(status),
        status_category(status, keywords),
        http_status=status,
        provider_reason=keywords[0] if keywords else None,
    )


# ---------------------------------------------------------------- HTTP client boundary
def build_groq_http_client() -> httpx.Client:
    """The only client shape accepted for a real request: no proxy or CA environment settings,
    no redirects, no retries, TLS verified against the pinned certifi bundle, one connection."""
    context = ssl.create_default_context(cafile=certifi.where())
    return httpx.Client(
        verify=context,
        trust_env=False,
        follow_redirects=False,
        timeout=GROQ_TIMEOUT,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )


def _attr(obj: object, name: str) -> Any:
    return getattr(obj, name, _MISSING)


def _routing_problems(http: httpx.Client) -> list[str]:
    """Configuration that could send the request or its key somewhere else, or expose them. It
    applies in every mode, because a proxy mount outranks even a mock transport."""
    problems = []
    if str(_attr(http, "base_url")) not in ("", "/"):
        problems.append("no base URL may be configured")
    if _attr(http, "follow_redirects") is not False:
        problems.append("HTTP redirects must not be followed")
    if _attr(http, "_trust_env") is not False:
        problems.append("proxy and CA environment settings must be ignored (trust_env=False)")
    if _attr(http, "_mounts") != {}:
        problems.append("no proxy or transport mount may be configured")
    if _attr(http, "auth") is not None:
        problems.append("no httpx auth may be configured")
    hooks = _attr(http, "event_hooks")
    if not isinstance(hooks, dict) or any(hooks.values()):
        problems.append("no HTTP event hook may be configured")
    headers = _attr(http, "headers")
    if (
        not isinstance(headers, httpx.Headers)
        or not {name.lower() for name in headers} <= _CLIENT_DEFAULT_HEADERS
    ):
        problems.append("the HTTP client must carry no custom or credential header")
    if len(_attr(http, "params")) or len(_attr(http, "cookies")):
        problems.append("no default query parameter or cookie may be configured")
    return problems


def groq_library_problems() -> list[str]:
    """Why the installed client libraries are not the audited ones (empty: they are)."""
    problems = []
    for name, version in AUDITED_LIBRARY_VERSIONS.items():
        try:
            installed: str | None = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            problems.append(f"{name} must be the audited version {version}")
    return problems


def live_groq_client_problems(http: object) -> list[str]:
    """Every reason `http` may not carry a real Groq key; empty when it may. Reads configuration
    only, and an unreadable attribute is a problem."""
    problems = groq_library_problems()
    if type(http) is not httpx.Client:
        return [*problems, "the HTTP client must be exactly an httpx.Client"]
    problems.extend(_routing_problems(http))
    if _attr(http, "timeout") != GROQ_TIMEOUT:
        problems.append("the HTTP client must use the bounded Groq timeouts")
    transport = _attr(http, "_transport")
    pool = _attr(transport, "_pool")
    if type(transport) is not httpx.HTTPTransport or type(pool).__name__ != "ConnectionPool":
        problems.append("the default direct HTTPTransport (no proxy, no mock) must be used")
    else:
        if _attr(pool, "_retries") != 0:
            problems.append("transport retries must be disabled")
        tls = _attr(pool, "_ssl_context")
        if (
            _attr(tls, "verify_mode") != ssl.CERT_REQUIRED
            or _attr(tls, "check_hostname") is not True
        ):
            problems.append("TLS certificate and hostname verification must be enabled")
    return problems


def check_live_groq_client(http: object) -> None:
    """Raise `ConfigError` naming every `live_groq_client_problems` entry (no value)."""
    problems = live_groq_client_problems(http)
    if problems:
        raise ConfigError("live Groq client: " + "; ".join(problems))


def _check_http_client(http: object, provenance: str) -> None:
    if provenance == "real":
        if not REAL_EXECUTION_ENABLED:
            raise ConfigError("provenance: real Groq requests are not enabled in this version")
        check_live_groq_client(http)
        return
    if provenance != "synthetic":
        raise ConfigError("provenance: must be 'real' or 'synthetic'")
    if type(http) is not httpx.Client:
        raise ConfigError("http_client: must be exactly an httpx.Client")
    problems = _routing_problems(http)
    if type(_attr(http, "_transport")) is not httpx.MockTransport:
        problems.append("synthetic provenance needs an httpx.MockTransport")
    if problems:
        raise ConfigError("http_client: " + "; ".join(problems))


# ---------------------------------------------------------------- the instrumented client
AttemptObserver = Callable[[int, object], None]


def _notify(observer: AttemptObserver | None, attempt: int, outcome: object) -> None:
    if observer is None:
        return
    try:
        observer(attempt, outcome)
    except Exception:  # an observer must not change the call's outcome
        logger.debug("llm_insight_sdk: an attempt observer raised", exc_info=False)


def _refuse_unsupported(
    model: object, input_text: object, max_tokens: object, unsupported: Mapping[str, Any]
) -> None:
    for name in sorted(unsupported):
        raise UnsupportedRequestError(f"{name} is not supported by the Groq adapter")
    if (
        not isinstance(model, str)
        or not MODEL_ID.fullmatch(model)
        or _guards.check(model, _guards.MODEL) is None
    ):
        raise UnsupportedRequestError("model must be an explicit, plain Groq model ID")
    if model.lower().startswith(_COMPOUND_PREFIXES):
        raise UnsupportedRequestError("compound systems are not supported by the Groq adapter")
    if not isinstance(input_text, str) or not input_text.strip():
        raise UnsupportedRequestError("input_text must be one non-empty text string")
    try:
        encoded = input_text.encode("utf-8")
    except UnicodeEncodeError:
        encoded = None
    if encoded is None or len(encoded) > MAX_INPUT_BYTES:
        raise UnsupportedRequestError(
            f"input_text must be valid UTF-8, {MAX_INPUT_BYTES} bytes max"
        )
    if (
        not isinstance(max_tokens, int)
        or isinstance(max_tokens, bool)
        or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS
    ):
        raise UnsupportedRequestError(
            f"max_completion_tokens must be an integer from 1 to {MAX_OUTPUT_TOKENS}"
        )


def _request_body(model: str, input_text: str, max_tokens: int) -> bytes:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": input_text}],
        "max_completion_tokens": max_tokens,
        "stream": False,
        "include_reasoning": False,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _record_response(recorder: InferenceRecorder | None, completion: GroqCompletion) -> None:
    if recorder is None:
        return
    try:
        finish = completion.verdict.finish_reason
        recorder.record_response(
            response_model=completion.response_model,
            response_id=completion.response_id,
            finish_reasons=[finish] if finish else [],
            usage=completion.usage,
        )
        if completion.id_withheld:
            # Counts the withheld ID. The rejected value itself was not kept.
            recorder.set_metadata("gen_ai.response.id", "")
        if completion.verdict.span_error is not None:
            recorder.mark_failed(completion.verdict.span_error)
    except Exception:  # telemetry must never break the call
        logger.debug("llm_insight_sdk: recording the Groq response failed", exc_info=False)


class ObservedGroqClient:
    """Instrumented Groq chat completion: exactly one physical request per call.

    The endpoint is a constant. `http_client` is required, so no client is created implicitly,
    and it is re-checked immediately before every send. The API key is held only here and added
    to each request; it is never set on the HTTP client."""

    def __init__(
        self,
        telemetry: Telemetry,
        api_key: str,
        *,
        http_client: httpx.Client,
        provenance: Provenance,
    ) -> None:
        _check_http_client(http_client, provenance)
        if not isinstance(api_key, str) or not _API_KEY.fullmatch(api_key):
            raise ConfigError("api_key: must be a non-empty string of visible ASCII characters")
        self._telemetry = telemetry
        self._api_key = api_key
        self._http = http_client
        self._provenance: Provenance = provenance

    def __repr__(self) -> str:
        return f"ObservedGroqClient(provenance={self._provenance!r})"

    @contextmanager
    def _attempt_span(
        self,
        model: str,
        call_id: str,
        prompt_name: str | None,
        prompt_version: str | None,
        max_tokens: int,
    ) -> Iterator[InferenceRecorder | None]:
        """The attempt's span, or None if telemetry itself fails: the request still runs and
        its outcome is never changed by telemetry."""
        try:
            cm: AbstractContextManager[InferenceRecorder] = instrumentation.inference_span(
                self._telemetry.tracer,
                on_withheld=self._telemetry._withheld,
                provenance=self._provenance,
                operation=OPERATION,
                provider=PROVIDER,
                request_model=model,
                call_id=call_id,
                attempt=1,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                params=RequestParams(max_tokens=max_tokens),
            )
            recorder = cm.__enter__()
        except Exception:
            logger.debug("llm_insight_sdk: starting the Groq span failed", exc_info=False)
            yield None
            return
        recorder.set_metadata("server.address", GROQ_HOST)
        recorder.set_metadata("server.port", GROQ_PORT)
        try:
            yield recorder
        except BaseException as exc:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)  # records ERROR, re-raises exc
            except BaseException as raised:
                if raised is not exc:
                    logger.debug("llm_insight_sdk: ending the Groq span failed", exc_info=False)
            raise
        else:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                logger.debug("llm_insight_sdk: ending the Groq span failed", exc_info=False)

    def _build_request(self, content: bytes) -> httpx.Request:
        request = self._http.build_request(
            "POST",
            ENDPOINT_URL,
            content=content,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": f"llm-insight-sdk/{__version__}",
            },
            timeout=GROQ_TIMEOUT,
        )
        names = {name.lower() for name in request.headers}
        if str(request.url) != ENDPOINT_URL or request.method != "POST" or names != _SENT_HEADERS:
            raise ConfigError("http_client: the request is not the pinned Groq request")
        return request

    def _exchange(self, request: httpx.Request, requested_model: str) -> GroqCompletion:
        """One physical exchange. Any httpx error becomes a `GroqCallError` outside its handler,
        so the exception, which holds the request and its header, does not escape."""
        deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
        failure: GroqCallError | None = None
        raw = b""
        try:
            response = self._http.send(request, stream=True, follow_redirects=False, auth=None)
            try:
                if response.status_code != 200:
                    failure = _http_failure(response, deadline)
                elif not _has_json_content_type(response):
                    failure = _malformed()
                else:
                    raw = _read_limited(response, MAX_RESPONSE_BYTES, deadline)
            finally:
                response.close()
        except httpx.TimeoutException:
            failure = GroqCallError("timeout", "timeout")
        except httpx.TransportError as exc:
            failure = GroqCallError(f"httpx.{type(exc).__name__}", "connection_error")
        except httpx.HTTPError as exc:
            failure = GroqCallError(f"httpx.{type(exc).__name__}", "unexpected_error")
        if failure is not None:
            raise failure
        return parse_completion(raw, requested_model)

    def create_chat_completion(
        self,
        *,
        model: str,
        input_text: str,
        max_completion_tokens: int,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        attempt_observer: AttemptObserver | None = None,
        **unsupported: Any,
    ) -> GroqCompletion:
        """Exactly one physical request. `input_text` and the output are never recorded, and
        `attempt_observer(1, completion_or_exception)` is called once; its errors are ignored."""
        _refuse_unsupported(model, input_text, max_completion_tokens, unsupported)
        _check_http_client(self._http, self._provenance)  # the client that actually sends
        request = self._build_request(_request_body(model, input_text, max_completion_tokens))
        with self._attempt_span(
            model, new_call_id(), prompt_name, prompt_version, max_completion_tokens
        ) as recorder:
            try:
                completion = self._exchange(request, model)
            except BaseException as exc:
                _notify(attempt_observer, 1, exc)
                raise
            _record_response(recorder, completion)
            _notify(attempt_observer, 1, completion)
            return completion


class GroqTextAdapter:
    """`TextGenerationAdapter` over `ObservedGroqClient`: one non-streaming text request, one
    physical attempt, a metadata-only outcome, and provider failures returned as an outcome."""

    provider = PROVIDER

    def __init__(self, client: ObservedGroqClient) -> None:
        self._client = client

    def generate_text(self, request: TextRequest) -> TextOutcome:
        records: list[AttemptRecord] = []

        def observe(attempt: int, outcome: object) -> None:
            if isinstance(outcome, GroqCompletion):
                records.append(
                    AttemptRecord(
                        attempt,
                        usage=outcome.usage,
                        response_model=outcome.response_model,
                        service_tier=outcome.service_tier,
                        error_category=outcome.verdict.category,
                    )
                )
                return
            category = outcome.category if isinstance(outcome, GroqCallError) else None
            records.append(AttemptRecord(attempt, error_category=category or "unexpected_error"))

        started = time.monotonic()
        try:
            completion = self._client.create_chat_completion(
                model=request.model,
                input_text=request.input_text,
                max_completion_tokens=request.max_output_tokens,
                prompt_name=request.prompt_name,
                prompt_version=request.prompt_version,
                attempt_observer=observe,
            )
        except (UnsupportedRequestError, ConfigError):
            raise  # refused before anything was sent
        except Exception as exc:
            return TextOutcome(
                provider=PROVIDER,
                status="error",
                request_model=request.model,
                response_model=None,
                response_id=None,
                service_tier=None,
                usage=None,
                attempts=tuple(records),
                error_category=records[-1].error_category if records else "unexpected_error",
                duration_ms=_elapsed(started),
                provider_reason=exc.provider_reason if isinstance(exc, GroqCallError) else None,
            )
        verdict = completion.verdict
        return TextOutcome(
            provider=PROVIDER,
            status=verdict.status,
            request_model=request.model,
            response_model=completion.response_model,
            response_id=completion.response_id,
            service_tier=completion.service_tier,
            usage=completion.usage,
            attempts=tuple(records),
            error_category=verdict.category,
            duration_ms=_elapsed(started),
            provider_response=completion,
            incomplete_reason=verdict.reason if verdict.status == "incomplete" else None,
            provider_reason=verdict.reason,
        )


def _elapsed(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)
