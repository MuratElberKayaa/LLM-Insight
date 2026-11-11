"""OpenRouter chat completions adapter (ADR-46): one CLIENT span per physical
`POST https://openrouter.ai/api/v1/chat/completions` request, for exactly one `:free` model.

**No real request can be made yet.** `provenance="real"` is refused while
`REAL_EXECUTION_ENABLED` is false, and `synthetic` provenance runs only over an
`httpx.MockTransport`. Once enabled by a reviewed change, a real client must carry the free-model
guard (`openrouter_live.FreeModelSession`, ADR-47) as its `send_gate`.

Needs the optional dependency: `pip install "llm-insight-sdk[openrouter]"` (httpx, certifi; the
same audited pins as the Groq adapter, whose endpoint-boundary checks are reused unchanged).

**Surface.** One user message, non-streaming, at most `MAX_OUTPUT_TOKENS`. The model must be one
explicit `org/name:free` ID: routers (`openrouter/*`), aliases and any other variant are refused.
The request body is exactly `model`, `messages`, `max_tokens`, `stream: false`,
`reasoning: {exclude: true}` and the routing constraints in `PROVIDER_ROUTING`: no provider
fallback, no data collection, and a maximum price of 0 for prompt, completion, request and image.
`models`, `route`, `plugins`, `sort`, tools, response formats, sampling settings and every other
parameter are refused before anything is sent. There is no retry, redirect, continuation or
fallback, and no default model. The routing constraints are a second layer: whether OpenRouter
enforces them for a given model is evidence for the guard (ADR-47), not something this module
assumes.

**Telemetry** (semconv pin `9ffc7e00c639`, `llminsight.attrmap.v1`, no backend change): provider
`openrouter`, operation `chat`, the request and response model, the response `id`, the normalized
finish reason verbatim, `server.address`/`server.port` and `gen_ai.request.max_tokens`. Never: the
prompt, the output, `message.reasoning`, `native_finish_reason`, the upstream `provider`, headers,
the API key, provider error text, `usage.cost`, `cost_details` or `is_byok`. A response field
that is not named here is never read.

**Usage** (`usage_from_openrouter_body`). `prompt_tokens` and `completion_tokens` map to input and
output; `prompt_tokens_details.cached_tokens`, `prompt_tokens_details.cache_write_tokens` and
`completion_tokens_details.reasoning_tokens` map to the subset counters when present. Missing
counts make the usage unknown (`missing`); non-counts, `prompt + completion != total` or a subset
above its total make it unknown (`inconsistent`). Nothing is estimated.

**Free-routing signals.** `usage.cost` (credits, per OpenRouter's documentation) and
`usage.is_byok` are read into `FreeRoutingSignals` for the guard's post-call check only. They are
never exported and never become a ledger cost: OpenRouter cost stays unknown (ADR-40).

**Outcomes.** As the Groq adapter, from the response alone: `stop` with text is `completed`;
`length` is `incomplete`; `content_filter` is `blocked`; `stop` with no text or no choice is
`failed`/`empty_response`; `error`, or a choice carrying an `error`, is `failed`/`response_failed`;
anything else, or a tool call, is `failed`/`unexpected_status`. A response naming another model is
`failed` (`openrouter.response.model_mismatch`). A 200 whose Content-Type is not exactly
`application/json` is refused before its body is read. A body that is not a chat completion,
oversized or missing its model is `error`/`malformed_response`. An `error` object in a 200
response is `error`, never a completion: OpenRouter documents that errors after routing can
arrive with HTTP 200.

**Failures** reach callers as `OpenRouterCallError`, which holds only status, category and
validated keywords, and is raised outside the handling of the library's exception.
"""

from __future__ import annotations

import json
import logging
import math
import re
import ssl
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal

import certifi
import httpx

