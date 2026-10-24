"""Deterministic server-side cost engine (ADR-11, METRICS_CATALOG.md M10, docs/PRICING.md).

Pure functions only: the repository loads the candidate `model_prices` rows, this module
decides. The rules, in the order they are applied to one usage row:

1. Usage. Tokens are validated and must be provider-reported:
   - a subset larger than its total (`cache_read + cache_write > input`,
     `reasoning > output`) or a malformed count → `usage_inconsistent`;
   - a missing `input_tokens`/`output_tokens`, or usage that is not `provider_reported`
     (estimated or unknown) → `usage_missing`.
2. Applicability. Prices are standard-tier text prices. A provider-specific rule decides
   whether the call was billed that way; if it cannot be shown, the price is inapplicable.
   OpenAI: `openai.response.service_tier` must be `default` and `server.address` must be
   `api.openai.com` (Flex / Batch / Fast tiers and regional endpoints differ) → else
   `price_missing`.
3. Price selection. Exact model: `response_model`, or `requested_model` only when the
   response model is absent. No alias, prefix or fuzzy matching. Of that model's rows, the
   one with the latest `effective_from <= started_at` (event time). None → `price_missing`.
4. Cost. `((in - cache_read - cache_write) * p_in + cache_read * p_cached
   + cache_write * p_cache_write + out * p_out) / 1_000_000`, in exact `Decimal`. Cached
   and cache-write tokens are subsets of input and priced once; reasoning tokens are part
   of output and priced once at the output rate. A non-zero term whose price is NULL makes
   the row `price_missing` (cached tokens are never priced at the full input rate). The
   exact sum is rounded once, half-even, to the 10 decimal places of NUMERIC(20,10). A cost
   that does not fit the column means implausible counts → `usage_inconsistent`.

Binary floats never enter: prices must already be `Decimal` (a float raises `TypeError`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Any

TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)  # prices are per 1M tokens
COST_QUANTUM = Decimal("1E-10")  # usage_records.cost_usd NUMERIC(20,10)
MAX_COST = Decimal("9999999999.9999999999")  # largest NUMERIC(20,10) value
ROUNDING = ROUND_HALF_EVEN
# Exact arithmetic: BIGINT counts (19 digits) times NUMERIC(20,10) prices need < 40 digits.
_CONTEXT = Context(prec=60, rounding=ROUNDING)


@dataclass(frozen=True)
class Tokens:
    input: int | None
    output: int | None
    cache_read: int | None
    cache_write: int | None
    reasoning: int | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Tokens:
        return cls(
            input=row["input_tokens"],
            output=row["output_tokens"],
            cache_read=row["cached_input_tokens"],
            cache_write=row["cache_write_input_tokens"],
            reasoning=row["reasoning_output_tokens"],
        )


@dataclass(frozen=True)
class Price:
    """One `model_prices` row, as the engine needs it."""

    id: int
    version: str
    provider: str
    model: str
    effective_from: datetime
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None
    cache_write_input_per_million: Decimal | None

    def __post_init__(self) -> None:
        for name in ("input_per_million", "output_per_million"):
            _require_decimal(name, getattr(self, name))
        for name in ("cached_input_per_million", "cache_write_input_per_million"):
            value = getattr(self, name)
            if value is not None:
                _require_decimal(name, value)


@dataclass(frozen=True)
class CostOutcome:
    cost_usd: Decimal | None
    cost_status: str
    cost_unknown_reason: str | None
    price_version: str | None = None
    model_price_id: int | None = None

    def as_columns(self) -> dict[str, Any]:
        return {
            "cost_usd": self.cost_usd,
            "cost_status": self.cost_status,
            "cost_unknown_reason": self.cost_unknown_reason,
            "price_version": self.price_version,
            "model_price_id": self.model_price_id,
        }


def _require_decimal(name: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise TypeError(f"{name}: prices must be finite, non-negative Decimal values")


def unknown(reason: str) -> CostOutcome:
    return CostOutcome(None, "unknown", reason)


def _count(value: object) -> bool:
    return value is None or (type(value) is int and value >= 0)


def usage_unknown_reason(tokens: Tokens, usage_source: str) -> str | None:
    """Rule 1. `None` means the usage can be priced."""
    counts = (tokens.input, tokens.output, tokens.cache_read, tokens.cache_write, tokens.reasoning)
    if not all(_count(v) for v in counts):
        return "usage_inconsistent"
    subsets_in = (tokens.cache_read or 0) + (tokens.cache_write or 0)
    if (tokens.input is not None and subsets_in > tokens.input) or (
        tokens.output is not None and (tokens.reasoning or 0) > tokens.output
    ):
        return "usage_inconsistent"
    if tokens.input is None or tokens.output is None or usage_source != "provider_reported":
        return "usage_missing"
    return None


# ---------------------------------------------------------------- rule 2: applicability
OPENAI_STANDARD_TIER = "default"
OPENAI_GLOBAL_HOST = "api.openai.com"


def _openai_standard(attributes: Mapping[str, Any]) -> bool:
    return (
        attributes.get("openai.response.service_tier") == OPENAI_STANDARD_TIER
        and attributes.get("server.address") == OPENAI_GLOBAL_HOST
    )


# Providers with billing modes beyond the stored standard-tier price. A provider without an
# entry has no such mode in the catalogue; adding a real provider requires adding its rule.
APPLICABILITY: dict[str, Callable[[Mapping[str, Any]], bool]] = {"openai": _openai_standard}


def price_applicable(provider: str, attributes: Mapping[str, Any]) -> bool:
    rule = APPLICABILITY.get(provider)
    return rule is None or rule(attributes)


# ---------------------------------------------------------------- rule 3: selection
def priced_model(requested_model: str | None, response_model: str | None) -> str | None:
    """The response model is what was served; the request model is used only without it."""
    return response_model if response_model is not None else requested_model


def select_price(prices: Iterable[Price], provider: str, model: str, at: datetime) -> Price | None:
    """The row of exactly (provider, model) in force at event time `at`."""
    in_force = [
        p for p in prices if p.provider == provider and p.model == model and p.effective_from <= at
    ]
    return max(in_force, key=lambda p: p.effective_from, default=None)


# ---------------------------------------------------------------- rule 4: cost
def compute_cost(tokens: Tokens, price: Price) -> Decimal | None:
    """Exact cost of consistent, complete usage (rule 1 passed), or `None` when a non-zero
    term has no price. The result is quantized to 10 decimal places."""
    if tokens.input is None or tokens.output is None:
        raise ValueError("compute_cost needs complete usage; check usage_unknown_reason first")
    cache_read, cache_write = tokens.cache_read or 0, tokens.cache_write or 0
    uncached = tokens.input - cache_read - cache_write
    if uncached < 0 or (tokens.reasoning or 0) > tokens.output:
        raise ValueError("inconsistent usage; check usage_unknown_reason first")
    terms: list[tuple[int, Decimal | None]] = [
        (uncached, price.input_per_million),
        (cache_read, price.cached_input_per_million),
        (cache_write, price.cache_write_input_per_million),
        (tokens.output, price.output_per_million),  # includes reasoning: billed once
    ]
    total = Decimal(0)
    for count, per_million in terms:
        if count == 0:
            continue
        if per_million is None:
            return None
        total = _CONTEXT.add(total, _CONTEXT.multiply(Decimal(count), per_million))
    exact = _CONTEXT.divide(total, TOKENS_PER_PRICE_UNIT)
    return exact.quantize(COST_QUANTUM, rounding=ROUNDING, context=_CONTEXT)


def assess(
    *,
    tokens: Tokens,
    usage_source: str,
    provider: str,
    requested_model: str | None,
    response_model: str | None,
    attributes: Mapping[str, Any],
    started_at: datetime,
    prices: Iterable[Price],
) -> CostOutcome:
    """Rules 1–4 for one usage row."""
    reason = usage_unknown_reason(tokens, usage_source)
    if reason is not None:
        return unknown(reason)
    model = priced_model(requested_model, response_model)
    if model is None or not price_applicable(provider, attributes):
        return unknown("price_missing")
    price = select_price(prices, provider, model, started_at)
    if price is None:
        return unknown("price_missing")
    cost = compute_cost(tokens, price)
    if cost is None:
        return unknown("price_missing")
    if cost > MAX_COST:
        return unknown("usage_inconsistent")
    return CostOutcome(cost, "computed", None, price.version, price.id)
