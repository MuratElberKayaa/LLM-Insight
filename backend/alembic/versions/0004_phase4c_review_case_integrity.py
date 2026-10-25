"""Phase 4C Gate A: review and test-case integrity (ADR-24). No column is added or changed.

1. Partial unique index `uq_test_cases_dataset_id_origin_review_id` on
   `test_cases(dataset_id, origin_review_id) WHERE origin_review_id IS NOT NULL`: at most one
   case per (dataset, review) (decision D5, now also in the database). Standalone cases
   (no review) are not affected.
2. Trigger `failure_reviews_integrity` (BEFORE UPDATE): every column other than
   `review_status`, `reviewer_decision` and `updated_at` is immutable, and `reviewer_decision`
   cannot change once a test case cites the review.
3. Trigger `test_cases_review_eligibility` (BEFORE INSERT / UPDATE of the reference columns):
   a case may cite a review only when the review's decision is `hypothesis_supported`, or
   `hypothesis_rejected` with a non-NULL `expected_output`, the review is of the case's
   `origin_trace_id` and belongs to the dataset's project. The review row is read `FOR SHARE`,
   so a concurrent decision change waits for the insert (and then sees the case), or the
   insert waits for the change (and then sees the new decision).

The triggers raise SQLSTATE 23000 with a constraint name, which the API maps to `409`.

Before anything is created, existing rows are checked: the upgrade refuses (and changes
nothing) when two cases already cite the same review in one dataset, or when an existing case
cites a review that the new rules would not accept. Such rows are reported by count only and
must be resolved by a person; they are never rewritten silently.

Downgrade drops the index, triggers and functions only; no data is touched.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CASE_REVIEW_INDEX = "uq_test_cases_dataset_id_origin_review_id"

_DUPLICATES = """
SELECT count(*) FROM (
    SELECT dataset_id, origin_review_id FROM test_cases
    WHERE origin_review_id IS NOT NULL
    GROUP BY dataset_id, origin_review_id HAVING count(*) > 1
) AS d
"""

_INELIGIBLE = """
SELECT count(*) FROM test_cases c
JOIN failure_reviews r ON r.id = c.origin_review_id
JOIN test_datasets d ON d.id = c.dataset_id
WHERE r.reviewer_decision NOT IN ('hypothesis_supported', 'hypothesis_rejected')
   OR (r.reviewer_decision = 'hypothesis_rejected' AND c.expected_output IS NULL)
   OR c.origin_trace_id IS DISTINCT FROM r.trace_id
   OR r.project_id <> d.project_id
"""

_REVIEW_FUNCTION = """
CREATE FUNCTION failure_reviews_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
       OR NEW.span_id IS DISTINCT FROM OLD.span_id
       OR NEW.issue_type IS DISTINCT FROM OLD.issue_type
       OR NEW.evidence IS DISTINCT FROM OLD.evidence
       OR NEW.hypothesis IS DISTINCT FROM OLD.hypothesis
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'a failure review''s content and references are immutable'
            USING ERRCODE = 'integrity_constraint_violation',
                  CONSTRAINT = 'failure_reviews_immutable';
    END IF;
    IF NEW.reviewer_decision IS DISTINCT FROM OLD.reviewer_decision
       AND EXISTS (SELECT 1 FROM test_cases WHERE origin_review_id = OLD.id) THEN
        RAISE EXCEPTION 'the reviewer decision is frozen: a test case cites this review'
            USING ERRCODE = 'integrity_constraint_violation',
                  CONSTRAINT = 'failure_reviews_decision_frozen';
    END IF;
    RETURN NEW;
END
$$
"""

_CASE_FUNCTION = """
CREATE FUNCTION test_cases_review_eligibility() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    IF NEW.origin_review_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT fr.reviewer_decision, fr.trace_id, fr.project_id INTO r
        FROM failure_reviews fr WHERE fr.id = NEW.origin_review_id FOR SHARE;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports a missing review
    END IF;
    IF r.reviewer_decision NOT IN ('hypothesis_supported', 'hypothesis_rejected')
       OR (r.reviewer_decision = 'hypothesis_rejected' AND NEW.expected_output IS NULL)
       OR NEW.origin_trace_id IS DISTINCT FROM r.trace_id
       OR r.project_id IS DISTINCT FROM
            (SELECT d.project_id FROM test_datasets d WHERE d.id = NEW.dataset_id) THEN
        RAISE EXCEPTION 'the cited review is not eligible for this test case'
            USING ERRCODE = 'integrity_constraint_violation',
                  CONSTRAINT = 'test_cases_review_eligibility';
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text(_DUPLICATES)).scalar_one()
    ineligible = bind.execute(sa.text(_INELIGIBLE)).scalar_one()
    if duplicates or ineligible:
        raise RuntimeError(
            "refusing to upgrade: "
            f"{duplicates} (dataset, review) pairs with more than one test case, "
            f"{ineligible} test cases citing a review the Phase 4C rules do not accept; "
            "resolve them by hand (they are human work and are never rewritten automatically)"
        )
    op.create_index(
        CASE_REVIEW_INDEX,
        "test_cases",
        ["dataset_id", "origin_review_id"],
        unique=True,
        postgresql_where=sa.text("origin_review_id IS NOT NULL"),
    )
    op.execute(_REVIEW_FUNCTION)
    op.execute(
        "CREATE TRIGGER failure_reviews_integrity BEFORE UPDATE ON failure_reviews"
        " FOR EACH ROW EXECUTE FUNCTION failure_reviews_integrity()"
    )
    op.execute(_CASE_FUNCTION)
    op.execute(
        "CREATE TRIGGER test_cases_review_eligibility BEFORE INSERT OR UPDATE OF"
        " origin_review_id, origin_trace_id, dataset_id, expected_output ON test_cases"
        " FOR EACH ROW EXECUTE FUNCTION test_cases_review_eligibility()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER test_cases_review_eligibility ON test_cases")
    op.execute("DROP FUNCTION test_cases_review_eligibility()")
    op.execute("DROP TRIGGER failure_reviews_integrity ON failure_reviews")
    op.execute("DROP FUNCTION failure_reviews_integrity()")
    op.drop_index(CASE_REVIEW_INDEX, table_name="test_cases")
