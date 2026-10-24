"""Verified OpenAI price catalogue `openai-2026-10-24` (docs/PRICING.md §2).

Every value was read from the official OpenAI documentation on 2026-10-24 (UTC; last
re-check of the pricing page completed by 21:38:20Z):
- the pricing page (Standard tier, "Prices per 1M tokens", USD), and
- each model's page, which shows the same three prices and names the model's
  "Default snapshot".

Rules for this file:
- Never guess a price. A model, tier, modality or tool charge that is not listed here is
  `price_missing`, never priced at a neighbour's rate.
- Only models whose Standard text price is flat (no long-context tier on the pricing page,
  no cache-write price) are listed, because `model_prices` stores one standard-tier price
  per model (ARCHITECTURE.md §4.7).
- An alias and its dated snapshot are separate rows. A snapshot is listed only where the
  model page names it as the alias's default snapshot under the same prices; its
  `source_url` is that model page.
- `effective_from` = `verified_at` = 2026-10-24T21:40:00Z, just after the last check:
  there is no evidence for earlier instants, so older events stay `price_missing`. All
  times are UTC (a local-date timestamp would claim hours without evidence).
- Rows are never edited. A price change is a new catalogue version with a later
  `effective_from` (the table is append-only, migration `0002`).

Synthetic test prices never live here; they are inserted by tests only and labelled
`synthetic-*`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

VERSION = "openai-2026-10-24"
VERIFIED_AT = datetime(2026, 10, 24, 21, 40, tzinfo=UTC)
EFFECTIVE_FROM = VERIFIED_AT
PRICING_URL = "https://developers.openai.com/api/docs/pricing"
MODEL_PAGE = "https://developers.openai.com/api/docs/models/{model}"


@dataclass(frozen=True)
class CatalogEntry:
    provider: str
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None
    cache_write_input_per_million: Decimal | None
    source_url: str
    version: str = VERSION
    effective_from: datetime = EFFECTIVE_FROM
    verified_at: datetime = VERIFIED_AT
    currency: str = "USD"


def _model(
    alias: str, snapshot: str, input_: str, output: str, cached: str
) -> tuple[CatalogEntry, CatalogEntry]:
    prices = {
        "input_per_million": Decimal(input_),
        "output_per_million": Decimal(output),
        "cached_input_per_million": Decimal(cached),
        "cache_write_input_per_million": None,  # no cache-write price listed
    }
    return (
        CatalogEntry("openai", alias, source_url=PRICING_URL, **prices),
        CatalogEntry("openai", snapshot, source_url=MODEL_PAGE.format(model=alias), **prices),
    )


ENTRIES: tuple[CatalogEntry, ...] = (
    # Current model (no deprecation notice on 2026-10-24). Max input 272,000 tokens, so the
    # >272K long-context rates of the larger GPT-5.4 models cannot apply.
    *_model("gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "0.75", "4.50", "0.075"),
    # Reasoning model; deprecated, shutdown announced for 2026-12-11.
    *_model("gpt-5-mini", "gpt-5-mini-2025-08-07", "0.25", "2.00", "0.025"),
    # Non-reasoning model; deprecated, shutdown announced for 2027-01-14.
    *_model("gpt-4.1-mini", "gpt-4.1-mini-2025-04-14", "0.40", "1.60", "0.10"),
)
