"""OpenAI Chat Completions (Phase 2B) and Responses API (Phase 6A) adapter: one CLIENT
span per physical attempt.

    import openai
    import llm_insight_sdk as lli
    from llm_insight_sdk.openai_adapter import ObservedOpenAIClient

    telemetry = lli.init(lli.LLMInsightConfig.from_env())
    llm = ObservedOpenAIClient(telemetry, openai.OpenAI(), provenance="real")
    completion = llm.create_chat_completion(
        model="gpt-5.4-mini", messages=[...], service_tier="default", prompt_version="v1"
    )

Needs the optional dependency: `pip install "llm-insight-sdk[openai]"` (openai 3.28.0).

**Surface.** Only `client.chat.completions.create(...)` without streaming (the
`openai.api.type = chat_completions` API of the OpenAI semconv). Streaming, the Responses
API, Azure OpenAI and other base URLs are refused before any request is sent.

**What is recorded** (metadata only, ADR-19): provider `openai`, operation `chat`, request
and response model, `gen_ai.response.id` (only from a returned completion), finish reasons,
the provider-reported usage object, request/response service tier, system fingerprint,
`server.address`/`server.port`, numeric request settings, prompt name/version, call ID and
attempt. Never: messages, instructions, tools, completion text, the API key, headers or
any exception message. Token counts are never estimated: a field the provider did not
report stays absent.

Usage mapping (OpenAI's own Chat Completions field names, which are not the legacy semconv
aliases): `usage.prompt_tokens` → `gen_ai.usage.input_tokens`,
`usage.completion_tokens` → `gen_ai.usage.output_tokens`,
`prompt_tokens_details.cached_tokens` → `gen_ai.usage.cache_read.input_tokens`,
`prompt_tokens_details.cache_write_tokens` → `gen_ai.usage.cache_write.input_tokens`,
`completion_tokens_details.reasoning_tokens` → `gen_ai.usage.reasoning.output_tokens`.

**Attempts.** The wrapped client's own retries are switched off (`max_retries=0`), because
they would hide several billable requests inside one span. The adapter retries instead,
one span per request, sharing a fresh UUIDv4 `llminsight.call_id`. Retried: connection
errors and timeouts, 408, 409, 429 and 5xx (the OpenAI SDK's own rule). `Retry-After` is
not honoured; the back-off is linear.

**Errors.** A provider error reaches the caller unchanged after the last attempt; the span
gets status `ERROR` and `error.type` (`timeout`, the HTTP status, or `openai.<Class>`).
Telemetry problems never reach the caller (fail-open).

**Provenance** must be declared: `real` for a client that talks to OpenAI, `synthetic` for
injected fake transports and fixtures. `real` with an `httpx2.MockTransport` is refused.

**Cost.** The backend prices only calls the response reports as `service_tier=default` on
`api.openai.com` (docs/PRICING.md §3). The response reports the tier reliably only when the
request sets it, so pass `service_tier="default"` when you want a computed cost.

**Responses API (Phase 6A, ADR-28).** `create_response(...)` instruments
`client.responses.create(...)` for non-streaming text requests: operation `chat` (the
pinned semconv's applicable predefined value) with `openai.api.type = responses`, so the
backend contract (`llminsight.attrmap.v1`) is unchanged. Usage mapping (the Responses
field names): `usage.input_tokens` → `gen_ai.usage.input_tokens`, `usage.output_tokens` →
`gen_ai.usage.output_tokens`, `input_tokens_details.cached_tokens` → `cache_read`,
`input_tokens_details.cache_write_tokens` → `cache_write`,
`output_tokens_details.reasoning_tokens` → `reasoning`. `max_output_tokens` →
`gen_ai.request.max_tokens`. An `incomplete` response records its
`incomplete_details.reason` (OpenAI's Responses vocabulary, e.g. `max_output_tokens`) as
the finish reason; a `completed` response records none (the API reports no per-output
reason). A `failed` or unexpected status, or a body that is not a Response, ends the span
with status ERROR (`openai.response.failed`, `openai.response.unexpected_status`,
`openai.response.malformed`) and is still returned unchanged. Every field is type-checked:
`openai` 3.28.0 does not validate response bodies.

Refused before sending: `stream`, `stream_options`, `background`, `conversation`,
`previous_response_id`, `tools`, `tool_choice`, `store` other than False, and (Phase 6B)
every argument outside `RESPONSES_ALLOWED` (`extra_body`, `prompt`, `moderation`, ...).
`store=False` is always sent. Tool calls are refused because built-in tool charges are not
representable in the price catalogue (docs/PRICING.md §3), so a token-only cost would
understate them.

Retries differ from Chat Completions: one attempt by default, and only an **unambiguous**
failure is retried (`is_unambiguously_retryable`: a 429 that is not `insufficient_quota`).
A timeout, connection error or 5xx may have been processed and billed, so it is never
retried automatically (docs/PHASE6_LIVE_INTEGRATION_PLAN.md §H).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Literal, cast

import httpx2
import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.responses import Response

from llm_insight_sdk import instrumentation
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.instrumentation import (
    InferenceRecorder,
    RequestParams,
    TokenUsage,
    acall_with_retries,
    call_with_retries,
    classify_error,
    new_call_id,
)
from llm_insight_sdk.providers import AttemptRecord, OutcomeStatus, TextOutcome, TextRequest
from llm_insight_sdk.telemetry import Telemetry

logger = logging.getLogger("llm_insight_sdk")

PROVIDER = "openai"
OPERATION = "chat"
API_TYPE = "chat_completions"
RESPONSES_API_TYPE = "responses"
RESPONSES_DEFAULT_MAX_ATTEMPTS = 1
# Response.status values of a finished, non-background response (openai 3.28.0 types).
RESPONSE_STATUSES = frozenset({"completed", "incomplete", "failed"})
RESPONSES_REFUSED = (
    "stream",
    "stream_options",
    "background",
    "conversation",
    "previous_response_id",
    "tools",
    "tool_choice",
)
# Phase 6B: an allowlist, not only the refusals above. `extra_body` could carry `tools`,
# `prompt` a stored template (model, tools), `moderation` a second model, and
# `context_management` or `access_programs` change processing; none of these charges is in
# the catalogue. Any argument outside this set is refused before sending.
RESPONSES_ALLOWED = frozenset(
    {
        "instructions",
        "max_output_tokens",
        "metadata",
        "prompt_cache_key",
        "reasoning",
        "safety_identifier",
        "service_tier",
        "store",
        "temperature",
        "text",
        "timeout",
        "top_p",
        "truncation",
        "user",
    }
)
OPENAI_HOST = "api.openai.com"
RETRYABLE_STATUS = frozenset({408, 409, 429})
DEFAULT_MAX_ATTEMPTS = 3  # the OpenAI SDK default: 1 request + 2 retries
DEFAULT_BACKOFF_SECONDS = 0.5

Provenance = Literal["real", "synthetic"]


class UnsupportedRequestError(ValueError):
    """The request uses a feature the adapter does not instrument. Nothing was sent."""


def classify_openai_error(exc: BaseException) -> str:
    """semconv `error.type` for an OpenAI failure. Never the message or the request ID."""
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.APIStatusError):
        return str(exc.status_code)
    if isinstance(exc, openai.OpenAIError):
        return f"openai.{type(exc).__name__}"
    return classify_error(exc)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in RETRYABLE_STATUS or exc.status_code >= 500
    return False


def usage_from(completion: ChatCompletion) -> TokenUsage | None:
    """The provider-reported usage, or None when the response has no usage object."""
    u = completion.usage
    if u is None:
        return None
    prompt, output = u.prompt_tokens_details, u.completion_tokens_details
    return TokenUsage(
        input_tokens=u.prompt_tokens,
        output_tokens=u.completion_tokens,
        cache_read_input_tokens=prompt.cached_tokens if prompt is not None else None,
        cache_write_input_tokens=prompt.cache_write_tokens if prompt is not None else None,
        reasoning_output_tokens=output.reasoning_tokens if output is not None else None,
        source="provider_reported",
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def request_params(kwargs: Mapping[str, Any]) -> RequestParams:
    """Numeric request settings only; `max_completion_tokens` wins over `max_tokens`."""
    max_tokens = kwargs.get("max_completion_tokens", kwargs.get("max_tokens"))
    return RequestParams(
        max_tokens=_integer(max_tokens),
        temperature=_number(kwargs.get("temperature")),
        top_p=_number(kwargs.get("top_p")),
        seed=_integer(kwargs.get("seed")),
    )


def _output_type(response_format: object) -> str | None:
    kind = response_format.get("type") if isinstance(response_format, Mapping) else None
    if kind in ("json_object", "json_schema"):
        return "json"
    return "text" if kind == "text" else None


def _check_client(client: object, expected: type[Any], azure: type[Any], provenance: str) -> None:
    if not isinstance(client, expected) or isinstance(client, azure):
        raise ConfigError(f"client: must be an {expected.__name__} instance (not Azure)")
    url = cast(httpx2.URL, client.base_url)
    if url.scheme != "https" or url.host != OPENAI_HOST or url.port not in (None, 443):
        raise ConfigError(f"client: only https://{OPENAI_HOST} is supported (base_url refused)")
    if url.userinfo:
        raise ConfigError("client: base_url must not contain user info")
    if provenance not in ("real", "synthetic"):
        raise ConfigError("provenance: must be 'real' or 'synthetic'")
    transport = getattr(getattr(client, "_client", None), "_transport", None)
    if provenance == "real" and isinstance(transport, httpx2.MockTransport):
        raise ConfigError("provenance: 'real' is refused for a client with a mock transport")


def _refuse_unsupported(kwargs: Mapping[str, Any]) -> None:
    if kwargs.get("stream"):
        raise UnsupportedRequestError("stream=True is not supported by the OpenAI adapter")
    if "stream_options" in kwargs:
        raise UnsupportedRequestError("stream_options is not supported by the OpenAI adapter")


# ---------------------------------------------------------------- Responses API (Phase 6A)
def is_unambiguously_retryable(exc: BaseException) -> bool:
    """True only for a failure that was rejected before processing: a 429 rate limit that
    is not `insufficient_quota` (retrying an exhausted quota cannot succeed). Timeouts,
    connection errors and 5xx are ambiguous (the request may have been processed and
    billed) and are never retried automatically."""
    return isinstance(exc, openai.RateLimitError) and exc.code != "insufficient_quota"


_STATUS_CATEGORIES: dict[type[openai.APIStatusError], str] = {
    openai.AuthenticationError: "authentication_failed",
    openai.PermissionDeniedError: "permission_denied",
    openai.NotFoundError: "not_found",
    openai.BadRequestError: "invalid_request",
    openai.UnprocessableEntityError: "invalid_request",
    openai.ConflictError: "invalid_request",
}


def error_category(exc: BaseException) -> str:
    """Provider-neutral category of a failed attempt (`providers.ERROR_CATEGORIES`). Reads
    only the exception class, the HTTP status and OpenAI's error `code`; never a message."""
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "connection_error"
    if isinstance(exc, openai.RateLimitError):
        return "quota_exhausted" if exc.code == "insufficient_quota" else "rate_limited"
    if isinstance(exc, openai.APIStatusError):
        for cls, category in _STATUS_CATEGORIES.items():
            if isinstance(exc, cls):
                return category
        return "provider_error" if exc.status_code >= 500 else "http_error"
    if isinstance(exc, openai.APIResponseValidationError | ValueError):
        return "malformed_response"  # includes a body that is not JSON (JSONDecodeError)
    return "unexpected_error"


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage_from_response(response: object) -> TokenUsage | None:
    """The provider-reported usage of a Responses API response, or None when there is no
    usable usage object (absent, or totals of the wrong type). Never estimated."""
    u = getattr(response, "usage", None)
    if u is None:
        return None
    input_tokens = _count(getattr(u, "input_tokens", None))
    output_tokens = _count(getattr(u, "output_tokens", None))
    if input_tokens is None or output_tokens is None:
        return None
    details_in = getattr(u, "input_tokens_details", None)
    details_out = getattr(u, "output_tokens_details", None)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=_count(getattr(details_in, "cached_tokens", None)),
        cache_write_input_tokens=_count(getattr(details_in, "cache_write_tokens", None)),
        reasoning_output_tokens=_count(getattr(details_out, "reasoning_tokens", None)),
        source="provider_reported",
    )


