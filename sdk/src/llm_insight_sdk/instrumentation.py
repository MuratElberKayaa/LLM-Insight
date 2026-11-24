"""GenAI client inference spans (TELEMETRY_CONTRACT.md §3–4, semconv pin `9ffc7e00c639`).

One span per **physical attempt**: name `{gen_ai.operation.name} {gen_ai.request.model}`,
kind `CLIENT`, a child of whatever span is current. Attempts of one logical call share a
UUIDv4 `llminsight.call_id` and carry `llminsight.attempt` = 1, 2, ...

Metadata only (ADR-19): no message content, system instructions or tool definitions is ever
set, and exceptions are **not** recorded as span events, because their messages and stack
traces can quote prompts. A failed attempt gets status `ERROR` with no description plus a
low-cardinality `error.type`. The application's exception is re-raised unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from opentelemetry.trace.span import Span

from llm_insight_sdk import _guards

logger = logging.getLogger("llm_insight_sdk")


INFERENCE_OPERATIONS = frozenset({"chat", "text_completion", "generate_content", "embeddings"})
# Provider metadata a recorder may set (all allowlisted in llminsight.attrmap.v1), with the
# value rule the backend applies (TELEMETRY_CONTRACT.md §3.1). Anything else is withheld.
METADATA_RULES: dict[str, _guards.Rule] = {
    "gen_ai.output.type": _guards.KEYWORD,
    "openai.api.type": _guards.KEYWORD,
    "openai.request.service_tier": _guards.KEYWORD,
    "openai.response.service_tier": _guards.KEYWORD,
    "openai.response.system_fingerprint": _guards.FINGERPRINT,
    "server.address": _guards.HOST,
}
MAX_PORT = 65_535
MAX_ATTEMPTS = 10
INT32_MAX = 2**31 - 1  # usage_records.attempt_id is INT
MAX_FINISH_REASONS = 16
OTHER_ERROR = "_OTHER"  # semconv fallback for an unclassifiable error
MAX_TTFC_SECONDS = 3600  # the backend's attrmap v2 bound for gen_ai.response.time_to_first_chunk


@dataclass(frozen=True)
class TokenUsage:
    """Provider-reported token counts. A subset count (cache read/write, reasoning) is part of
    its total, never added to it. `None` means "not reported" and is not emitted; an explicit
    0 from the provider is emitted (TELEMETRY_CONTRACT.md §4)."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    source: Literal["provider_reported", "estimated"] = "provider_reported"


@dataclass(frozen=True)
class RequestParams:
    """Numeric request settings that may be recorded. Content never goes here."""

    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    # Phase 10B (ADR-55): emitted as `gen_ai.request.stream` only when True (semconv: "if and
    # only if the request is streaming; if unset, the request is assumed to be non-streaming").
    stream: bool | None = None


def new_call_id() -> str:
    """A fresh UUIDv4 for one logical call (shared by all its attempts)."""
    return str(uuid.uuid4())


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def classify_error(exc: BaseException) -> str:
    """semconv `error.type`: the exception's own `error_type` code if it has a valid one
    (e.g. a provider status `429`, `timeout`), else its fully-qualified class name, else
    `_OTHER`. Never the exception message."""
    explicit = getattr(exc, "error_type", None)
    if isinstance(explicit, str) and _guards.check(explicit, _guards.ERROR_TYPE) == explicit:
        return explicit
    cls = type(exc)
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    name = (
        cls.__qualname__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__qualname__}"
    )
    return name if _guards.check(name, _guards.ERROR_TYPE) == name else OTHER_ERROR


