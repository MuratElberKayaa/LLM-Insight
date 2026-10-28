"""Provider-neutral text generation contract (Phase 6A, ADR-28).

A `TextGenerationAdapter` runs one logical text request against one provider and returns a
`TextOutcome` made of **metadata only**: status, models, response ID, provider-reported
usage, attempts and a stable error category. The provider's own response object is kept
on `TextOutcome.provider_response` for the application; it is excluded from `repr()` and
equality, and nothing in this package logs, prints or exports it.

Adapters never raise a provider failure: a failed call is an outcome with
`status = "error"` and an `error_category`. They raise only for a request they refuse
before sending (`UnsupportedRequestError` in the OpenAI adapter) or invalid configuration.

Implementations: `openai_adapter.OpenAIResponsesTextAdapter` (ADR-28) and, since Phase 6C-A,
`gemini_adapter.GeminiTextAdapter` (ADR-33). Each has its own usage mapping and error
categories and a backend pricing applicability rule (docs/PRICING.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from llm_insight_sdk.instrumentation import TokenUsage

# `blocked` (Phase 6C-A): the provider answered, but withheld the output for a content
# reason (a blocked prompt or a safety-stopped candidate). It is never `completed`.
OutcomeStatus = Literal["completed", "incomplete", "blocked", "failed", "malformed", "error"]

# Stable, provider-neutral error categories (docs/PHASE6_LIVE_INTEGRATION_PLAN.md §F).
# Span `error.type` keeps the telemetry-contract values (`429`, `timeout`, ...); these
# categories are for callers and the live runner's output only.
ERROR_CATEGORIES = frozenset(
    {
        "timeout",
        "connection_error",
        "rate_limited",
        "quota_exhausted",
        "authentication_failed",
        "permission_denied",
        "not_found",
        "invalid_request",
        "provider_error",
        "http_error",
        "malformed_response",
        "response_failed",
        "unexpected_status",
        "unexpected_error",
        # Phase 6C-A (Gemini, additive)
        "payment_required",  # 402: a paid project's prepaid credit is exhausted
        "precondition_failed",  # 400 FAILED_PRECONDITION, e.g. billing not set up
        "content_blocked",
        "empty_response",
    }
)


@dataclass(frozen=True)
class TextRequest:
    """One text request. `input_text` is passed to the provider and never recorded."""

    model: str
    input_text: str = field(repr=False)
    max_output_tokens: int
    prompt_name: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class AttemptRecord:
    """One physical request (one CLIENT span). `usage` is None when the provider reported
    none, including every failed attempt."""

    attempt: int
    usage: TokenUsage | None = None
    response_model: str | None = None
    service_tier: str | None = None
    error_category: str | None = None


@dataclass(frozen=True)
class TextOutcome:
    """Metadata of one logical call. `usage` is the last attempt's provider-reported usage
    or None (unknown, never 0). `incomplete_reason` is the provider's own reason for an
    `incomplete` status (e.g. `max_output_tokens`): an incomplete answer is not a completed
    one. `provider_reason` (Phase 6C-A, additive) is the provider's own keyword for an outcome
    that is not `completed`: a finish or block reason, or an error status such as
    `RESOURCE_EXHAUSTED`; never a message."""

    provider: str
    status: OutcomeStatus
    request_model: str
    response_model: str | None
    response_id: str | None
    service_tier: str | None
    usage: TokenUsage | None
    attempts: tuple[AttemptRecord, ...]
    error_category: str | None
    duration_ms: float
    provider_response: object | None = field(default=None, repr=False, compare=False)
    incomplete_reason: str | None = None
    provider_reason: str | None = None


class TextGenerationAdapter(Protocol):
    """One provider's instrumented text generation (the ProviderAdapter abstraction)."""

    @property
    def provider(self) -> str: ...

    def generate_text(self, request: TextRequest) -> TextOutcome: ...
