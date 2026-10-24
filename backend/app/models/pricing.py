"""ARCHITECTURE.md §4.7 `model_prices`: the global, versioned price catalogue (Phase 2B).

A row is one standard-tier text price of one exact model, valid from `effective_from` until
the next row of the same (provider, model) starts. `UNIQUE(provider, model, effective_from)`
therefore leaves no instant with two candidate prices. The table is append-only: migration
`0002` adds triggers that reject UPDATE, DELETE, TRUNCATE and back-dated INSERTs, so a stored
price is never rewritten (`docs/PRICING.md`).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, length, non_negative

PRICE_COLUMNS = (
    "input_per_million",
    "output_per_million",
    "cached_input_per_million",
    "cache_write_input_per_million",
)
# The same identifier formats the ingest accepts (TELEMETRY_CONTRACT.md §3.1), so a stored
# model name can never be a pattern ("no wildcard").
MODEL_FORMAT = "^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"
PROVIDER_FORMAT = "^[A-Za-z0-9][A-Za-z0-9._-]*$"


class ModelPrice(Base):
    __tablename__ = "model_prices"
    __table_args__ = (
        length("version", 64),
        length("provider", 64),
        length("model", 256),
        CheckConstraint(f"provider ~ '{PROVIDER_FORMAT}'", name="provider_format"),
        CheckConstraint(f"model ~ '{MODEL_FORMAT}'", name="model_format"),
        CheckConstraint("currency = 'USD'", name="currency_usd"),
        *(non_negative(c) for c in PRICE_COLUMNS),
        CheckConstraint("source_url LIKE 'https://%'", name="source_url_https"),
        UniqueConstraint("provider", "model", "version"),
        UniqueConstraint("provider", "model", "effective_from"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'USD'"))
    input_per_million: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    output_per_million: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    cached_input_per_million: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    cache_write_input_per_million: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