class InferenceRecorder:
    """Handle for the current attempt's span. Never raises into the application."""

    def __init__(
        self,
        span: Span,
        on_withheld: Callable[[str], None],
        classify: Callable[[BaseException], str] = classify_error,
    ) -> None:
        self._span = span
        self._on_withheld = on_withheld
        self._classify = classify
        self._usage_complete = False
        self._failure: str | None = None
        self._started = time.monotonic()  # the attempt's start (Phase 10B, ADR-55)
        self._first_chunk_recorded = False

    def record_first_chunk(self) -> None:
        """Call once, when the first stream event with new, non-empty output (text, reasoning,
        a tool-call name or arguments) has been received, before the application processes it.
        Sets `gen_ai.response.time_to_first_chunk` in seconds from this attempt's start; later
        calls are ignored. Never call it when no chunk arrived (semconv: MUST NOT be set).
        The chunk itself is never recorded."""
        if self._first_chunk_recorded:
            return
        self._first_chunk_recorded = True
        try:
            seconds = time.monotonic() - self._started
            if 0 <= seconds <= MAX_TTFC_SECONDS:
                self._span.set_attribute("gen_ai.response.time_to_first_chunk", seconds)
            else:
                self._on_withheld("gen_ai.response.time_to_first_chunk")
        except Exception:  # telemetry must never break the call
            logger.debug("llm_insight_sdk: recording the first chunk failed", exc_info=False)

    def _set(self, key: str, value: object, rule: _guards.Rule | None = None) -> None:
        if rule is not None:
            value = _guards.check(value, rule)
        if value is None:
            self._on_withheld(key)
            return
        self._span.set_attribute(key, value)  # type: ignore[arg-type]

    def record_response(
        self,
        *,
        response_model: str | None = None,
        response_id: str | None = None,
        finish_reasons: Sequence[str] = (),
        usage: TokenUsage | None = None,
    ) -> None:
        """Response metadata of a successful attempt."""
        try:
            if response_model is not None:
                self._set("gen_ai.response.model", response_model, _guards.MODEL)
            if response_id is not None:
                self._set("gen_ai.response.id", response_id, _guards.RESPONSE_ID)
            if finish_reasons:
                reasons = [_guards.check(r, _guards.KEYWORD) for r in finish_reasons]
                # one bad item withholds the array, as the backend would drop it
                if len(reasons) <= MAX_FINISH_REASONS and all(r is not None for r in reasons):
                    self._span.set_attribute("gen_ai.response.finish_reasons", reasons)
                else:
                    self._on_withheld("gen_ai.response.finish_reasons")
            if usage is not None:
                self._record_usage(usage)
        except Exception:  # telemetry must never break the call
            logger.debug("llm_insight_sdk: recording response metadata failed", exc_info=False)

    def set_metadata(self, key: str, value: str | int) -> None:
        """One provider metadata attribute from `METADATA_RULES` (or `server.port`). Values
        that fail the rule, and keys outside the list, are withheld and counted."""
        try:
            if key == "server.port":
                port = _non_negative_int(value)
                self._set(key, port if port is not None and 1 <= port <= MAX_PORT else None)
            elif key in METADATA_RULES:
                self._set(key, value, METADATA_RULES[key])
            else:
                self._on_withheld(key)
        except Exception:  # telemetry must never break the call
            logger.debug("llm_insight_sdk: recording metadata failed", exc_info=False)

    def mark_failed(self, error_type: str) -> None:
        """The attempt returned a response that reports a failure (no exception reached the
        adapter). The span ends with status `ERROR` and this `error.type`; the response is
        still returned to the application unchanged."""
        self._failure = error_type

    def _record_usage(self, usage: TokenUsage) -> None:
        counters = {
            "gen_ai.usage.input_tokens": usage.input_tokens,
            "gen_ai.usage.output_tokens": usage.output_tokens,
            "gen_ai.usage.cache_read.input_tokens": usage.cache_read_input_tokens,
            "gen_ai.usage.cache_write.input_tokens": usage.cache_write_input_tokens,
            "gen_ai.usage.reasoning.output_tokens": usage.reasoning_output_tokens,
        }
        for key, raw in counters.items():
            if raw is None:
                continue  # not reported by the provider: absent, never 0
            self._set(key, _non_negative_int(raw))
        totals_known = (
            _non_negative_int(usage.input_tokens) is not None
            and _non_negative_int(usage.output_tokens) is not None
        )
        if totals_known and usage.source in ("provider_reported", "estimated"):
            self._usage_complete = True
            self._span.set_attribute("llminsight.usage_source", usage.source)

    def _finish(self, exc: BaseException | None) -> None:
        try:
            if not self._usage_complete:
                self._span.set_attribute("llminsight.usage_source", "unknown")
            error_type = self._classify(exc) if exc is not None else self._failure
            if error_type is not None:
                if _guards.check(error_type, _guards.ERROR_TYPE) != error_type:
                    error_type = OTHER_ERROR
                self._span.set_attribute("error.type", error_type)
                self._span.set_status(Status(StatusCode.ERROR))  # no description (R21)
        except Exception:
            logger.debug("llm_insight_sdk: finishing the inference span failed", exc_info=False)


