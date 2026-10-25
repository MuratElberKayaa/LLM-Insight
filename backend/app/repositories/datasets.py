"""Dataset and test case queries (ARCHITECTURE.md §4.8, §4.10, Phase 4B).

`test_cases` has no `project_id`: every case statement joins or filters through its dataset,
whose `project_id` scopes the request. Bound parameters only (SECURITY.md §3).

Phase 4C: `expected_output` and `rule` are written from exact JSON text and read back as
`jsonb::text` (`*_json` columns), so a number keeps its exact digits end to end; psycopg's
default `jsonb` loader would turn non-integers into binary floats.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, String, Text, bindparam, cast, exists, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Session

from app.models import TestCase, TestDataset

CASE_COLUMNS = (
    TestCase.id,
    TestCase.dataset_id,
    TestCase.origin_trace_id,
    TestCase.origin_review_id,
    TestCase.sanitized_input,
    cast(TestCase.expected_output, Text).label("expected_output_json"),
    cast(TestCase.rule, Text).label("rule_json"),
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


def _jsonb(name: str, text_value: str | None) -> Any:
    """`CAST(:text AS jsonb)`: PostgreSQL parses the exact JSON text itself."""
    if text_value is None:
        return None
    return cast(bindparam(name, text_value, type_=String), JSONB)


def insert_case(
    session: Session, values: dict[str, Any], expected_json: str | None, rule_json: str
) -> Row[Any]:
    """`values` holds the plain columns; `expected_json` / `rule_json` are exact JSON text
    (`app.core.exact_json.dumps`)."""
    stmt = (
        insert(TestCase)
        .values(
            approved_at=func.now(),
            expected_output=_jsonb("expected_json", expected_json),
            rule=_jsonb("rule_json", rule_json),
            **values,
        )
        .returning(*CASE_COLUMNS)
    )
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
