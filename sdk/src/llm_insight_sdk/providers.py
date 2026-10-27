"""Provider-neutral text generation contract (Phase 6A, ADR-28).

A `TextGenerationAdapter` runs one logical text request against one provider and returns a
`TextOutcome` made of **metadata only**: status, models, response ID, provider-reported
usage, attempts and a stable error category. The provider's own response object is kept
on `TextOutcome.provider_response` for the application; it is excluded from `repr()` and
equality, and nothing in this package logs, prints or exports it.

Adapters never raise a provider failure: a failed call is an outcome with
`status = "error"` and an `error_category`. They raise only for a request they refuse
before sending (`UnsupportedRequestError` in the OpenAI adapter) or invalid configuration.

The only implementation is `openai_adapter.OpenAIResponsesTextAdapter` (ADR-05: one
provider adapter). A second provider implements the same protocol, its own usage mapping
and error categories, and a backend pricing applicability rule (docs/PRICING.md §3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from llm_insight_sdk.instrumentation import TokenUsage

OutcomeStatus = Literal["completed", "incomplete", "failed", "malformed", "error"]

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
    or None (unknown, never 0)."""

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


class TextGenerationAdapter(Protocol):
    """One provider's instrumented text generation (the ProviderAdapter abstraction)."""

    @property
    def provider(self) -> str: ...

    def generate_text(self, request: TextRequest) -> TextOutcome: ...