@contextmanager
def inference_span(
    tracer: Tracer,
    *,
    on_withheld: Callable[[str], None],
    provenance: Literal["real", "synthetic"],
    operation: str,
    provider: str,
    request_model: str | None,
    call_id: str,
    attempt: int,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    params: RequestParams | None = None,
    classify: Callable[[BaseException], str] = classify_error,
) -> Iterator[InferenceRecorder]:
    """Span for one physical attempt. Use `Telemetry.inference_span`, which supplies the
    tracer, the withheld-value counter and the configured provenance (synthetic). Only a
    provider adapter that knows it calls the real provider passes `real` (openai_adapter);
    `classify` maps a failure to `error.type`."""
    attributes: dict[str, str | int | float] = {}
    withheld: list[str] = []

    def put(key: str, value: object, rule: _guards.Rule | None = None) -> None:
        checked = _guards.check(value, rule) if rule is not None else value
        if checked is None:
            withheld.append(key)
        else:
            attributes[key] = checked  # type: ignore[assignment]

    op = operation if operation in INFERENCE_OPERATIONS else None
    put("gen_ai.operation.name", op, _guards.OPERATION)
    put("gen_ai.provider.name", provider, _guards.PROVIDER)
    model = _guards.check(request_model, _guards.MODEL) if request_model is not None else None
    if request_model is not None:
        put("gen_ai.request.model", model)
    put("llminsight.call_id", call_id, _guards.CALL_ID)
    valid_attempt = _non_negative_int(attempt)
    in_range = valid_attempt is not None and 1 <= valid_attempt <= INT32_MAX
    put("llminsight.attempt", valid_attempt if in_range else None)
    if provenance not in ("real", "synthetic"):
        raise ValueError("provenance must be 'real' or 'synthetic'")
    attributes["llminsight.provenance"] = provenance
    if prompt_name is not None:
        put("gen_ai.prompt.name", prompt_name, _guards.PROMPT_NAME)
    if prompt_version is not None:
        put("gen_ai.prompt.version", prompt_version, _guards.PROMPT_VERSION)
    if params is not None:
        if params.max_tokens is not None:
            put("gen_ai.request.max_tokens", _non_negative_int(params.max_tokens))
        for key, num in (
            ("gen_ai.request.temperature", params.temperature),
            ("gen_ai.request.top_p", params.top_p),
        ):
            if num is not None:
                ok = isinstance(num, int | float) and not isinstance(num, bool)
                put(key, float(num) if ok else None)
        if params.seed is not None:
            seed_ok = isinstance(params.seed, int) and not isinstance(params.seed, bool)
            put("gen_ai.request.seed", params.seed if seed_ok else None)
        if params.stream is True:
            attributes["gen_ai.request.stream"] = True
    for key in withheld:
        on_withheld(key)

    # semconv: "{operation} {model}", or the operation alone when the model is unavailable
    name = f"{op} {model}" if op and model else (op or "inference")
    with tracer.start_as_current_span(
        name,
        kind=SpanKind.CLIENT,
        attributes=attributes,
        record_exception=False,  # an exception event would carry message and stacktrace
        set_status_on_exception=False,  # status is set by the recorder, without description
    ) as span:
        recorder = InferenceRecorder(span, on_withheld, classify)
        try:
            yield recorder
        except BaseException as exc:
            recorder._finish(exc)
            raise
        recorder._finish(None)


# ---------------------------------------------------------------- agent and tool steps
AGENT_OPERATIONS = frozenset({"invoke_agent", "create_agent"})


class StepRecorder:
    """Handle of an agent or tool span (Phase 10A, ADR-54). It carries no content: tool
    arguments and results, agent instructions and MCP session IDs have no setter and are
    denied by the backend anyway."""

    def __init__(self, span: Span, classify: Callable[[BaseException], str]) -> None:
        self._span = span
        self._classify = classify
        self._failure: str | None = None

    def mark_failed(self, error_type: str) -> None:
        """The step returned normally but failed (for example a tool reported an error)."""
        self._failure = error_type

    def _finish(self, exc: BaseException | None) -> None:
        try:
            error_type = self._classify(exc) if exc is not None else self._failure
            if error_type is not None:
                if _guards.check(error_type, _guards.ERROR_TYPE) != error_type:
                    error_type = OTHER_ERROR
                self._span.set_attribute("error.type", error_type)
                self._span.set_status(Status(StatusCode.ERROR))  # no description (R21)
        except Exception:
            logger.debug("llm_insight_sdk: finishing a step span failed", exc_info=False)


@contextmanager
def _step_span(
    tracer: Tracer,
    name: str,
    attributes: dict[str, str | int | float],
    classify: Callable[[BaseException], str],
) -> Iterator[StepRecorder]:
    with tracer.start_as_current_span(
        name,
        kind=SpanKind.INTERNAL,
        attributes=attributes,
        record_exception=False,  # an exception message can quote content
        set_status_on_exception=False,
    ) as span:
        recorder = StepRecorder(span, classify)
        try:
            yield recorder
        except BaseException as exc:
            recorder._finish(exc)
            raise
        recorder._finish(None)


def _put_all(
    pairs: Sequence[tuple[str, object, _guards.Rule]], on_withheld: Callable[[str], None]
) -> dict[str, str | int | float]:
    attributes: dict[str, str | int | float] = {}
    for key, value, rule in pairs:
        if value is None:
            continue
        checked = _guards.check(value, rule)
        if checked is None:
            on_withheld(key)
        else:
            attributes[key] = checked
    return attributes


