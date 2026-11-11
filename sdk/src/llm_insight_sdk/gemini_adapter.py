"""Gemini Developer API adapter (Phase 6C-A, ADR-33): one CLIENT span per physical
`models.generateContent` request.

    import llm_insight_sdk as lli
    from google import genai
    from llm_insight_sdk.gemini_adapter import GeminiTextAdapter, ObservedGeminiClient

    telemetry = lli.init(lli.LLMInsightConfig.from_env())
    llm = ObservedGeminiClient(telemetry, genai.Client(vertexai=False, api_key=...),
                               provenance="real")
    outcome = GeminiTextAdapter(llm).generate_text(TextRequest(...))

Needs the optional dependency: `pip install "llm-insight-sdk[gemini]"` (google-genai 2.30.1).

**Surface.** Only `client.models.generate_content(...)` of the Gemini Developer API
(`https://generativelanguage.googleapis.com/v1beta`), for one single-turn text input,
without streaming. Vertex AI / Gemini Enterprise clients are refused. Refused before any
request is sent: a non-text or multi-turn `contents`, and every generation setting outside
`CONFIG_ALLOWED` (`tools`, `tool_config`, `cached_content`, `http_options`, `thinking_config`,
`system_instruction`, `safety_settings`, structured output, ...). The adapter always sends
`automatic_continuation=False` and `automatic_function_calling.disable=True`: with
google-genai 2.30.1 automatic continuation is **on by default** and silently sends further
billable requests while merging their usage into one response (probed offline), which would
put several physical requests inside one span.

**Telemetry** (semconv pin `9ffc7e00c639`, `llminsight.attrmap.v1`, no backend change):
provider `gcp.gemini` ("used when accessing the generativelanguage.googleapis.com
endpoint"), operation `generate_content`, request model, `modelVersion` as the response
model, `responseId` as the response ID, the candidate's finish reason verbatim (`STOP`,
`MAX_TOKENS`, `SAFETY`, ...), `server.address`/`server.port`, numeric settings. Never:
contents, output text, thoughts, safety ratings, headers, the API key, provider error
messages or bodies (google-genai puts the whole error body into `str(exc)`).

**Usage** (`usage_from_gemini_response`). The API documents `totalTokenCount` as
prompt + tool-use prompt + thoughts + response candidates, `promptTokenCount` as including
cached content, and output pricing as candidates + thinking. Mapping onto the semconv
counters, where a detail count is a subset of its total:

    gen_ai.usage.input_tokens               = promptTokenCount + toolUsePromptTokenCount
    gen_ai.usage.output_tokens              = candidatesTokenCount + thoughtsTokenCount
    gen_ai.usage.cache_read.input_tokens    = cachedContentTokenCount (subset of input)
    gen_ai.usage.reasoning.output_tokens    = thoughtsTokenCount (subset of output)

JSON omits zero counts, so an absent count is taken as 0 **only** when the reported
`totalTokenCount` equals that sum; a missing prompt or total count makes the whole usage
unknown (`missing`), a sum that does not match the total or a cached count above the prompt
count makes it unknown (`inconsistent`). Nothing is estimated from text.

**Outcomes.** `STOP` → `completed`; `MAX_TOKENS`/`CONTINUATION` → `incomplete` with the
reason; a blocked prompt (`promptFeedback.blockReason`, span ERROR
`gcp.gemini.prompt_blocked`) or a safety-type finish reason (span UNSET, reason recorded) →
`blocked`; no candidate → `failed` (`gcp.gemini.response.empty`); another finish reason →
`failed` (`gcp.gemini.response.failed`); none → `failed`
(`gcp.gemini.response.unexpected_finish`); an unreadable body → `error`/`malformed_response`.

**Attempts.** Exactly one physical request per call: the adapter never retries, and the
library's own retries stay at its default of one attempt (`retry_options` unset), which the
live check enforces.
"""

from __future__ import annotations

import enum
import logging
import re
import ssl
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Literal, cast

import httpx
from google import genai
from google.genai import _api_client as genai_api_client
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from llm_insight_sdk import _guards, instrumentation
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.instrumentation import (
    InferenceRecorder,
    RequestParams,
    TokenUsage,
    classify_error,
    new_call_id,
)
from llm_insight_sdk.providers import AttemptRecord, OutcomeStatus, TextOutcome, TextRequest
from llm_insight_sdk.telemetry import Telemetry

logger = logging.getLogger("llm_insight_sdk")

