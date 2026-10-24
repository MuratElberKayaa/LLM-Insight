"""Usage-ledger rows for inference spans (TELEMETRY_CONTRACT.md §4, ADR-13, ADR-21).

One row per physical attempt, only for inference spans. Phase 1B computes no money: every
row is `cost_status=unknown` with the reason that applies (no price catalogue exists yet,
so a complete, consistent row is `price_missing`). Missing tokens stay NULL, never 0.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.ingestion import attrmap
from app.services.ingestion.normalize import NormalizedSpan

MAX_CALL_KEY = 300  # usage_records.canonical_call_key LEN(300)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ns_to_datetime(ns: int) -> datetime:
    """Exact UTC conversion, truncated to the microsecond precision of TIMESTAMPTZ."""
    return EPOCH + timedelta(microseconds=ns // 1000)


def canonical_call_key(span: NormalizedSpan, provider: str) -> tuple[str, str]:
    """Master plan §E precedence: provider response ID, then SDK call_id+attempt, then span."""
    a = span.attributes
    response_id = a.get("gen_ai.response.id")
    if response_id is not None:
        key = f"resp:{provider}:{response_id}"
        if len(key) <= MAX_CALL_KEY:  # a longer key falls through to the next identity
            return key, "provider_response_id"
    call_id, attempt = a.get("llminsight.call_id"), a.get("llminsight.attempt")
    if call_id is not None and attempt is not None:
        return f"sdk:{call_id}:{attempt}", "sdk_attempt"
    return f"span:{span.trace_id}:{span.span_id}", "span_fallback"


def _cost_unknown_reason(tokens: dict[str, int | None]) -> str:
    i, o = tokens["input_tokens"], tokens["output_tokens"]
    subsets_in = (tokens["cached_input_tokens"] or 0) + (tokens["cache_write_input_tokens"] or 0)
    reasoning = tokens["reasoning_output_tokens"]
    if (i is not None and subsets_in > i) or (
        o is not None and reasoning is not None and reasoning > o
    ):
        return "usage_inconsistent"
    if i is None or o is None:
        return "usage_missing"
    return "price_missing"


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
        "cost_usd": None,
        "cost_status": "unknown",
        "cost_unknown_reason": _cost_unknown_reason(tokens),
        "price_version": None,
        "model_price_id": None,
        "provenance": span.provenance,
        "started_at": ns_to_datetime(span.start_ns),
    }
