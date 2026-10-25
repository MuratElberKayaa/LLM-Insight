"""Failure review queries (ARCHITECTURE.md §4.9, Phase 4B).

Every statement is filtered by `project_id` and built from SQLAlchemy expressions with bound
parameters (SECURITY.md §3). Reviews of a trace and the per-trace counts of a list page use
`ix_failure_reviews_project_id_trace_id`; linked test cases are reached through the review id.
Nothing here reads or writes telemetry: traces and spans are only checked for existence.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import FailureReview, Span, TestCase, TestDataset, Trace

REVIEW_COLUMNS = (
    FailureReview.id,
    FailureReview.trace_id,
    FailureReview.span_id,
    FailureReview.issue_type,
    FailureReview.evidence,
    FailureReview.hypothesis,
    FailureReview.review_status,
    FailureReview.reviewer_decision,
    FailureReview.created_by,
    FailureReview.created_at,
    FailureReview.updated_at,
)


def trace_exists(session: Session, project_id: int, trace_id: str) -> bool:
    stmt = select(exists().where(Trace.project_id == project_id, Trace.trace_id == trace_id))
    return session.execute(stmt).scalar_one()


def span_exists(session: Session, project_id: int, trace_id: str, span_id: str) -> bool:
    stmt = select(
        exists().where(
            Span.project_id == project_id, Span.trace_id == trace_id, Span.span_id == span_id
        )
    )
    return session.execute(stmt).scalar_one()


def insert_review(session: Session, project_id: int, values: dict[str, Any]) -> Row[Any]:
    stmt = insert(FailureReview).values(project_id=project_id, **values).returning(*REVIEW_COLUMNS)
    return session.execute(stmt).one()


def get_review(
    session: Session, project_id: int, review_id: int, *, lock: str | None = None
) -> Row[Any] | None:
    stmt = select(*REVIEW_COLUMNS).where(
        FailureReview.project_id == project_id, FailureReview.id == review_id
    )
    if lock == "update":
        stmt = stmt.with_for_update()
    elif lock == "share":
        stmt = stmt.with_for_update(read=True)
    return session.execute(stmt).one_or_none()


def review_has_cases(session: Session, review_id: int) -> bool:
    """Whether any test case cites the review (its decision is then frozen, ADR-24)."""
    stmt = select(exists().where(TestCase.origin_review_id == review_id))
    return session.execute(stmt).scalar_one()


def update_review(
    session: Session, project_id: int, review_id: int, status: str, decision: str
) -> Row[Any]:
    stmt = (
        update(FailureReview)
        .where(FailureReview.project_id == project_id, FailureReview.id == review_id)
        .values(review_status=status, reviewer_decision=decision, updated_at=func.now())
        .returning(*REVIEW_COLUMNS)
    )
    return session.execute(stmt).one()


def reviews_for_trace(
    session: Session, project_id: int, trace_id: str, limit: int
) -> Sequence[Row[Any]]:
    """At most `limit + 1` reviews (the extra row only signals truncation), oldest first."""
    stmt = (
        select(*REVIEW_COLUMNS)
        .where(FailureReview.project_id == project_id, FailureReview.trace_id == trace_id)
        .order_by(FailureReview.created_at, FailureReview.id)
        .limit(limit + 1)
    )
    return session.execute(stmt).all()


def cases_for_reviews(
    session: Session, project_id: int, review_ids: Sequence[int], limit: int
) -> Sequence[Row[Any]]:
    """Test cases converted from these reviews, with their dataset, at most `limit` rows."""
    if not review_ids:
        return []
    stmt = (
        select(
            TestCase.id,
            TestCase.origin_review_id,
            TestCase.dataset_id,
            TestDataset.name.label("dataset_name"),
        )
        .join(TestDataset, TestDataset.id == TestCase.dataset_id)
        .where(TestDataset.project_id == project_id, TestCase.origin_review_id.in_(review_ids))
        .order_by(TestCase.origin_review_id, TestCase.id)
        .limit(limit)
    )
    return session.execute(stmt).all()


def review_counts(
    session: Session, project_id: int, trace_ids: Sequence[str]
) -> dict[str, tuple[int, int]]:
    """(total, not closed) reviews per trace id of one list page; absent traces have none."""
    if not trace_ids:
        return {}
    stmt = (
        select(
            FailureReview.trace_id,
            func.count().label("total"),
            func.count().filter(FailureReview.review_status != "closed").label("not_closed"),
        )
        .where(FailureReview.project_id == project_id, FailureReview.trace_id.in_(trace_ids))
        .group_by(FailureReview.trace_id)
    )
    return {r.trace_id: (int(r.total), int(r.not_closed)) for r in session.execute(stmt)}