def responses_request_params(kwargs: Mapping[str, Any]) -> RequestParams:
    """Numeric Responses request settings only (`max_output_tokens`; no seed in this API)."""
    return RequestParams(
        max_tokens=_integer(kwargs.get("max_output_tokens")),
        temperature=_number(kwargs.get("temperature")),
        top_p=_number(kwargs.get("top_p")),
    )


def _refuse_unsupported_responses(kwargs: Mapping[str, Any]) -> None:
    for name in kwargs:
        if name in RESPONSES_REFUSED or name not in RESPONSES_ALLOWED:
            raise UnsupportedRequestError(f"{name} is not supported by the Responses adapter")
    if "store" in kwargs and kwargs["store"] is not False:
        raise UnsupportedRequestError("store must be False: the adapter never stores responses")


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def response_status(response: object) -> OutcomeStatus:
    """`completed` / `incomplete` / `failed`, or `malformed` for anything that is not a
    Response with a string status. Any other status of a finished non-background call
    counts as `failed` here; its span records `openai.response.unexpected_status`."""
    if not isinstance(response, Response):
        return "malformed"
    status = response.status
    if not isinstance(status, str):
        return "malformed"
    if status in ("completed", "incomplete"):
        return "completed" if status == "completed" else "incomplete"
    return "failed"


