"""ARCHITECTURE.md §4.8 `test_datasets` and §4.10 `test_cases`, Phase 4B.

A test case exists only with explicit human approval (`approved_by`, `approved_at`) and after
server-side redaction of its input (`redaction_applied`), both enforced by the API (T34, T35).
Its content is typed by a human: telemetry never stored prompts or completions (ADR-19), so
nothing is copied from a trace. `origin_trace_id` is a soft reference (lineage survives
retention, §4.1); `origin_review_id` keeps the ADR-12 lineage review -> case and becomes NULL
if the review is ever deleted.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex32, length

DATASET_NAME_MAX = 100
SANITIZED_INPUT_MAX = 8000
RULE_TYPES = ("json_schema", "field_match", "exact", "regex")


class TestDataset(Base):
    __tablename__ = "test_datasets"
    __test__ = False  # not a pytest class, despite the name
    __table_args__ = (
        length("name", DATASET_NAME_MAX),
        UniqueConstraint("project_id", "name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False
    __table_args__ = (
        hex32("origin_trace_id"),
        length("sanitized_input", SANITIZED_INPUT_MAX),
        # §4.10: `rule->>'type' IN (...)`. A missing type would make the bare IN expression
        # NULL, which a CHECK accepts, so the absent case is mapped to a refused value.
        CheckConstraint(
            "COALESCE(rule->>'type', '') IN (" + ", ".join(f"'{t}'" for t in RULE_TYPES) + ")",
            name="rule_type_enum",
        ),
        length("approved_by", 100),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("test_datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_review_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("failure_reviews.id", ondelete="SET NULL"), nullable=True
    )
    sanitized_input: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    rule: Mapped[Any] = mapped_column(JSONB, nullable=False)
    redaction_applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approved_by: Mapped[str] = mapped_column(Text, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