from llm_insight_sdk import _guards, groq_adapter, instrumentation
from llm_insight_sdk._version import __version__
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.groq_adapter import (
    AttemptObserver,
    Provenance,
    RequestState,
    SendGate,
    SendPermit,
    UsageReading,
)
from llm_insight_sdk.instrumentation import (
    InferenceRecorder,
    RequestParams,
    TokenUsage,
    new_call_id,
)
from llm_insight_sdk.live import LiveRefusal
from llm_insight_sdk.providers import AttemptRecord, OutcomeStatus, TextOutcome, TextRequest
from llm_insight_sdk.telemetry import Telemetry

__all__ = [
    "ENDPOINT_URL",
    "PROVIDER",
    "REAL_EXECUTION_ENABLED",
    "FreeRoutingSignals",
    "ObservedOpenRouterClient",
    "OpenRouterCallError",
    "OpenRouterCompletion",
    "OpenRouterTextAdapter",
    "RequestState",
    "UnsupportedRequestError",
    "build_openrouter_http_client",
    "live_openrouter_client_problems",
    "parse_completion",
    "usage_from_openrouter_body",
]

logger = logging.getLogger("llm_insight_sdk")

PROVIDER = "openrouter"
OPERATION = "chat"
OPENROUTER_HOST = "openrouter.ai"
OPENROUTER_PORT = 443
ENDPOINT_URL = f"https://{OPENROUTER_HOST}/api/v1/chat/completions"

# No free-model or routing evidence has been released (`openrouter_live`, ADR-47) and real
# requests remain NO-GO. Enabling this is a reviewed code change.
REAL_EXECUTION_ENABLED: bool = False

MAX_OUTPUT_TOKENS = 1024
MAX_INPUT_BYTES = groq_adapter.MAX_INPUT_BYTES
MAX_RESPONSE_BYTES = groq_adapter.MAX_RESPONSE_BYTES
MAX_ERROR_BODY_BYTES = groq_adapter.MAX_ERROR_BODY_BYTES
OPENROUTER_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=1.0)
TOTAL_DEADLINE_SECONDS = 30.0

FREE_SUFFIX = ":free"
# `org/name:free`, nothing else: no router, alias (`~`), other variant, query or further path.
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}:free")
_ROUTER_ORGS = frozenset({"openrouter"})
# The routing constraints sent with every request (ADR-47 decides whether they are trusted).
PROVIDER_ROUTING: Mapping[str, Any] = {
    "allow_fallbacks": False,
    "data_collection": "deny",
    "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0},
}
REQUEST_FIELDS = ("model", "messages", "max_tokens", "stream", "reasoning", "provider")
_STATUS_CATEGORIES = {
    400: "invalid_request",
    401: "authentication_failed",
    402: "payment_required",
    403: "permission_denied",
    404: "not_found",
    408: "timeout",
    413: "invalid_request",
    422: "invalid_request",
    429: "rate_limited",
}

UsageCheck = groq_adapter.UsageCheck
SignalState = Literal["zero", "nonzero", "absent", "invalid"]
FlagState = Literal["true", "false", "absent", "invalid"]


class UnsupportedRequestError(ValueError):
    """The request uses a feature the OpenRouter adapter does not support. Nothing was sent."""


class OpenRouterCallError(Exception):
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


def _malformed() -> OpenRouterCallError:
    return OpenRouterCallError("openrouter.response.malformed", "malformed_response")


