"""OpenAI Chat Completions adapter (Phase 2B): one CLIENT span per physical attempt.

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
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Literal, cast

import httpx2
import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

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
from llm_insight_sdk.telemetry import Telemetry

logger = logging.getLogger("llm_insight_sdk")

PROVIDER = "openai"
OPERATION = "chat"
API_TYPE = "chat_completions"
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
    ) -> Iterator[InferenceRecorder | None]:
        """The attempt's span, or None if telemetry itself fails: the request still runs and
        its outcome (result or provider error) is never changed by telemetry."""
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
                params=request_params(kwargs),
                classify=classify_openai_error,
            )
            recorder = cm.__enter__()
        except Exception:
            logger.debug("llm_insight_sdk: starting the OpenAI span failed", exc_info=False)
            yield None
            return
        _record_request(recorder, kwargs)
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


def _record_request(recorder: InferenceRecorder, kwargs: Mapping[str, Any]) -> None:
    recorder.set_metadata("openai.api.type", API_TYPE)
    recorder.set_metadata("server.address", OPENAI_HOST)
    recorder.set_metadata("server.port", 443)
    tier = kwargs.get("service_tier")
    if isinstance(tier, str) and tier != "auto":  # semconv: "if set and not auto"
        recorder.set_metadata("openai.request.service_tier", tier)
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
