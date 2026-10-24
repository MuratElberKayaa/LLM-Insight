"""ARCHITECTURE.md §4.4 `traces` (derived summary) and §4.5 `spans`."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    hex16,
    hex32,
    json_object,
    length,
    max_length,
    non_negative,
    one_of,
)

TRACE_STATUSES = ("ok", "error")
TRACE_PROVENANCE = ("real", "synthetic", "mixed", "unknown")
SPAN_KINDS = ("UNSPECIFIED", "INTERNAL", "SERVER", "CLIENT", "PRODUCER", "CONSUMER")
SPAN_STATUSES = ("UNSET", "OK", "ERROR")
SPAN_PROVENANCE = ("real", "synthetic", "unknown")


class Trace(Base):
    __tablename__ = "traces"
    __table_args__ = (
        hex32("trace_id"),
        length("service_name", 256),
        CheckConstraint("start_ns > 0", name="start_ns_positive"),
        CheckConstraint("end_ns >= start_ns", name="end_after_start"),
        one_of("status", TRACE_STATUSES),
        one_of("provenance", TRACE_PROVENANCE),
        CheckConstraint("span_count >= 1", name="span_count_positive"),
        UniqueConstraint("project_id", "trace_id"),
        Index("ix_traces_project_id_started_at", "project_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown_service'")
    )
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    span_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Span(Base):
    __tablename__ = "spans"
    __table_args__ = (
        hex32("trace_id"),
        hex16("span_id"),
        hex16("parent_span_id"),
        CheckConstraint("parent_span_id <> span_id", name="parent_not_self"),
        length("name", 256),
        one_of("kind", SPAN_KINDS),
        CheckConstraint("start_ns > 0", name="start_ns_positive"),
        CheckConstraint("end_ns >= start_ns", name="end_after_start"),
        non_negative("duration_ns"),
        one_of("status", SPAN_STATUSES),
        max_length("status_message", 256),
        max_length("error_type", 256),
        max_length("scope_name", 256),
        max_length("scope_version", 64),
        json_object("resource_attributes"),
        json_object("attributes"),
        one_of("provenance", SPAN_PROVENANCE),
        non_negative("server_dropped_attributes_count"),
        # Composite FK to the derived trace row; a missing parent span is an orphan (no FK).
        ForeignKeyConstraint(
            ["project_id", "trace_id"],
            ["traces.project_id", "traces.trace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "trace_id", "span_id"),
        Index("ix_spans_project_id_trace_id_start_ns", "project_id", "trace_id", "start_ns"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    span_id: Mapped[str] = mapped_column(Text, nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    start_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_ns: Mapped[int] = mapped_column(
        BigInteger, Computed("end_ns - start_ns", persisted=True), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    attributes_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'unknown'"))
    server_dropped_attributes_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
