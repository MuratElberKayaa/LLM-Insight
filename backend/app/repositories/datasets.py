"""Dataset and test case queries (ARCHITECTURE.md §4.8, §4.10, Phase 4B).

`test_cases` has no `project_id`: every case statement joins or filters through its dataset,
whose `project_id` scopes the request. Bound parameters only (SECURITY.md §3).
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import TestCase, TestDataset

CASE_COLUMNS = (
    TestCase.id,
    TestCase.dataset_id,
    TestCase.origin_trace_id,
    TestCase.origin_review_id,
    TestCase.sanitized_input,
    TestCase.expected_output,
    TestCase.rule,
    TestCase.redaction_applied,
    TestCase.approved_by,
    TestCase.approved_at,
    TestCase.created_at,
)


def _case_count():  # noqa: ANN202 - a correlated scalar subquery
    return (
        select(func.count())
        .where(TestCase.dataset_id == TestDataset.id)
        .scalar_subquery()
        .label("case_count")
    )


def list_datasets(session: Session, project_id: int, limit: int) -> Sequence[Row[Any]]:
    stmt = (
        select(TestDataset.id, TestDataset.name, TestDataset.created_at, _case_count())
        .where(TestDataset.project_id == project_id)
        .order_by(TestDataset.id)
        .limit(limit + 1)
    )
    return session.execute(stmt).all()


def get_dataset(session: Session, project_id: int, dataset_id: int) -> Row[Any] | None:
    stmt = select(TestDataset.id, TestDataset.name, TestDataset.created_at, _case_count()).where(
        TestDataset.project_id == project_id, TestDataset.id == dataset_id
    )
    return session.execute(stmt).one_or_none()


def insert_dataset(session: Session, project_id: int, name: str) -> Row[Any]:
    stmt = (
        insert(TestDataset)
        .values(project_id=project_id, name=name)
        .returning(TestDataset.id, TestDataset.name, TestDataset.created_at)
    )
    return session.execute(stmt).one()


def lock_review_conversion(session: Session, dataset_id: int, review_id: int) -> None:
    """Serialize conversions of one review into one dataset until the transaction ends, so
    the duplicate check below cannot race (decision D5; the pattern of migration 0002)."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"llminsight.test_case:{dataset_id}:{review_id}"},
    )


def case_exists_for_review(session: Session, dataset_id: int, review_id: int) -> int | None:
    stmt = (
        select(TestCase.id)
        .where(TestCase.dataset_id == dataset_id, TestCase.origin_review_id == review_id)
        .order_by(TestCase.id)
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def insert_case(session: Session, values: dict[str, Any]) -> Row[Any]:
    stmt = insert(TestCase).values(approved_at=func.now(), **values).returning(*CASE_COLUMNS)
    return session.execute(stmt).one()


def list_cases(
    session: Session, dataset_id: int, after: int | None, limit: int
) -> Sequence[Row[Any]]:
    stmt = select(*CASE_COLUMNS).where(TestCase.dataset_id == dataset_id)
    if after is not None:
        stmt = stmt.where(TestCase.id > after)
    return session.execute(stmt.order_by(TestCase.id).limit(limit + 1)).all()


def dataset_exists(session: Session, project_id: int, dataset_id: int) -> bool:
    stmt = select(
        exists().where(TestDataset.project_id == project_id, TestDataset.id == dataset_id)
    )
    return session.execute(stmt).scalar_one()
