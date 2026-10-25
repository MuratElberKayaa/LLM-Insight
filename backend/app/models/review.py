"""ARCHITECTURE.md §4.9 `failure_reviews` (CR-03), Phase 4B.

A review is human work: it is written only by `POST /api/v1/failure-reviews` and changed only
by `PATCH /api/v1/failure-reviews/{id}`. It references its trace softly (no FK), so the
lineage trace -> review -> test case survives telemetry retention (§4.1); the API checks the
trace exists in the project at insert time. `hypothesis_supported` means the evidence supports
the hypothesis, never that a root cause is proven (ADR-10).

Phase 4C (migration 0004, ADR-24): the trigger `failure_reviews_integrity` keeps every column
except `review_status`, `reviewer_decision` and `updated_at` immutable, and freezes
`reviewer_decision` once a test case cites the review.
"""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, hex16, hex32, length, one_of

ISSUE_TYPES = (
    "retrieval_missing",
    "chunk_boundary",
    "prompt_instruction",
    "generation_mismatch",
    "schema_violation",
    "unknown",
)
REVIEW_STATUSES = ("open", "in_review", "closed")
REVIEWER_DECISIONS = ("pending", "hypothesis_supported", "hypothesis_rejected", "inconclusive")
EVIDENCE_MAX = 4000
HYPOTHESIS_MAX = 2000
ACTOR_MAX = 100  # created_by / approved_by: self-declared, there is no user auth in the MVP

OPEN_REVIEW_INDEX = "uq_failure_reviews_project_id_trace_id_issue_type"
# Raised by the migration-0004 trigger (`RAISE ... CONSTRAINT`), reported like constraints.
DECISION_FROZEN = "failure_reviews_decision_frozen"
REVIEW_IMMUTABLE = "failure_reviews_immutable"


class FailureReview(Base):
    __tablename__ = "failure_reviews"
    __table_args__ = (
        hex32("trace_id"),
        hex16("span_id"),
        one_of("issue_type", ISSUE_TYPES),
        length("evidence", EVIDENCE_MAX),
        length("hypothesis", HYPOTHESIS_MAX),
        one_of("review_status", REVIEW_STATUSES),
        one_of("reviewer_decision", REVIEWER_DECISIONS),
        length("created_by", ACTOR_MAX),
        CheckConstraint(
            "review_status <> 'closed' OR reviewer_decision <> 'pending'",
            name="closed_needs_decision",
        ),
        Index("ix_failure_reviews_project_id_trace_id", "project_id", "trace_id"),
        # At most one review per (trace, issue type) that is not closed: a duplicate is 409.
        Index(
            OPEN_REVIEW_INDEX,
            "project_id",
            "trace_id",
            "issue_type",
            unique=True,
            postgresql_where=text("review_status <> 'closed'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    span_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_type: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    reviewer_decision: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