# ---------------------------------------------------------------- usage
def usage_from_openrouter_body(body: Mapping[str, Any]) -> UsageReading:
    """Provider-reported usage of a chat completion body (module docstring)."""
    usage = body.get("usage")
    if usage is None:
        return UsageReading(None, "missing")
    if not isinstance(usage, dict):
        return UsageReading(None, "inconsistent")
    raw = [usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")]
    counts = [groq_adapter._count(value) for value in raw]
    if any(r is not None and c is None for r, c in zip(raw, counts, strict=True)):
        return UsageReading(None, "inconsistent")
    prompt, completion, total = counts
    if prompt is None or completion is None or total is None:
        return UsageReading(None, "missing")
    if prompt + completion != total:
        return UsageReading(None, "inconsistent")
    detail = groq_adapter._detail
    try:
        cached = detail(usage, "prompt_tokens_details", "cached_tokens", prompt)
        written = detail(usage, "prompt_tokens_details", "cache_write_tokens", prompt)
        reasoning = detail(usage, "completion_tokens_details", "reasoning_tokens", completion)
    except ValueError:
        return UsageReading(None, "inconsistent")
    if cached is not None and written is not None and cached + written > prompt:
        return UsageReading(None, "inconsistent")
    return UsageReading(
        TokenUsage(
            input_tokens=prompt,
            output_tokens=completion,
            cache_read_input_tokens=cached,
            cache_write_input_tokens=written,
            reasoning_output_tokens=reasoning,
            source="provider_reported",
        ),
        "consistent",
    )


@dataclass(frozen=True)
class FreeRoutingSignals:
    """What the response says about the free-only route, for the guard only. Never exported.

    `reported_cost` is `zero` only for an int or float exactly 0; `is_byok` is `false` only for
    the JSON value false. Anything else cannot confirm a free, non-BYOK route."""

    reported_cost: SignalState
    is_byok: FlagState


def _cost_state(value: object) -> SignalState:
    if value is None:
        return "absent"
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "invalid"
    if not math.isfinite(value) or value < 0:
        return "invalid"
    return "zero" if value == 0 else "nonzero"


def _flag_state(value: object) -> FlagState:
    if value is None:
        return "absent"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "invalid"


def routing_signals(body: Mapping[str, Any]) -> FreeRoutingSignals:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return FreeRoutingSignals("absent" if usage is None else "invalid", "absent")
    return FreeRoutingSignals(_cost_state(usage.get("cost")), _flag_state(usage.get("is_byok")))


# ---------------------------------------------------------------- response parsing
_keyword = groq_adapter._keyword


@dataclass(frozen=True)
class OpenRouterVerdict:
    status: OutcomeStatus
    reason: str | None  # the normalized finish reason, verbatim
    category: str | None  # providers.ERROR_CATEGORIES, None when completed/incomplete
    span_error: str | None  # span error.type, None when the span stays UNSET
    finish_reason: str | None  # recorded on the span


def assess_choice(
    *, has_choice: bool, finish: object, text: str | None, tool_calls: bool, choice_error: bool
) -> OpenRouterVerdict:
    """What the single choice of a readable response means (module docstring)."""
    if not has_choice:
        return OpenRouterVerdict(
            "failed", None, "empty_response", "openrouter.response.empty", None
        )
    reason = _keyword(finish)
    failed = OpenRouterVerdict(
        "failed", reason, "response_failed", "openrouter.response.failed", reason
    )
    if choice_error:
        return failed
    unexpected = OpenRouterVerdict(
        "failed", reason, "unexpected_status", "openrouter.response.unexpected_finish", reason
    )
    if tool_calls:
        return unexpected  # no tools are ever sent, so a tool call is not an answer
    if reason == "stop":
        if text is not None and text.strip():
            return OpenRouterVerdict("completed", None, None, None, reason)
        return OpenRouterVerdict(
            "failed", reason, "empty_response", "openrouter.response.empty", reason
        )
    if reason == "length":
        return OpenRouterVerdict("incomplete", reason, None, None, reason)
    if reason == "content_filter":
        return OpenRouterVerdict("blocked", reason, "content_blocked", None, reason)
    if reason == "error":
        return failed
    return unexpected


class OpenRouterCompletion:
    """A readable chat completion: validated metadata and, for the application only, the text.

    Deliberately not a dataclass, so `dataclasses.asdict(outcome)` cannot expand the text."""

    __slots__ = (
        "_text",
        "id_withheld",
        "response_id",
        "response_model",
        "signals",
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
        usage: TokenUsage | None,
        verdict: OpenRouterVerdict,
        signals: FreeRoutingSignals,
    ) -> None:
        self._text = text
        self.response_id = response_id
        self.id_withheld = id_withheld
        self.response_model = response_model
        self.usage = usage
        self.verdict = verdict
        self.signals = signals

    @property
    def text(self) -> str | None:
        return self._text

    @property
    def service_tier(self) -> None:
        return None  # OpenRouter reports none; kept for the provider-neutral outcome

    def __repr__(self) -> str:
        return f"OpenRouterCompletion(status={self.verdict.status!r})"


def _error_reason(body: object) -> tuple[int | None, str | None]:
    """The `error.code` (an HTTP-style integer) and a validated `error.metadata.error_type`. The
    message and every other metadata field are never read."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, None
    raw_code = error.get("code")
    code = raw_code if type(raw_code) is int and 100 <= raw_code <= 599 else None
    metadata = error.get("metadata")
    error_type = _keyword(metadata.get("error_type")) if isinstance(metadata, dict) else None
    return code, error_type


def status_category(status: int) -> str:
    """Provider-neutral category of a non-200 status (or a 200 body's `error.code`)."""
    if status in _STATUS_CATEGORIES:
        return _STATUS_CATEGORIES[status]
    if status >= 500:
        return "provider_error"
    return "http_error"


def _reason_text(code: int | None, error_type: str | None) -> str | None:
    if error_type is not None:
        return error_type
    return str(code) if code is not None else None


def _read_choice(choice: object) -> tuple[str | None, object, bool, bool]:
    """(text, finish reason, has tool calls, carries an error) of the single choice.
    `message.reasoning` and `native_finish_reason` are never read."""
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
    return content, finish, bool(message.get("tool_calls")), choice.get("error") is not None


def parse_completion(raw: bytes, requested_model: str) -> OpenRouterCompletion:
    """Parse a 200 body. Raises `OpenRouterCallError` for an error object or an unusable shape."""
    body = groq_adapter._decode(raw)
    if not isinstance(body, dict):
        raise _malformed()
    if body.get("error") is not None:
        code, error_type = _error_reason(body)
        raise OpenRouterCallError(
            "openrouter.response.error",
            status_category(code) if code is not None and code != 200 else "provider_error",
            http_status=200,
            provider_reason=_reason_text(code, error_type),
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

    text, finish, tool_calls, choice_error = (
        _read_choice(choices[0]) if choices else (None, None, False, False)
    )
    verdict = assess_choice(
        has_choice=bool(choices),
        finish=finish,
        text=text,
        tool_calls=tool_calls,
        choice_error=choice_error,
    )
    if response_model != requested_model:
        verdict = OpenRouterVerdict(
            "failed",
            verdict.reason,
            "unexpected_status",
            "openrouter.response.model_mismatch",
            verdict.finish_reason,
        )

    reported_id = body.get("id")
    response_id = _guards.check(reported_id, _guards.RESPONSE_ID)
    return OpenRouterCompletion(
        text=text,
        response_id=response_id,
        id_withheld=isinstance(reported_id, str) and response_id is None,
        response_model=response_model,
        usage=usage_from_openrouter_body(body).usage,
        verdict=verdict,
        signals=routing_signals(body),
    )


def _read_limited(response: httpx.Response, limit: int, deadline: float) -> bytes:
    """The response body, or `OpenRouterCallError`/`httpx.ReadTimeout` before reading past
    `limit` bytes or `deadline`. A compressed body is refused, so `limit` is the real size."""
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise _malformed()
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isdecimal() or int(declared) > limit):
        raise OpenRouterCallError("openrouter.response.oversized", "malformed_response")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > limit:
            raise OpenRouterCallError("openrouter.response.oversized", "malformed_response")
        if time.monotonic() > deadline:
            raise httpx.ReadTimeout("the response deadline passed")
        chunks.append(chunk)
    return b"".join(chunks)


def _http_failure(response: httpx.Response, deadline: float) -> OpenRouterCallError:
    """A non-200 response as an `OpenRouterCallError`. An unreadable body leaves the status."""
    try:
        body = groq_adapter._decode(_read_limited(response, MAX_ERROR_BODY_BYTES, deadline))
    except (OpenRouterCallError, httpx.HTTPError):
        body = groq_adapter._UNREADABLE
    _, error_type = _error_reason(body)
    status = response.status_code
    return OpenRouterCallError(
        str(status),
        status_category(status),
        http_status=status,
        provider_reason=error_type,
    )


# ---------------------------------------------------------------- HTTP client boundary
def build_openrouter_http_client() -> httpx.Client:
    """The only client shape accepted for a real request (the Groq boundary's shape, ADR-43)."""
    context = ssl.create_default_context(cafile=certifi.where())
    return httpx.Client(
        verify=context,
        trust_env=False,
        follow_redirects=False,
        timeout=OPENROUTER_TIMEOUT,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )


def live_openrouter_client_problems(http: object) -> list[str]:
    """Every reason `http` may not carry a real OpenRouter key; empty when it may. The library,
    routing, transport and pool checks are the audited Groq ones, unchanged."""
    problems = groq_adapter.groq_library_problems()
    if type(http) is not httpx.Client:
        return [*problems, "the HTTP client must be exactly an httpx.Client"]
    problems.extend(groq_adapter._routing_problems(http))
    if groq_adapter._attr(http, "timeout") != OPENROUTER_TIMEOUT:
        problems.append("the HTTP client must use the bounded OpenRouter timeouts")
    problems.extend(groq_adapter.transport_problems(groq_adapter._attr(http, "_transport")))
    return problems


def check_live_openrouter_client(http: object) -> None:
    problems = live_openrouter_client_problems(http)
    if problems:
        raise ConfigError("live OpenRouter client: " + "; ".join(problems))


def _check_http_client(http: object, provenance: str) -> None:
    if provenance == "real":
        if not REAL_EXECUTION_ENABLED:
            raise ConfigError(
                "provenance: real OpenRouter requests are not enabled in this version"
            )
        check_live_openrouter_client(http)
        return
    if provenance != "synthetic":
        raise ConfigError("provenance: must be 'real' or 'synthetic'")
    if type(http) is not httpx.Client:
        raise ConfigError("http_client: must be exactly an httpx.Client")
    problems = groq_adapter._routing_problems(http)
    if type(groq_adapter._attr(http, "_transport")) is not httpx.MockTransport:
        problems.append("synthetic provenance needs an httpx.MockTransport")
    if problems:
        raise ConfigError("http_client: " + "; ".join(problems))


def _check_send_gate(gate: object, provenance: str) -> None:
    if provenance != "real":
        if gate is not None:
            raise ConfigError("send_gate: applies only to real provenance")
        return
    from llm_insight_sdk.openrouter_live import FreeModelSession  # imports this module

    if type(gate) is not FreeModelSession:
        raise ConfigError("send_gate: a real OpenRouter request needs exactly a FreeModelSession")


def _finish(permit: SendPermit | None, outcome: object) -> None:
    if permit is None:
        return
    try:
        permit.finish(outcome)
    except Exception:  # an unfinished permit keeps the session closed, which is the safe state
        logger.debug("llm_insight_sdk: settling the OpenRouter send failed", exc_info=False)


def _note_connect_failure(permit: SendPermit | None, exc: Exception) -> None:
    if permit is not None and isinstance(exc, groq_adapter._NOT_TRANSMITTED_ERRORS):
        permit.connection_not_established()


# ---------------------------------------------------------------- the instrumented client
def is_free_model_id(model: object) -> bool:
    """An explicit `org/name:free` model ID that the telemetry rules accept, and not a router."""
    return (
        isinstance(model, str)
        and MODEL_ID.fullmatch(model) is not None
        and model.split("/", 1)[0] not in _ROUTER_ORGS
        and _guards.check(model, _guards.MODEL) is not None
    )


def _refuse_unsupported(
    model: object, input_text: object, max_tokens: object, unsupported: Mapping[str, Any]
) -> None:
    for name in sorted(unsupported):
        raise UnsupportedRequestError(f"{name} is not supported by the OpenRouter adapter")
    if not is_free_model_id(model):
        raise UnsupportedRequestError(
            "model must be one explicit 'org/name:free' OpenRouter model ID (no router)"
        )
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
            f"max_tokens must be an integer from 1 to {MAX_OUTPUT_TOKENS}"
        )


def request_body(model: str, input_text: str, max_tokens: int) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": input_text}],
        "max_tokens": max_tokens,
        "stream": False,
        "reasoning": {"exclude": True},
        "provider": json.loads(json.dumps(PROVIDER_ROUTING)),
    }