def _record_response(recorder: InferenceRecorder | None, response: object) -> None:
    if recorder is None:
        return
    try:
        status = response_status(response)
        if status == "malformed":
            recorder.mark_failed("openai.response.malformed")
            return
        reason = _text(getattr(getattr(response, "incomplete_details", None), "reason", None))
        recorder.record_response(
            response_model=_text(getattr(response, "model", None)),
            response_id=_text(getattr(response, "id", None)),
            finish_reasons=[reason] if status == "incomplete" and reason is not None else [],
            usage=usage_from_response(response),
        )
        tier = _text(getattr(response, "service_tier", None))
        if tier is not None:
            recorder.set_metadata("openai.response.service_tier", tier)
        if status == "failed":
            known = getattr(response, "status", None) in RESPONSE_STATUSES
            recorder.mark_failed(
                "openai.response.failed" if known else "openai.response.unexpected_status"
            )
    except Exception:  # telemetry must never break the call
        logger.debug("llm_insight_sdk: recording the OpenAI response failed", exc_info=False)


AttemptObserver = Callable[[int, object], None]


def _notify(observer: AttemptObserver | None, attempt: int, outcome: object) -> None:
    if observer is None:
        return
    try:
        observer(attempt, outcome)
    except Exception:  # an observer must not change the call's outcome
        logger.debug("llm_insight_sdk: an attempt observer raised", exc_info=False)