PROVIDER = "gcp.gemini"
OPERATION = "generate_content"
GEMINI_HOST = "generativelanguage.googleapis.com"
LIVE_BASE_URL = f"https://{GEMINI_HOST}/"
LIVE_API_VERSION = "v1beta"
# The generation settings a caller may pass; everything else is refused before sending.
CONFIG_ALLOWED = frozenset({"max_output_tokens", "temperature", "top_p"})
# A plain model ID: no `models/` or `tunedModels/` path, no `:method`, no query.
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,127}")

COMPLETED_REASONS = frozenset({"STOP"})
INCOMPLETE_REASONS = frozenset({"MAX_TOKENS", "CONTINUATION"})
BLOCKED_REASONS = frozenset(
    {
        "SAFETY",
        "RECITATION",
        "LANGUAGE",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)
UNSPECIFIED_REASON = "FINISH_REASON_UNSPECIFIED"
_STATUS_KEYWORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

Provenance = Literal["real", "synthetic"]
UsageCheck = Literal["consistent", "missing", "inconsistent"]


class UnsupportedRequestError(ValueError):
    """The request uses a feature the Gemini adapter does not instrument. Nothing was sent."""


# ---------------------------------------------------------------- errors
def _api_status(exc: BaseException) -> str | None:
    """The error's status keyword (`RESOURCE_EXHAUSTED`, ...) when it is one, else None."""
    status = getattr(exc, "status", None)
    return status if isinstance(status, str) and _STATUS_KEYWORD.fullmatch(status) else None


def _api_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def classify_gemini_error(exc: BaseException) -> str:
    """semconv `error.type` of a failed attempt. Never `str(exc)`: google-genai puts the
    provider's error body (which can quote the request) into the exception message."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, genai_errors.APIError):
        code = _api_code(exc)
        return str(code) if code is not None and 100 <= code <= 599 else "gcp.gemini.APIError"
    if isinstance(exc, httpx.HTTPError):
        return f"httpx.{type(exc).__name__}"
    if isinstance(exc, ValueError):  # JSONDecodeError, pydantic ValidationError
        return "gcp.gemini.response.malformed"
    return classify_error(exc)


def gemini_error_category(exc: BaseException) -> str:
    """Provider-neutral category (`providers.ERROR_CATEGORIES`) from the exception class,
    the HTTP status and the status keyword only."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "connection_error"
    if isinstance(exc, genai_errors.APIError):
        code = _api_code(exc) or 0
        if code == 400 and (_api_status(exc) or "").upper() == "FAILED_PRECONDITION":
            return "precondition_failed"
        categories = {
            401: "authentication_failed",
            402: "payment_required",
            403: "permission_denied",
            404: "not_found",
            429: "rate_limited",  # per-minute or daily quota: never retried here
        }
        if code in categories:
            return categories[code]
        if code in (400, 409, 416, 422):
            return "invalid_request"
        return "provider_error" if code >= 500 else "http_error"
    if isinstance(exc, ValueError):
        return "malformed_response"
    return "unexpected_error"


# ---------------------------------------------------------------- usage
def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


@dataclass(frozen=True)
class UsageReading:
    """The mapped usage (None = unknown, never 0), why it is unknown, and the provider's raw
    counts (`None` = absent in the response) for side-by-side display."""

    usage: TokenUsage | None
    check: UsageCheck
    provider_counts: dict[str, int | None]


_PROVIDER_FIELDS = (
    ("prompt", "prompt_token_count"),
    ("candidates", "candidates_token_count"),
    ("thoughts", "thoughts_token_count"),
    ("cached_content", "cached_content_token_count"),
    ("tool_use_prompt", "tool_use_prompt_token_count"),
    ("total", "total_token_count"),
)


def usage_from_gemini_response(response: object) -> UsageReading:
    """Provider-reported usage of a `generateContent` response (module docstring)."""
    meta = getattr(response, "usage_metadata", None)
    raw = {name: getattr(meta, attr, None) for name, attr in _PROVIDER_FIELDS}
    counts = {name: _count(value) for name, value in raw.items()}
    if meta is None:
        return UsageReading(None, "missing", counts)
    if any(raw[n] is not None and counts[n] is None for n in raw):
        return UsageReading(None, "inconsistent", counts)  # present but not a count
    prompt, total = counts["prompt"], counts["total"]
    if prompt is None or total is None:
        return UsageReading(None, "missing", counts)
    tool_use = counts["tool_use_prompt"] or 0
    candidates = counts["candidates"] or 0
    thoughts = counts["thoughts"] or 0
    cached = counts["cached_content"]
    if prompt + tool_use + candidates + thoughts != total or (cached or 0) > prompt:
        return UsageReading(None, "inconsistent", counts)
    usage = TokenUsage(
        input_tokens=prompt + tool_use,
        output_tokens=candidates + thoughts,
        cache_read_input_tokens=cached,
        reasoning_output_tokens=counts["thoughts"],
        source="provider_reported",
    )
    return UsageReading(usage, "consistent", counts)


# ---------------------------------------------------------------- outcome
def _keyword(value: object) -> str | None:
    if isinstance(value, enum.Enum):
        value = value.value
    return _guards.check(value, _guards.KEYWORD) if isinstance(value, str) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class ResponseAssessment:
    status: OutcomeStatus
    reason: str | None  # the provider's finish or block reason, verbatim
    category: str | None  # providers.ERROR_CATEGORIES, None when completed/incomplete
    span_error: str | None  # span error.type, None when the span stays UNSET
    finish_reason: str | None  # the candidate's finish reason, recorded on the span


def assess_gemini_response(response: object) -> ResponseAssessment:
    """What a returned `generateContent` response means (module docstring)."""
    if not isinstance(response, genai_types.GenerateContentResponse):
        return ResponseAssessment(
            "malformed", None, "malformed_response", "gcp.gemini.response.malformed", None
        )
    block = _keyword(getattr(response.prompt_feedback, "block_reason", None))
    if block is not None and block != "BLOCKED_REASON_UNSPECIFIED":
        return ResponseAssessment(
            "blocked", block, "content_blocked", "gcp.gemini.prompt_blocked", None
        )
    candidates = response.candidates
    if not isinstance(candidates, list) or not candidates:
        return ResponseAssessment(
            "failed", None, "empty_response", "gcp.gemini.response.empty", None
        )
    finish = _keyword(getattr(candidates[0], "finish_reason", None))
    if finish in COMPLETED_REASONS:
        return ResponseAssessment("completed", None, None, None, finish)
    if finish in INCOMPLETE_REASONS:
        return ResponseAssessment("incomplete", finish, None, None, finish)
    if finish in BLOCKED_REASONS:
        return ResponseAssessment("blocked", finish, "content_blocked", None, finish)
    if finish is None or finish == UNSPECIFIED_REASON:
        return ResponseAssessment(
            "failed", finish, "unexpected_status", "gcp.gemini.response.unexpected_finish", None
        )
    return ResponseAssessment(
        "failed", finish, "response_failed", "gcp.gemini.response.failed", finish
    )


def _record_response(recorder: InferenceRecorder | None, response: object) -> None:
    if recorder is None:
        return
    try:
        verdict = assess_gemini_response(response)
        if verdict.status != "malformed":
            recorder.record_response(
                response_model=_text(getattr(response, "model_version", None)),
                response_id=_text(getattr(response, "response_id", None)),
                finish_reasons=[verdict.finish_reason] if verdict.finish_reason else [],
                usage=usage_from_gemini_response(response).usage,
            )
        if verdict.span_error is not None:
            recorder.mark_failed(verdict.span_error)
    except Exception:  # telemetry must never break the call
        logger.debug("llm_insight_sdk: recording the Gemini response failed", exc_info=False)


# ---------------------------------------------------------------- live endpoint boundary
# A real-mode credential may only travel to LIVE_BASE_URL (ADR-34). The checks read the
# pinned libraries' configuration attributes, so they are valid for the audited versions only.
AUDITED_LIBRARY_VERSIONS = {"google-genai": "2.30.1", "httpx": "0.28.1", "httpcore": "1.0.9"}
# The headers google-genai 2.30.1 itself puts on a Developer API client; `X-Server-Timeout`
# is added to the same dict when the first request is built.
_ALLOWED_CLIENT_HEADERS = frozenset(
    {"content-type", "x-goog-api-key", "user-agent", "x-goog-api-client", "x-server-timeout"}
)
_CREDENTIAL_HEADERS = ("authorization", "x-goog-api-key", "x-goog-user-project")
_MISSING = object()


def gemini_library_problems() -> list[str]:
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


def live_gemini_client_problems(client: object) -> list[str]:
    """Every reason `client` may not carry a real-mode Gemini API key; empty when it may.

    Allowed: exactly a `google.genai.Client` whose API client is exactly the plain
    `BaseApiClient` (no replay client) of the **Gemini Developer API** (`vertexai is False`:
    no Vertex AI / Enterprise, project, location, credentials or custom base URL), for
    exactly `LIVE_BASE_URL` + `v1beta`, authenticated only by its non-empty API key in
    `x-goog-api-key` (no other credential or routing header, no ephemeral token), with no
    `extra_body`, no retry options (the library's one attempt) and no mTLS switch; sending
    through an `httpx.Client` that follows no redirect, ignores proxy and CA environment
    settings, has no mount, hook or auth, carries no credential header, and uses the plain
    `HTTPTransport` with a direct connection pool and a verifying TLS context. Reads
    configuration only and never quotes the key; an unreadable attribute is a problem."""
    problems = gemini_library_problems()
    if type(client) is not genai.Client:
        return [*problems, "client must be a google.genai.Client instance (no subclass)"]

    def attr(obj: object, name: str) -> Any:
        return getattr(obj, name, _MISSING)

    debug = attr(client, "_debug_config")
    if attr(debug, "client_mode") is not None:
        problems.append("no google-genai record/replay client mode may be configured")
    api = attr(client, "_api_client")
    if type(api) is not genai_api_client.BaseApiClient:
        return [*problems, "the API client must be the plain google-genai BaseApiClient"]
    if attr(api, "vertexai") is not False:
        problems.append("the Gemini Developer API is required (vertexai must be False)")
    for name, what in (
        ("custom_base_url", "a custom base URL"),
        ("project", "a Google Cloud project"),
        ("location", "a Google Cloud location"),
        ("_credentials", "Google Cloud credentials"),
    ):
        if attr(api, name) is not None:
            problems.append(f"{what} must not be configured ({name})")
    key = attr(api, "api_key")
    if not isinstance(key, str) or not key or key.startswith("auth_tokens/"):
        problems.append("api_key must be a non-empty Gemini API key (no ephemeral token)")
    options = attr(api, "_http_options")
    if attr(options, "base_url") != LIVE_BASE_URL:
        problems.append(f"base_url must be exactly {LIVE_BASE_URL}")
    if attr(options, "api_version") != LIVE_API_VERSION:
        problems.append(f"api_version must be exactly {LIVE_API_VERSION}")
    if attr(options, "extra_body") is not None:
        problems.append("no extra request body may be configured")
    if attr(options, "retry_options") is not None:
        problems.append("library retries must stay disabled (no retry_options)")
    stop = attr(attr(api, "_retry"), "stop")
    if attr(stop, "max_attempt_number") != 1:
        problems.append("the library must send exactly one attempt")
    headers = attr(options, "headers")
    if not isinstance(headers, dict):
        problems.append("the client headers must be readable")
    else:
        names = {str(k).lower() for k in headers}
        if not names <= _ALLOWED_CLIENT_HEADERS:
            problems.append("no custom, credential or routing header may be configured")
        if not isinstance(key, str) or headers.get("x-goog-api-key") != key:
            problems.append("the API key header must carry the client's own API key")
    if attr(api, "_httpx_use_mtls_endpoint") is not False:
        problems.append("the mTLS endpoint switch must be off")
    try:
        google_auth_session = api._use_google_auth_sync()
    except Exception:
        google_auth_session = True
    if google_auth_session is not False:
        problems.append("no google-auth session may send the request")

    http = attr(api, "_httpx_client")
    if type(http) is not httpx.Client:
        return [*problems, "the HTTP client must be exactly an httpx.Client"]
    if attr(http, "follow_redirects") is not False:
        problems.append("HTTP redirects must not be followed")
    if attr(http, "_trust_env") is not False:
        problems.append("proxy and CA environment settings must be ignored (trust_env=False)")
    if attr(http, "_mounts") != {}:
        problems.append("no proxy or transport mount may be configured")
    if attr(http, "auth") is not None:
        problems.append("no httpx auth may be configured")
    hooks = attr(http, "event_hooks")
    if not isinstance(hooks, dict) or any(hooks.values()):
        problems.append("no HTTP event hook may be configured")
    http_headers = attr(http, "headers")
    if not isinstance(http_headers, httpx.Headers) or any(
        name in http_headers for name in _CREDENTIAL_HEADERS
    ):
        problems.append("the HTTP client must carry no credential or routing header")
    transport = attr(http, "_transport")
    pool = attr(transport, "_pool")
    if type(transport) is not httpx.HTTPTransport or type(pool).__name__ != "ConnectionPool":
        problems.append("the default direct HTTPTransport (no proxy, no mock) must be used")
    else:
        tls = attr(pool, "_ssl_context")
        if attr(tls, "verify_mode") != ssl.CERT_REQUIRED or attr(tls, "check_hostname") is not True:
            problems.append("TLS certificate and hostname verification must be enabled")
        # R123 (2): a context with key logging writes the TLS session keys to a file. A missing
        # attribute (not an `ssl.SSLContext`) is refused as well, never read as "off".
        if type(tls) is not ssl.SSLContext or attr(tls, "keylog_filename") is not None:
            problems.append("TLS key logging must be off")
    # The library's async client and websocket context are never used to send, but each holds
    # a TLS context of its own, built from the environment unless one is given (R123).
    async_tls = attr(
        attr(attr(attr(api, "_async_httpx_client"), "_transport"), "_pool"), "_ssl_context"
    )
    websocket_args = attr(api, "_websocket_ssl_ctx")  # request arguments: {"ssl": context}
    websocket_tls = websocket_args.get("ssl") if isinstance(websocket_args, dict) else _MISSING
    for context, where in ((async_tls, "async client"), (websocket_tls, "websocket")):
        if type(context) is not ssl.SSLContext or attr(context, "keylog_filename") is not None:
            problems.append(f"TLS key logging must be off ({where})")
    return problems


def check_live_gemini_client(client: object) -> None:
    """Raise `ConfigError` naming every `live_gemini_client_problems` entry (no value)."""
    problems = live_gemini_client_problems(client)
    if problems:
        raise ConfigError("live Gemini client: " + "; ".join(problems))


def _check_client(client: object, provenance: str) -> None:
    """Every mode: a Gemini Developer API client for the public endpoint."""
    if provenance not in ("real", "synthetic"):
        raise ConfigError("provenance: must be 'real' or 'synthetic'")
    if not isinstance(client, genai.Client):
        raise ConfigError("client: must be a google.genai.Client instance")
    api = getattr(client, "_api_client", None)
    if getattr(api, "vertexai", None) is not False:
        raise ConfigError("client: the Gemini Developer API is required (vertexai=False)")
    base = getattr(getattr(api, "_http_options", None), "base_url", None)
    if base != LIVE_BASE_URL:
        raise ConfigError(f"client: only {LIVE_BASE_URL} is supported (base_url refused)")
    transport = getattr(getattr(api, "_httpx_client", None), "_transport", None)
    if provenance == "real" and isinstance(transport, httpx.MockTransport):
        raise ConfigError("provenance: 'real' is refused for a client with a mock transport")


# ---------------------------------------------------------------- the instrumented client
AttemptObserver = Callable[[int, object], None]


def _notify(observer: AttemptObserver | None, attempt: int, outcome: object) -> None:
    if observer is None:
        return
    try:
        observer(attempt, outcome)
    except Exception:  # an observer must not change the call's outcome
        logger.debug("llm_insight_sdk: an attempt observer raised", exc_info=False)


def _refuse_unsupported(model: object, contents: object, config: Mapping[str, Any]) -> None:
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        raise UnsupportedRequestError("model must be a plain Gemini model ID")
    if not isinstance(contents, str):
        raise UnsupportedRequestError("contents must be one text string (single turn)")
    for name in config:
        if name not in CONFIG_ALLOWED:
            raise UnsupportedRequestError(f"{name} is not supported by the Gemini adapter")


def generation_config(config: Mapping[str, Any]) -> genai_types.GenerateContentConfig:
    """The allowed settings plus the two forced ones (no automatic extra requests)."""
    return genai_types.GenerateContentConfig(
        **config,
        automatic_continuation=False,
        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
    )


def _params(config: Mapping[str, Any]) -> RequestParams:
    def number(value: object) -> float | None:
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        return float(cast(float, value)) if ok else None

    tokens = config.get("max_output_tokens")
    return RequestParams(
        max_tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        temperature=number(config.get("temperature")),
        top_p=number(config.get("top_p")),
    )


class ObservedGeminiClient:
    """Instrumented `google.genai.Client.models.generate_content` (sync, one attempt)."""

    def __init__(
        self,
        telemetry: Telemetry,
        client: genai.Client,
        *,
        provenance: Provenance,
        require_live_endpoint: bool = False,
    ) -> None:
        """`require_live_endpoint=True` (the live runner, ADR-34) applies
        `check_live_gemini_client` now and again immediately before every send."""
        _check_client(client, provenance)
        if require_live_endpoint:
            check_live_gemini_client(client)
        self._telemetry = telemetry
        self._client = client
        self._provenance: Provenance = provenance
        self._require_live_endpoint = require_live_endpoint

    @contextmanager
    def _attempt_span(
        self,
        model: str,
        call_id: str,
        prompt_name: str | None,
        prompt_version: str | None,
        config: Mapping[str, Any],
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
                params=_params(config),
                classify=classify_gemini_error,
            )
            recorder = cm.__enter__()
        except Exception:
            logger.debug("llm_insight_sdk: starting the Gemini span failed", exc_info=False)
            yield None
            return
        recorder.set_metadata("server.address", GEMINI_HOST)
        recorder.set_metadata("server.port", 443)
        try:
            yield recorder
        except BaseException as exc:
            try:
                cm.__exit__(type(exc), exc, exc.__traceback__)  # records ERROR, re-raises exc
            except BaseException as raised:
                if raised is not exc:
                    logger.debug("llm_insight_sdk: ending the Gemini span failed", exc_info=False)
            raise
        else:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                logger.debug("llm_insight_sdk: ending the Gemini span failed", exc_info=False)

    def generate_content(
        self,
        *,
        model: str,
        contents: str,
        prompt_name: str | None = None,
        prompt_version: str | None = None,
        attempt_observer: AttemptObserver | None = None,
        **config: Any,
    ) -> genai_types.GenerateContentResponse:
        """`client.models.generate_content(model=..., contents=..., config=...)` as exactly
        one physical request. `contents` and the output are never recorded.
        `attempt_observer(1, response_or_exception)` is called once; its errors are ignored."""
        _refuse_unsupported(model, contents, config)
        request_config = generation_config(config)
        if self._require_live_endpoint:
            check_live_gemini_client(self._client)  # the client that actually sends
        with self._attempt_span(
            model, new_call_id(), prompt_name, prompt_version, config
        ) as recorder:
            try:
                response = self._client.models.generate_content(
                    model=model, contents=contents, config=request_config
                )
            except BaseException as exc:
                _notify(attempt_observer, 1, exc)
                raise
            _record_response(recorder, response)
            _notify(attempt_observer, 1, response)
            return response


class GeminiTextAdapter:
    """`TextGenerationAdapter` over `ObservedGeminiClient.generate_content` (ADR-33): one
    non-streaming text request, one physical attempt, metadata-only outcome, provider
    failures returned as an outcome instead of raised."""

    provider = PROVIDER

    def __init__(self, client: ObservedGeminiClient) -> None:
        self._client = client

    def generate_text(self, request: TextRequest) -> TextOutcome:
        records: list[AttemptRecord] = []

        def observe(attempt: int, outcome: object) -> None:
            if isinstance(outcome, BaseException):
                records.append(
                    AttemptRecord(attempt, error_category=gemini_error_category(outcome))
                )
                return
            verdict = assess_gemini_response(outcome)
            readable = verdict.status != "malformed"
            records.append(
                AttemptRecord(
                    attempt,
                    usage=usage_from_gemini_response(outcome).usage if readable else None,
                    response_model=_text(getattr(outcome, "model_version", None)),
                    error_category=verdict.category,
                )
            )

        started = time.monotonic()
        try:
            response = self._client.generate_content(
                model=request.model,
                contents=request.input_text,
                max_output_tokens=request.max_output_tokens,
                prompt_name=request.prompt_name,
                prompt_version=request.prompt_version,
                attempt_observer=observe,
            )
        except UnsupportedRequestError:
            raise
        except ConfigError:
            raise  # the live client check refused: nothing was sent
        except Exception as exc:
            category = records[-1].error_category if records else gemini_error_category(exc)
            return TextOutcome(
                provider=PROVIDER,
                status="error",
                request_model=request.model,
                response_model=None,
                response_id=None,
                service_tier=None,
                usage=None,
                attempts=tuple(records),
                error_category=category,
                duration_ms=_elapsed(started),
                provider_reason=_api_status(exc),
            )
        verdict = assess_gemini_response(response)
        readable = verdict.status != "malformed"
        return TextOutcome(
            provider=PROVIDER,
            status=verdict.status,
            request_model=request.model,
            response_model=_text(getattr(response, "model_version", None)) if readable else None,
            response_id=_text(getattr(response, "response_id", None)) if readable else None,
            service_tier=None,
            usage=usage_from_gemini_response(response).usage if readable else None,
            attempts=tuple(records),
            error_category=verdict.category,
            duration_ms=_elapsed(started),
            provider_response=response,
            incomplete_reason=verdict.reason if verdict.status == "incomplete" else None,
            provider_reason=verdict.reason,
        )


def _elapsed(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)