def _encode_body(model: str, input_text: str, max_tokens: int) -> bytes:
    body = request_body(model, input_text, max_tokens)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _record_response(recorder: InferenceRecorder | None, completion: OpenRouterCompletion) -> None:
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
            recorder.set_metadata("gen_ai.response.id", "")
        if completion.verdict.span_error is not None:
            recorder.mark_failed(completion.verdict.span_error)
    except Exception:  # telemetry must never break the call
        logger.debug("llm_insight_sdk: recording the OpenRouter response failed", exc_info=False)


class ObservedOpenRouterClient:
    """Instrumented OpenRouter chat completion: exactly one physical request per call.

    The endpoint is a constant, `http_client` is required and re-checked immediately before every
    send, and the API key is held only here and added to each request. Real provenance also
    needs `send_gate`, which authorizes each send once the request is built."""

    def __init__(
        self,
        telemetry: Telemetry,
        api_key: str,
        *,
        http_client: httpx.Client,
        provenance: Provenance,
        send_gate: SendGate | None = None,
    ) -> None:
        _check_http_client(http_client, provenance)
        _check_send_gate(send_gate, provenance)
        if not isinstance(api_key, str) or not groq_adapter._API_KEY.fullmatch(api_key):
            raise ConfigError("api_key: must be a non-empty string of visible ASCII characters")
        self._telemetry = telemetry
        self._api_key = api_key
        self._http = http_client
        self._provenance: Provenance = provenance
        self._send_gate = send_gate

    def __repr__(self) -> str:
        return f"ObservedOpenRouterClient(provenance={self._provenance!r})"

    @contextmanager
    def _attempt_span(
        self,
        model: str,
        call_id: str,
        prompt_name: str | None,
        prompt_version: str | None,
        max_tokens: int,
    ) -> Iterator[InferenceRecorder | None]:
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
            logger.debug("llm_insight_sdk: starting the OpenRouter span failed", exc_info=False)
            yield None
            return
        recorder.set_metadata("server.address", OPENROUTER_HOST)
        recorder.set_metadata("server.port", OPENROUTER_PORT)
        try:
            yield recorder
        except BaseException as exc:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)  # records ERROR, re-raises exc
            except BaseException as raised:
                if raised is not exc:
                    logger.debug(
                        "llm_insight_sdk: ending the OpenRouter span failed", exc_info=False
                    )
            raise
        else:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                logger.debug("llm_insight_sdk: ending the OpenRouter span failed", exc_info=False)

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
            timeout=OPENROUTER_TIMEOUT,
        )
        names = {name.lower() for name in request.headers}
        if (
            str(request.url) != ENDPOINT_URL
            or request.method != "POST"
            or names != groq_adapter._SENT_HEADERS
        ):
            raise ConfigError("http_client: the request is not the pinned OpenRouter request")
        return request

    def _authorize_send(self, request: httpx.Request) -> SendPermit | None:
        _check_send_gate(self._send_gate, self._provenance)
        if self._send_gate is None:
            return None
        return self._send_gate.authorize_send(self._http, request)

    def _exchange(
        self, request: httpx.Request, requested_model: str, permit: SendPermit | None
    ) -> OpenRouterCompletion:
        deadline = time.monotonic() + TOTAL_DEADLINE_SECONDS
        failure: OpenRouterCallError | None = None
        raw = b""
        try:
            if permit is not None:
                permit.transmission_starting()
            response = self._http.send(request, stream=True, follow_redirects=False, auth=None)
            if permit is not None:
                permit.response_received()
            try:
                if response.status_code != 200:
                    failure = _http_failure(response, deadline)
                elif not groq_adapter._has_json_content_type(response):
                    failure = _malformed()
                else:
                    raw = _read_limited(response, MAX_RESPONSE_BYTES, deadline)
            finally:
                response.close()
        except httpx.TimeoutException as exc:
            _note_connect_failure(permit, exc)
            failure = OpenRouterCallError("timeout", "timeout")
        except httpx.TransportError as exc:
            _note_connect_failure(permit, exc)
            failure = OpenRouterCallError(f"httpx.{type(exc).__name__}", "connection_error")
        except httpx.HTTPError as exc:
            failure = OpenRouterCallError(f"httpx.{type(exc).__name__}", "unexpected_error")
        if failure is not None:
            raise failure
        return parse_completion(raw, requested_model)

    def create_chat_completion(
        self,
        *,
        model: str,
        input_text: str,
        max_tokens: int,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        attempt_observer: AttemptObserver | None = None,
        **unsupported: Any,
    ) -> OpenRouterCompletion:
        """Exactly one physical request. `input_text` and the output are never recorded, and
        `attempt_observer(1, completion_or_exception)` is called once; its errors are ignored."""
        _refuse_unsupported(model, input_text, max_tokens, unsupported)
        _check_http_client(self._http, self._provenance)  # the client that actually sends
        request = self._build_request(_encode_body(model, input_text, max_tokens))
        permit = self._authorize_send(request)
        try:
            with self._attempt_span(
                model, new_call_id(), prompt_name, prompt_version, max_tokens
            ) as recorder:
                try:
                    completion = self._exchange(request, model, permit)
                except BaseException as exc:
                    groq_adapter._notify(attempt_observer, 1, exc)
                    raise
                _record_response(recorder, completion)
                groq_adapter._notify(attempt_observer, 1, completion)
        except BaseException as exc:
            _finish(permit, exc)
            raise
        _finish(permit, completion)
        return completion