class _ObservedBase:
    def __init__(
        self,
        telemetry: Telemetry,
        *,
        provenance: Provenance,
        max_attempts: int,
        backoff_seconds: float,
    ) -> None:
        instrumentation._check_attempts(max_attempts)
        if backoff_seconds < 0:
            raise ConfigError("backoff_seconds: must be >= 0")
        self._telemetry = telemetry
        self._provenance: Provenance = provenance
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds

    @contextmanager
    def _attempt_span(
        self,
        model: str,
        call_id: str,
        attempt: int,
        prompt_name: str | None,
        prompt_version: str | None,
        kwargs: Mapping[str, Any],
        api_type: str = API_TYPE,
    ) -> Iterator[InferenceRecorder | None]:
        """The attempt's span, or None if telemetry itself fails: the request still runs and
        its outcome (result or provider error) is never changed by telemetry."""
        params = (
            responses_request_params(kwargs)
            if api_type == RESPONSES_API_TYPE
            else request_params(kwargs)
        )
        try:
            cm: AbstractContextManager[InferenceRecorder] = instrumentation.inference_span(
                self._telemetry.tracer,
                on_withheld=self._telemetry._withheld,
                provenance=self._provenance,
                operation=OPERATION,
                provider=PROVIDER,
                request_model=model,
                call_id=call_id,
                attempt=attempt,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                params=params,
                classify=classify_openai_error,
            )
            recorder = cm.__enter__()
        except Exception:
            logger.debug("llm_insight_sdk: starting the OpenAI span failed", exc_info=False)
            yield None
            return
        _record_request(recorder, kwargs, api_type)
        try:
            yield recorder
        except BaseException as exc:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)  # records ERROR, re-raises exc
            except BaseException as raised:
                if raised is not exc:
                    logger.debug("llm_insight_sdk: ending the OpenAI span failed", exc_info=False)
            raise
        else:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                logger.debug("llm_insight_sdk: ending the OpenAI span failed", exc_info=False)


