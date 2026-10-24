"""The verified catalogue (app.services.pricing_catalog) is well-formed, contains no
synthetic or guessed rows, and equals the table in docs/PRICING.md §2 value for value."""

import re
from datetime import UTC, datetime
from decimal import Decimal

from app.models.pricing import MODEL_FORMAT, PROVIDER_FORMAT
from app.services import pricing_catalog
from app.services.pricing_catalog import ENTRIES
from tests.helpers import REPO_ROOT

ALLOWED_SOURCES = re.compile(
    r"https://developers\.openai\.com/api/docs/(pricing|models/[a-z0-9.-]+)"
)


def _doc_rows() -> list[list[str]]:
    doc = (REPO_ROOT / "docs" / "PRICING.md").read_text(encoding="utf-8")
    block = doc.split("## 2. Verified catalogue", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in block.splitlines() if ln.startswith("| openai |")]
    return [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]


def _dec(cell: str) -> Decimal | None:
    return None if cell == "—" else Decimal(cell)


def test_catalogue_matches_the_documented_table_exactly():
    rows = _doc_rows()
    assert len(rows) == len(ENTRIES) == 6
    for e, (provider, model, inp, cached, cw, out, eff, source) in zip(ENTRIES, rows, strict=True):
        assert (e.provider, e.model) == (provider, model)
        assert (e.input_per_million, e.cached_input_per_million) == (_dec(inp), _dec(cached))
        assert (e.cache_write_input_per_million, e.output_per_million) == (_dec(cw), _dec(out))
        assert e.effective_from == datetime.fromisoformat(eff.replace("Z", "+00:00"))
        assert e.source_url == source


def test_entries_are_well_formed():
    identities = {(e.provider, e.model, e.version) for e in ENTRIES}
    starts = {(e.provider, e.model, e.effective_from) for e in ENTRIES}
    assert len(identities) == len(starts) == len(ENTRIES)  # both UNIQUE constraints hold
    for e in ENTRIES:
        assert re.fullmatch(PROVIDER_FORMAT.strip("^$"), e.provider)
        assert re.fullmatch(MODEL_FORMAT.strip("^$"), e.model)
        assert e.currency == "USD"
        assert e.version == pricing_catalog.VERSION and len(e.version) <= 64
        assert e.effective_from.tzinfo is UTC and e.verified_at.tzinfo is UTC
        assert e.effective_from >= e.verified_at  # never back-dated before the evidence
        for value in (e.input_per_million, e.output_per_million, e.cached_input_per_million):
            assert isinstance(value, Decimal) and value >= 0
            assert -value.as_tuple().exponent <= 10  # fits NUMERIC(20,10) without rounding
        assert e.cache_write_input_per_million is None  # none listed for these models


def test_only_official_openai_sources_and_no_synthetic_rows():
    for e in ENTRIES:
        assert e.provider == "openai"
        assert ALLOWED_SOURCES.fullmatch(e.source_url), e.source_url
        assert "synthetic" not in f"{e.model} {e.version} {e.source_url}"


def test_snapshots_cite_their_model_page_and_share_the_alias_prices():
    by_model = {e.model: e for e in ENTRIES}
    for alias, snapshot in (
        ("gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"),
        ("gpt-5-mini", "gpt-5-mini-2025-08-07"),
        ("gpt-4.1-mini", "gpt-4.1-mini-2025-04-14"),
    ):
        a, s = by_model[alias], by_model[snapshot]
        assert a.source_url == pricing_catalog.PRICING_URL
        assert s.source_url == pricing_catalog.MODEL_PAGE.format(model=alias)
        prices = ("input_per_million", "output_per_million", "cached_input_per_million")
        assert all(getattr(a, p) == getattr(s, p) for p in prices)


def test_models_with_tiers_the_schema_cannot_represent_are_absent():
    """Long-context or cache-write-priced models must stay price_missing (docs/PRICING.md §2)."""
    models = {e.model for e in ENTRIES}
    for absent in ("gpt-5.4", "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-terra", "gpt-6-luna", "gpt-4o"):
        assert absent not in models


def test_catalogue_is_never_dated_in_the_future():
    """Regression: a first draft stamped the catalogue with the *local* date (UTC+3) at
    00:00Z, hours after the real verification, so live calls were priced as `price_missing`
    and the rows claimed evidence that did not exist yet. All catalogue times are UTC and
    no later than the clock that runs the tests."""
    now = datetime.now(UTC)
    assert now >= pricing_catalog.VERIFIED_AT
    assert all(e.effective_from <= now and e.verified_at <= now for e in ENTRIES)
