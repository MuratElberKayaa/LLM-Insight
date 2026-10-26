"""`model_prices` access: price lookup for ingest and the append-only catalogue seed.

The seed never updates or deletes. For each catalogue entry it inserts the row, skips it
when an identical row exists, and refuses (raising `CatalogConflict`, nothing written) when
a stored row with the same identity differs. The table's triggers enforce the same
append-only rule for every other writer (migration `0002`).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import datetime

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.orm import Session

from app.models import ModelPrice
from app.services.pricing import Price
from app.services.pricing_catalog import CatalogEntry

_COMPARED = tuple(
    f.name for f in fields(CatalogEntry) if f.name not in ("provider", "model", "version")
)


class CatalogConflict(Exception):
    """A catalogue entry disagrees with a stored price row (the stored row is kept)."""


@dataclass(frozen=True)
class SeedResult:
    inserted: list[tuple[str, str, str]]
    unchanged: list[tuple[str, str, str]]


def load_prices(session: Session, pairs: Iterable[tuple[str, str]]) -> list[Price]:
    """Every stored price of the given (provider, model) pairs; one query per batch."""
    wanted = sorted(set(pairs))
    if not wanted:
        return []
    m = ModelPrice
    stmt = select(
        m.id,
        m.version,
        m.provider,
        m.model,
        m.effective_from,
        m.input_per_million,
        m.output_per_million,
        m.cached_input_per_million,
        m.cache_write_input_per_million,
    ).where(tuple_(m.provider, m.model).in_(wanted))
    return [Price(**r._asdict()) for r in session.execute(stmt)]


def prices_of_provider(session: Session, provider: str, limit: int) -> list[Price]:
    """Stored prices of one provider, at most `limit + 1` rows (Phase 4D: the models an
    experiment can price). The caller picks the row in force with `pricing.select_price`."""
    m = ModelPrice
    stmt = (
        select(
            m.id,
            m.version,
            m.provider,
            m.model,
            m.effective_from,
            m.input_per_million,
            m.output_per_million,
            m.cached_input_per_million,
            m.cache_write_input_per_million,
        )
        .where(m.provider == provider)
        .order_by(m.model, m.effective_from)
        .limit(limit + 1)
    )
    return [Price(**r._asdict()) for r in session.execute(stmt)]


def database_now(session: Session) -> datetime:
    """The database clock (`now()`), which also stamps `experiments.started_at`."""
    return session.execute(select(func.now())).scalar_one()


def _identity(e: CatalogEntry) -> tuple[str, str, str]:
    return (e.provider, e.model, e.version)


def seed(session: Session, entries: Sequence[CatalogEntry], *, dry_run: bool) -> SeedResult:
    """Insert missing catalogue rows inside the caller's transaction. `dry_run` checks and
    reports without writing. Raises `CatalogConflict` before writing anything."""
    session.execute(text("LOCK TABLE model_prices IN SHARE ROW EXCLUSIVE MODE"))
    stored = {
        (r.provider, r.model, r.version): r
        for r in session.scalars(
            select(ModelPrice).where(
                tuple_(ModelPrice.provider, ModelPrice.model).in_(
                    sorted({(e.provider, e.model) for e in entries})
                )
            )
        )
    }
    by_start = {(r.provider, r.model, r.effective_from): r for r in stored.values()}
    inserted, unchanged = [], []
    for e in entries:
        row = stored.get(_identity(e))
        if row is None:
            clash = by_start.get((e.provider, e.model, e.effective_from))
            if clash is not None:
                raise CatalogConflict(
                    f"{e.provider}/{e.model}: version {clash.version} already starts at "
                    f"{e.effective_from.isoformat()}"
                )
            inserted.append(_identity(e))
        elif all(getattr(row, f) == getattr(e, f) for f in _COMPARED):
            unchanged.append(_identity(e))
        else:
            raise CatalogConflict(
                f"{e.provider}/{e.model} {e.version}: the stored row differs from the "
                "catalogue; stored prices are never changed (add a new version instead)"
            )
    if not dry_run:
        new = [e for e in entries if _identity(e) in set(inserted)]
        session.add_all(
            ModelPrice(
                version=e.version,
                provider=e.provider,
                model=e.model,
                effective_from=e.effective_from,
                currency=e.currency,
                input_per_million=e.input_per_million,
                output_per_million=e.output_per_million,
                cached_input_per_million=e.cached_input_per_million,
                cache_write_input_per_million=e.cache_write_input_per_million,
                source_url=e.source_url,
                verified_at=e.verified_at,
            )
            for e in sorted(new, key=lambda e: (e.provider, e.model, e.effective_from))
        )
        session.flush()
    return SeedResult(inserted, unchanged)