def _record_request(
    recorder: InferenceRecorder, kwargs: Mapping[str, Any], api_type: str = API_TYPE
) -> None:
    recorder.set_metadata("openai.api.type", api_type)
    recorder.set_metadata("server.address", OPENAI_HOST)
    recorder.set_metadata("server.port", 443)
    tier = kwargs.get("service_tier")
    if isinstance(tier, str) and tier != "auto":  # semconv: "if set and not auto"
        recorder.set_metadata("openai.request.service_tier", tier)
    if api_type == RESPONSES_API_TYPE:
        text = kwargs.get("text")
        output_type = _output_type(text.get("format") if isinstance(text, Mapping) else None)
    else:
        output_type = _output_type(kwargs.get("response_format"))
    if output_type is not None:
        recorder.set_metadata("gen_ai.output.type", output_type)


def _record_completion(recorder: InferenceRecorder | None, completion: object) -> None:
    if recorder is None or not isinstance(completion, ChatCompletion):
        return
    try:
        recorder.record_response(
            response_model=completion.model,
            response_id=completion.id,
            finish_reasons=[c.finish_reason for c in completion.choices if c.finish_reason],
            usage=usage_from(completion),
        )
        if completion.service_tier is not None:
            recorder.set_metadata("openai.response.service_tier", completion.service_tier)
        if completion.system_fingerprint is not None:
            recorder.set_metadata(
                "openai.response.system_fingerprint", completion.system_fingerprint
            )
    except Exception:  # telemetry must never break the call
        logger.debug("llm_insight_sdk: recording the OpenAI response failed", exc_info=False)


