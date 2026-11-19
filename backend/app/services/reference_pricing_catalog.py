"""List-price reference catalogue `reference-2026-11-19` (ADR-52, docs/PRICING.md §9).

**Not ledger data.** These prices are never written to `usage_records` and never produce a
`computed` cost. ADR-35 and ADR-40 still hold: no stored price applies to `gcp.gemini`, `groq`
or `openrouter`. The reference answers one question at read time: what the observed,
provider-reported tokens would cost at the provider's published price. It is labelled
"reference, not billed".

Evidence: both pages were read on 2026-11-19 through the page fetcher and again as raw HTML
(downloaded 2026-11-19T12:51:42Z–12:51:43Z, HTTP 200):
- Gemini: https://aistudio.google.com/docs/pricing (the redirect target of
  `ai.google.dev/gemini-api/docs/pricing`), section "Gemini 3.1 Flash-Lite", tab "Standard",
  column "Paid Tier, per 1M tokens in USD": input "$0.25 (text / image / video)", output
  "$1.50" under "Output price (including thinking tokens)", context caching
  "$0.025 (text / image / video)".
- Groq: https://console.groq.com/docs/model/openai/gpt-oss-20b, the price card: Input
  "$0.075", Cached Input "$0.037", Output "$0.30".

Rules for this file:
- Never guess a price. A model that is not listed here has no reference.
- Only the rates named above. Gemini audio input (0.50), Batch, Flex and Priority, the
  per-hour cache storage price and Groq Batch/Flex are not applied.
- Groq's page shows `$` with no ISO code. It is recorded as USD on the same basis as the
  OpenAI catalogue (`PRICING.md` §2), and the basis says so.
- No OpenAI row (its cost is in the ledger, ADR-11) and no OpenRouter row (its only allowed
  model is `:free`, price 0).
- Rows are never edited. A price change is a new catalogue version in a new commit.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

VERSION = "reference-2026-11-19"
VERIFIED_AT = datetime(2026, 11, 19, 12, 51, 42, tzinfo=UTC)  # the raw-HTML read
STALE_AFTER = timedelta(days=30)  # shown with a warning after this; still computed


def _require_price(name: str, value: object, *, optional: bool) -> None:
    if value is None and optional:
        return
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise TypeError(f"{name}: prices must be finite, non-negative Decimal values")


@dataclass(frozen=True)
class ReferencePrice:
    provider: str
    model: str
    tier: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None  # None = not listed: cached tokens make it unavailable
    source_url: str
    basis: str
    version: str = VERSION
    verified_at: datetime = VERIFIED_AT
    currency: str = "USD"

    def __post_init__(self) -> None:
        _require_price("input_per_million", self.input_per_million, optional=False)
        _require_price("output_per_million", self.output_per_million, optional=False)
        _require_price("cached_input_per_million", self.cached_input_per_million, optional=True)


ENTRIES: tuple[ReferencePrice, ...] = (
    ReferencePrice(
        provider="gcp.gemini",
        model="gemini-3.1-flash-lite",
        tier="paid_standard",
        input_per_million=Decimal("0.25"),
        output_per_million=Decimal("1.50"),
        cached_input_per_million=Decimal("0.025"),
        source_url="https://aistudio.google.com/docs/pricing",
        basis=(
            "Paid Tier, Standard tab, per 1M tokens in USD; text/image/video input rate "
            "(audio input is 0.50 and not applied); output price includes thinking tokens"
        ),
    ),
    ReferencePrice(
        provider="groq",
        model="openai/gpt-oss-20b",
        tier="on_demand",
        input_per_million=Decimal("0.075"),
        output_per_million=Decimal("0.30"),
        cached_input_per_million=Decimal("0.037"),
        source_url="https://console.groq.com/docs/model/openai/gpt-oss-20b",
        basis=(
            "Model page price card (Input, Cached Input, Output); the page shows '$' with no "
            "ISO code, recorded as USD; Batch and Flex not applied"
        ),
    ),
)
