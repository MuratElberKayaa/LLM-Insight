"""Read-time list-price reference for one Models Compare group (ADR-52, PRICING.md §9).

**Outside the cost ledger.** Nothing here writes, reads or changes `cost_usd`,
`cost_status` or any stored row. The amount is "what these provider-reported tokens would cost
at the published price in `reference_pricing_catalog`". It is not a charge, and the API labels it
`reference_not_billed`.

One group of `GET /api/v1/models/compare` has one `(provider, requested_model, response_model)`,
so one catalogue row prices all of it. The equation is the ledger's own `pricing.compute_cost`
(subsets priced once, reasoning inside output, exact Decimal, rounded once, half-even, to 10
places). With one price per group, the cost of the summed tokens equals the sum of per-row costs.

A usage row is **eligible** when the ledger found its usage priceable: `cost_status = computed`,
or `unknown` with `price_missing` (rule 1 of `pricing.assess` runs before applicability). It is
also required to have no cache-write tokens, because the catalogue lists no cache-write rate.
Ineligible rows are counted by reason, and the reasons add up to `calls_not_priced`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.services import pricing
from app.services.reference_pricing_catalog import ENTRIES, STALE_AFTER, ReferencePrice

LABEL = "reference_not_billed"
NOT_PRICED_REASONS = (
    "usage_missing",
    "usage_inconsistent",
    "cache_write_not_representable",
    "not_in_reference_catalogue",
    "price_not_listed",
)


@dataclass(frozen=True)
class GroupUsage:
    """Aggregates of one compare group, from `repositories.metrics.model_groups`."""

    calls: int  # every usage row of the group
    eligible_calls: int  # priceable usage and no cache-write tokens
    input_tokens: int  # sums over the eligible rows only
    cached_input_tokens: int
    output_tokens: int  # includes reasoning; reasoning is never added again
    usage_missing: int  # ledger reasons of the ineligible rows
    usage_inconsistent: int
    cache_write_calls: int  # priceable usage, but cache-write tokens were reported

    def __post_init__(self) -> None:
        counts = (
            self.calls,
            self.eligible_calls,
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.usage_missing,
            self.usage_inconsistent,
            self.cache_write_calls,
        )
        if any(type(v) is not int or v < 0 for v in counts):
            raise ValueError("group usage counts must be non-negative integers")
        if self.eligible_calls > self.calls:
            raise ValueError("more eligible calls than calls")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens exceed input tokens")
        ineligible = self.usage_missing + self.usage_inconsistent + self.cache_write_calls
        if self.eligible_calls + ineligible != self.calls:
            raise ValueError("eligible and ineligible counts must add up to calls")


def find(
    provider: str, model: str | None, catalogue: Iterable[ReferencePrice] = ENTRIES
) -> ReferencePrice | None:
    """Exactly `(provider, model)`: no alias, prefix, case folding or fuzzy match."""
    if model is None:
        return None
    return next((e for e in catalogue if e.provider == provider and e.model == model), None)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def reference_block(
    provider: str,
    requested_model: str | None,
    response_model: str | None,
    usage: GroupUsage,
    *,
    now: datetime,
    catalogue: Iterable[ReferencePrice] = ENTRIES,
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    entry = find(provider, pricing.priced_model(requested_model, response_model), catalogue)
    by_reason = dict.fromkeys(NOT_PRICED_REASONS, 0)
    by_reason["usage_missing"] = usage.usage_missing
    by_reason["usage_inconsistent"] = usage.usage_inconsistent
    by_reason["cache_write_not_representable"] = usage.cache_write_calls

    amount: Decimal | None = None
    reason: str | None = None
    if entry is None:
        reason = "not_in_reference_catalogue"
    elif usage.eligible_calls == 0:
        reason = "no_priced_calls"
    elif usage.cached_input_tokens > 0 and entry.cached_input_per_million is None:
        reason = "price_not_listed"  # never charged at the full input rate
    else:
        amount = pricing.compute_cost(
            pricing.Tokens(
                input=usage.input_tokens,
                output=usage.output_tokens,
                cache_read=usage.cached_input_tokens,
                cache_write=0,
                reasoning=None,
            ),
            pricing.Price(
                id=0,  # not a `model_prices` row: the reference is never stored
                version=entry.version,
                provider=entry.provider,
                model=entry.model,
                effective_from=entry.verified_at,
                input_per_million=entry.input_per_million,
                output_per_million=entry.output_per_million,
                cached_input_per_million=entry.cached_input_per_million,
                cache_write_input_per_million=None,
            ),
        )
        if amount is None:  # unreachable: every non-zero term has a price here
            raise AssertionError("reference price without a rate for a non-zero term")
    if reason in ("not_in_reference_catalogue", "price_not_listed"):
        by_reason[reason] = usage.eligible_calls
    priced = usage.eligible_calls if amount is not None else 0

    return {
        "available": amount is not None,
        "unavailable_reason": reason,
        "label": LABEL,
        "currency": None if entry is None else entry.currency,
        "tier": None if entry is None else entry.tier,
        "catalogue_version": None if entry is None else entry.version,
        "verified_at": None if entry is None else _iso(entry.verified_at),
        "source_url": None if entry is None else entry.source_url,
        "reference_usd": None if amount is None else format(amount, "f"),
        "calls_priced": priced,
        "calls_not_priced": usage.calls - priced,
        "not_priced_by_reason": by_reason,
        "stale": None if entry is None else now - entry.verified_at > STALE_AFTER,
    }