class ObservedOpenAIClient(_ObservedBase):
    """Instrumented `openai.OpenAI` Chat Completions calls (sync)."""

    def __init__(
        self,
        telemetry: Telemetry,
        client: openai.OpenAI,
        *,
        provenance: Provenance,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        _check_client(client, openai.OpenAI, openai.AzureOpenAI, provenance)
        super().__init__(
            telemetry,
            provenance=provenance,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )
        self._client = client.with_options(max_retries=0)  # one span per physical request

    def create_chat_completion(
        self,
        *,
        model: str,
        messages: Iterable[ChatCompletionMessageParam],
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        max_attempts: int | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """`client.chat.completions.create(model=..., messages=..., **kwargs)` as one logical
        call. `messages` and every other argument are passed through and never recorded."""
        _refuse_unsupported(kwargs)
        call_id = new_call_id()
        create: Callable[..., object] = self._client.chat.completions.create

        def attempt_fn(attempt: int) -> ChatCompletion:
            with self._attempt_span(
                model, call_id, attempt, prompt_name, prompt_version, kwargs
            ) as recorder:
                completion = create(model=model, messages=messages, **kwargs)
                _record_completion(recorder, completion)
                return cast(ChatCompletion, completion)

        return call_with_retries(
            attempt_fn,
            max_attempts=max_attempts or self._max_attempts,
            is_retryable=is_retryable,
            backoff_seconds=self._backoff_seconds,
        )

    def create_response(
        self,
        *,
        model: str,
        input: Any,  # the OpenAI parameter name
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        max_attempts: int = RESPONSES_DEFAULT_MAX_ATTEMPTS,
        retry_if: Callable[[BaseException], bool] = is_unambiguously_retryable,
        attempt_observer: AttemptObserver | None = None,
        **kwargs: Any,
    ) -> Response:
        """`client.responses.create(model=..., input=..., store=False, **kwargs)`, non-streaming,
        as one logical call. `input` and every other argument are passed through and never
        recorded. `attempt_observer(attempt, response_or_exception)` is called after every
        physical attempt (before any retry decision); its errors are ignored."""
        _refuse_unsupported_responses(kwargs)
        kwargs["store"] = False
        call_id = new_call_id()
        create: Callable[..., object] = self._client.responses.create

        def attempt_fn(attempt: int) -> Response:
            with self._attempt_span(
                model, call_id, attempt, prompt_name, prompt_version, kwargs, RESPONSES_API_TYPE
            ) as recorder:
                try:
                    response = create(model=model, input=input, **kwargs)
                except BaseException as exc:
                    _notify(attempt_observer, attempt, exc)
                    raise
                _record_response(recorder, response)
                _notify(attempt_observer, attempt, response)
                return cast(Response, response)

        return call_with_retries(
            attempt_fn,
            max_attempts=max_attempts,
            is_retryable=retry_if,
            backoff_seconds=self._backoff_seconds,
        )


class OpenAIResponsesTextAdapter:
    """`TextGenerationAdapter` over `ObservedOpenAIClient.create_response` (ADR-28): one
    non-streaming text request, metadata-only outcome, provider failures returned as an
    outcome instead of raised. The request always sets `store=False` and
    `service_tier="default"` (the only tier the catalogue prices, docs/PRICING.md §3)."""

    provider = PROVIDER

    def __init__(
        self,
        client: ObservedOpenAIClient,
        *,
        max_attempts: int = RESPONSES_DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        instrumentation._check_attempts(max_attempts)
        self._client = client
        self._max_attempts = max_attempts

    def generate_text(self, request: TextRequest) -> TextOutcome:
        records: list[AttemptRecord] = []

        def observe(attempt: int, outcome: object) -> None:
            if isinstance(outcome, BaseException):
                records.append(AttemptRecord(attempt, error_category=error_category(outcome)))
                return
            status = response_status(outcome)
            records.append(
                AttemptRecord(
                    attempt,
                    usage=None if status == "malformed" else usage_from_response(outcome),
                    response_model=_text(getattr(outcome, "model", None)),
                    service_tier=_text(getattr(outcome, "service_tier", None)),
                    error_category=_status_category(status, outcome),
                )
            )

        started = time.monotonic()
        response: object = None
        try:
            response = self._client.create_response(
                model=request.model,
                input=request.input_text,
                max_output_tokens=request.max_output_tokens,
                service_tier="default",
                prompt_name=request.prompt_name,
                prompt_version=request.prompt_version,
                max_attempts=self._max_attempts,
                attempt_observer=observe,
            )
        except UnsupportedRequestError:
            raise
        except Exception as exc:
            category = records[-1].error_category if records else error_category(exc)
            return self._outcome(request, "error", None, records, category, started)
        status = response_status(response)
        return self._outcome(
            request, status, response, records, _status_category(status, response), started
        )

    def _outcome(
        self,
        request: TextRequest,
        status: OutcomeStatus,
        response: object,
        records: list[AttemptRecord],
        category: str | None,
        started: float,
    ) -> TextOutcome:
        readable = status != "malformed" and response is not None
        return TextOutcome(
            provider=PROVIDER,
            status=status,
            request_model=request.model,
            response_model=_text(getattr(response, "model", None)) if readable else None,
            response_id=_text(getattr(response, "id", None)) if readable else None,
            service_tier=_text(getattr(response, "service_tier", None)) if readable else None,
            usage=usage_from_response(response) if readable else None,
            attempts=tuple(records),
            error_category=category,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            provider_response=response,
            incomplete_reason=(
                _text(getattr(getattr(response, "incomplete_details", None), "reason", None))
                if status == "incomplete"
                else None
            ),
        )


def _status_category(status: OutcomeStatus, response: object) -> str | None:
    if status == "malformed":
        return "malformed_response"
    if status == "failed":
        known = getattr(response, "status", None) in RESPONSE_STATUSES
        return "response_failed" if known else "unexpected_status"
    return None


class ObservedAsyncOpenAIClient(_ObservedBase):
    """Instrumented `openai.AsyncOpenAI` Chat Completions calls."""

    def __init__(
        self,
        telemetry: Telemetry,
        client: openai.AsyncOpenAI,
        *,
        provenance: Provenance,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        _check_client(client, openai.AsyncOpenAI, openai.AsyncAzureOpenAI, provenance)
        super().__init__(
            telemetry,
            provenance=provenance,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )
        self._client = client.with_options(max_retries=0)

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: Iterable[ChatCompletionMessageParam],
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        max_attempts: int | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Async variant of `ObservedOpenAIClient.create_chat_completion`; concurrent calls
        keep their own parent context."""
        _refuse_unsupported(kwargs)
        call_id = new_call_id()
        create: Callable[..., Any] = self._client.chat.completions.create

        async def attempt_fn(attempt: int) -> ChatCompletion:
            with self._attempt_span(
                model, call_id, attempt, prompt_name, prompt_version, kwargs
            ) as recorder:
                completion = await create(model=model, messages=messages, **kwargs)
                _record_completion(recorder, completion)
                return cast(ChatCompletion, completion)

        return await acall_with_retries(
            attempt_fn,
            max_attempts=max_attempts or self._max_attempts,
            is_retryable=is_retryable,
            backoff_seconds=self._backoff_seconds,
        )