def _check_provenance(provenance: str) -> None:
    if provenance not in ("real", "synthetic"):
        raise ValueError("provenance must be 'real' or 'synthetic'")


@contextmanager
def agent_span(
    tracer: Tracer,
    *,
    on_withheld: Callable[[str], None],
    provenance: Literal["real", "synthetic"],
    agent_name: str,
    operation: str = "invoke_agent",
    agent_id: str | None = None,
    agent_version: str | None = None,
    provider: str | None = None,
    classify: Callable[[BaseException], str] = classify_error,
) -> Iterator[StepRecorder]:
    """An INTERNAL `invoke_agent {agent_name}` (or `create_agent`) span (OTel GenAI agent
    spans). Inference and tool spans opened inside it become its children."""
    if operation not in AGENT_OPERATIONS:
        raise ValueError("operation must be 'invoke_agent' or 'create_agent'")
    _check_provenance(provenance)
    attributes = _put_all(
        [
            ("gen_ai.agent.name", agent_name, _guards.AGENT_NAME),
            ("gen_ai.agent.id", agent_id, _guards.OPAQUE_ID),
            ("gen_ai.agent.version", agent_version, _guards.AGENT_VERSION),
            ("gen_ai.provider.name", provider, _guards.PROVIDER),
        ],
        on_withheld,
    )
    attributes["gen_ai.operation.name"] = operation
    attributes["llminsight.provenance"] = provenance
    name = attributes.get("gen_ai.agent.name")
    span_name = f"{operation} {name}" if name else operation
    with _step_span(tracer, span_name, attributes, classify) as recorder:
        yield recorder


@contextmanager
def tool_span(
    tracer: Tracer,
    *,
    on_withheld: Callable[[str], None],
    provenance: Literal["real", "synthetic"],
    tool_name: str,
    tool_call_id: str | None = None,
    tool_type: str | None = None,
    mcp_method: str | None = None,
    mcp_protocol_version: str | None = None,
    classify: Callable[[BaseException], str] = classify_error,
) -> Iterator[StepRecorder]:
    """An INTERNAL `execute_tool {tool_name}` span. `tool_call_id` links it to the tool call
    the model emitted; `mcp_method` (for example `tools/call`) marks a tool reached through
    MCP. The tool's arguments and result are never accepted."""
    _check_provenance(provenance)
    attributes = _put_all(
        [
            ("gen_ai.tool.name", tool_name, _guards.TOOL_NAME),
            ("gen_ai.tool.call.id", tool_call_id, _guards.OPAQUE_ID),
            ("gen_ai.tool.type", tool_type, _guards.TOOL_TYPE),
            ("mcp.method.name", mcp_method, _guards.MCP_METHOD),
            ("mcp.protocol.version", mcp_protocol_version, _guards.VERSION),
        ],
        on_withheld,
    )
    attributes["gen_ai.operation.name"] = "execute_tool"
    attributes["llminsight.provenance"] = provenance
    name = attributes.get("gen_ai.tool.name")
    span_name = f"execute_tool {name}" if name else "execute_tool"
    with _step_span(tracer, span_name, attributes, classify) as recorder:
        yield recorder


def _check_attempts(max_attempts: int) -> None:
    if isinstance(max_attempts, bool) or not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS}")


def call_with_retries[T](
    attempt_fn: Callable[[int], T],
    *,
    max_attempts: int,
    is_retryable: Callable[[BaseException], bool],
    backoff_seconds: float = 0.0,
) -> T:
    """Run `attempt_fn(1)`, `attempt_fn(2)`, ... until one succeeds, a non-retryable error
    occurs or `max_attempts` is reached. Each call is one physical attempt with its own span."""
    _check_attempts(max_attempts)
    for attempt in range(1, max_attempts + 1):
        try:
            return attempt_fn(attempt)
        except Exception as exc:
            if attempt == max_attempts or not is_retryable(exc):
                raise
        time.sleep(backoff_seconds * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


async def acall_with_retries[T](
    attempt_fn: Callable[[int], Awaitable[T]],
    *,
    max_attempts: int,
    is_retryable: Callable[[BaseException], bool],
    backoff_seconds: float = 0.0,
) -> T:
    """Async variant of `call_with_retries`."""
    _check_attempts(max_attempts)
    for attempt in range(1, max_attempts + 1):
        try:
            return await attempt_fn(attempt)
        except Exception as exc:
            if attempt == max_attempts or not is_retryable(exc):
                raise
        await asyncio.sleep(backoff_seconds * attempt)
    raise AssertionError("unreachable")  # pragma: no cover