class OpenRouterTextAdapter:
    """`TextGenerationAdapter` over `ObservedOpenRouterClient`: one non-streaming text request,
    one physical attempt, a metadata-only outcome, and provider failures returned as an outcome."""

    provider = PROVIDER

    def __init__(self, client: ObservedOpenRouterClient) -> None:
        self._client = client

    def generate_text(self, request: TextRequest) -> TextOutcome:
        records: list[AttemptRecord] = []

        def observe(attempt: int, outcome: object) -> None:
            if isinstance(outcome, OpenRouterCompletion):
                records.append(
                    AttemptRecord(
                        attempt,
                        usage=outcome.usage,
                        response_model=outcome.response_model,
                        error_category=outcome.verdict.category,
                    )
                )
                return
            category = outcome.category if isinstance(outcome, OpenRouterCallError) else None
            records.append(AttemptRecord(attempt, error_category=category or "unexpected_error"))

        started = time.monotonic()
        try:
            completion = self._client.create_chat_completion(
                model=request.model,
                input_text=request.input_text,
                max_tokens=request.max_output_tokens,
                prompt_name=request.prompt_name,
                prompt_version=request.prompt_version,
                attempt_observer=observe,
            )
        except (UnsupportedRequestError, ConfigError, LiveRefusal):
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
                provider_reason=(
                    exc.provider_reason if isinstance(exc, OpenRouterCallError) else None
                ),
            )
        verdict = completion.verdict
        return TextOutcome(
            provider=PROVIDER,
            status=verdict.status,
            request_model=request.model,
            response_model=completion.response_model,
            response_id=completion.response_id,
            service_tier=None,
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
