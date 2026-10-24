"""Usage-ledger rows for inference spans (TELEMETRY_CONTRACT.md §4, ADR-13, ADR-21).

One row per physical attempt, only for inference spans. `usage_row` builds the row with an
unknown cost and the usage-level reason; `price_rows` then applies the cost engine
(`app.services.pricing`) against the stored price catalogue. Missing tokens stay NULL,
never 0, and an unpriced row keeps `cost_usd` NULL with its reason.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services import pricing
from app.services.ingestion import attrmap
from app.services.ingestion.normalize import NormalizedSpan

MAX_CALL_KEY = 300  # usage_records.canonical_call_key LEN(300)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ns_to_datetime(ns: int) -> datetime:
    """Exact UTC conversion, truncated to the microsecond precision of TIMESTAMPTZ."""
    return EPOCH + timedelta(microseconds=ns // 1000)


def _key_component(value: str) -> str:
    return value.replace("%", "%25").replace(":", "%3A")


def canonical_call_key(span: NormalizedSpan, provider: str) -> tuple[str, str]:
    """Master plan §E precedence: provider response ID, then SDK call_id+attempt, then span.

    Every key is unambiguous: the provider is the only free component before a ':' separator,
    so its '%' and ':' are percent-encoded (provider `a:b` + ID `c` and provider `a` + ID
    `b:c` stay distinct). The attempt is an integer and the span key is fixed-width hex."""
    a = span.attributes
    response_id = a.get("gen_ai.response.id")
    if response_id is not None:
        key = f"resp:{_key_component(provider)}:{response_id}"
        if len(key) <= MAX_CALL_KEY:  # a longer key falls through to the next identity
            return key, "provider_response_id"
    call_id, attempt = a.get("llminsight.call_id"), a.get("llminsight.attempt")
    if call_id is not None and attempt is not None:
        return f"sdk:{call_id}:{attempt}", "sdk_attempt"
    return f"span:{span.trace_id}:{span.span_id}", "span_fallback"


def usage_row(project_id: int, span: NormalizedSpan) -> dict[str, Any] | None:
    a = span.attributes
    if not attrmap.is_inference(a):
        return None  # parent/root/other spans never produce usage (T20, T21, T23)
    provider = a.get("gen_ai.provider.name") or "unknown"
    key, source = canonical_call_key(span, provider)
    tokens = {
        "input_tokens": a.get("gen_ai.usage.input_tokens"),
        "output_tokens": a.get("gen_ai.usage.output_tokens"),
        "cached_input_tokens": a.get("gen_ai.usage.cache_read.input_tokens"),
        "cache_write_input_tokens": a.get("gen_ai.usage.cache_write.input_tokens"),
        "reasoning_output_tokens": a.get("gen_ai.usage.reasoning.output_tokens"),
    }
    if tokens["input_tokens"] is None or tokens["output_tokens"] is None:
        usage_source = "unknown"  # never estimated: the MVP has no tokenizer
    else:
        usage_source = a.get("llminsight.usage_source", "provider_reported")
    return {
        "project_id": project_id,
        "canonical_call_key": key,
        "call_key_source": source,
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "call_id": a.get("llminsight.call_id"),
        "attempt_id": a.get("llminsight.attempt"),
        "provider_response_id": a.get("gen_ai.response.id"),
        "operation_name": a["gen_ai.operation.name"],
        "provider": provider,
        "requested_model": a.get("gen_ai.request.model"),
        "response_model": a.get("gen_ai.response.model"),
        **tokens,
        "usage_source": usage_source,
        # Priced later by `price_rows`; until then the reason is the usage-level one.
        **pricing.unknown(
            pricing.usage_unknown_reason(pricing.Tokens.from_row(tokens), usage_source)
            or "price_missing"
        ).as_columns(),
        "provenance": span.provenance,
        "started_at": ns_to_datetime(span.start_ns),
    }


def price_model_keys(rows: Sequence[dict[str, Any]]) -> set[tuple[str, str]]:
    """The (provider, model) pairs whose stored prices `price_rows` may need."""
    keys = set()
    for r in rows:
        model = pricing.priced_model(r["requested_model"], r["response_model"])
        if model is not None:
            keys.add((r["provider"], model))
    return keys


def price_rows(
    rows: Sequence[dict[str, Any]],
    spans: dict[tuple[str, str], NormalizedSpan],
    prices: Sequence[pricing.Price],
) -> None:
    """Apply the cost engine to new usage rows (in place). Runs once, at insert: the rows are
    then written with ON CONFLICT DO NOTHING and never updated, so a re-export or a later
    catalogue change never alters a stored cost."""
    for r in rows:
        outcome = pricing.assess(
            tokens=pricing.Tokens.from_row(r),
            usage_source=r["usage_source"],
            provider=r["provider"],
            requested_model=r["requested_model"],
            response_model=r["response_model"],
            attributes=spans[(r["trace_id"], r["span_id"])].attributes,
            started_at=r["started_at"],
            prices=prices,
        )
        r.update(outcome.as_columns())
