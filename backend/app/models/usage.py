"""ARCHITECTURE.md §4.6 `usage_records`: one row per physical inference attempt.

`model_price_id` references `model_prices` (§4.7). Migration `0001` created the column
without its FK; migration `0002` (Phase 2B) creates `model_prices` and adds the FK
(ON DELETE RESTRICT), so a price row that was applied can never disappear.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex16, hex32, length, max_length, non_negative, one_of

CALL_KEY_SOURCES = ("provider_response_id", "sdk_attempt", "span_fallback")
OPERATION_NAMES = ("chat", "text_completion", "generate_content", "embeddings")
USAGE_SOURCES = ("provider_reported", "estimated", "unknown")
COST_STATUSES = ("computed", "unknown")
COST_UNKNOWN_REASONS = ("price_missing", "usage_missing", "usage_inconsistent")
USAGE_PROVENANCE = ("real", "synthetic", "unknown")

TOKEN_COLUMNS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        length("canonical_call_key", 300),
        one_of("call_key_source", CALL_KEY_SOURCES),
        hex32("trace_id"),
        hex16("span_id"),
        max_length("call_id", 64),
        CheckConstraint("attempt_id >= 1", name="attempt_id_positive"),
        max_length("provider_response_id", 256),
        one_of("operation_name", OPERATION_NAMES),
        length("provider", 64),
        max_length("requested_model", 256),
        max_length("response_model", 256),
        *(non_negative(c) for c in TOKEN_COLUMNS),
        one_of("usage_source", USAGE_SOURCES),
        non_negative("cost_usd"),
        one_of("cost_status", COST_STATUSES),
        one_of("cost_unknown_reason", COST_UNKNOWN_REASONS),
        one_of("provenance", USAGE_PROVENANCE),
        # Unknown cost is never stored as 0 (ADR-11).
        CheckConstraint(
            "(cost_status = 'computed') = (cost_usd IS NOT NULL)", name="cost_computed_has_value"
        ),
        CheckConstraint(
            "(cost_status = 'unknown') = (cost_unknown_reason IS NOT NULL)",
            name="cost_unknown_has_reason",
        ),
        CheckConstraint(
            "cost_status <> 'computed'"
            " OR (price_version IS NOT NULL AND model_price_id IS NOT NULL)",
            name="cost_computed_has_price",
        ),
        ForeignKeyConstraint(
            ["project_id", "trace_id", "span_id"],
            ["spans.project_id", "spans.trace_id", "spans.span_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["model_price_id"], ["model_prices.id"], ondelete="RESTRICT"),
        UniqueConstraint("project_id", "canonical_call_key"),
        UniqueConstraint("project_id", "trace_id", "span_id"),
        Index(
            "ix_usage_records_project_created_provider_model",
            "project_id",
            "created_at",
            "provider",
            "response_model",
        ),
        Index("ix_usage_records_project_id_started_at", "project_id", "started_at"),
        Index(
            "ix_usage_records_project_id_call_id",
            "project_id",
            "call_id",
            postgresql_where=text("call_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    canonical_call_key: Mapped[str] = mapped_column(Text, nullable=False)
    call_key_source: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    span_id: Mapped[str] = mapped_column(Text, nullable=False)
    call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    requested_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_write_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_source: Mapped[str] = mapped_column(Text, nullable=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    cost_status: Mapped[str] = mapped_column(Text, nullable=False)
    cost_unknown_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_price_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
