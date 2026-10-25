"""Phase 4B schema: ARCHITECTURE.md §4.8 `test_datasets`, §4.9 `failure_reviews`, §4.10
`test_cases`, column for column. §4.11–4.12 (experiments) belong to Phase 4C.

No Phase 1–3 table is altered: reviews and test cases reference traces softly (no FK, §4.1),
so telemetry rows, usage rows and prices are untouched by this migration.

Downgrade refuses to run while any review, dataset or test case exists: those rows are human
work (reviews, approvals, typed test content) that no other table could restore.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_datasets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name=op.f("ck_test_datasets_name_len")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_test_datasets_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_datasets")),
        sa.UniqueConstraint("project_id", "name", name=op.f("uq_test_datasets_project_id_name")),
    )
    op.create_table(
        "failure_reviews",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("span_id", sa.Text(), nullable=True),
        sa.Column("issue_type", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("review_status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column(
            "reviewer_decision", sa.Text(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)",
            name=op.f("ck_failure_reviews_trace_id_hex32"),
        ),
        sa.CheckConstraint(
            "span_id ~ '^[0-9a-f]{16}$' AND span_id <> repeat('0', 16)",
            name=op.f("ck_failure_reviews_span_id_hex16"),
        ),
        sa.CheckConstraint(
            "issue_type IN ('retrieval_missing', 'chunk_boundary', 'prompt_instruction',"
            " 'generation_mismatch', 'schema_violation', 'unknown')",
            name=op.f("ck_failure_reviews_issue_type_enum"),
        ),
        sa.CheckConstraint(
            "char_length(evidence) BETWEEN 1 AND 4000",
            name=op.f("ck_failure_reviews_evidence_len"),
        ),
        sa.CheckConstraint(
            "char_length(hypothesis) BETWEEN 1 AND 2000",
            name=op.f("ck_failure_reviews_hypothesis_len"),
        ),
        sa.CheckConstraint(
            "review_status IN ('open', 'in_review', 'closed')",
            name=op.f("ck_failure_reviews_review_status_enum"),
        ),
        sa.CheckConstraint(
            "reviewer_decision IN ('pending', 'hypothesis_supported', 'hypothesis_rejected',"
            " 'inconclusive')",
            name=op.f("ck_failure_reviews_reviewer_decision_enum"),
        ),
        sa.CheckConstraint(
            "char_length(created_by) BETWEEN 1 AND 100",
            name=op.f("ck_failure_reviews_created_by_len"),
        ),
        sa.CheckConstraint(
            "review_status <> 'closed' OR reviewer_decision <> 'pending'",
            name=op.f("ck_failure_reviews_closed_needs_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_failure_reviews_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_failure_reviews")),
    )
    op.create_index(
        "ix_failure_reviews_project_id_trace_id",
        "failure_reviews",
        ["project_id", "trace_id"],
        unique=False,
    )
    op.create_index(
        "uq_failure_reviews_project_id_trace_id_issue_type",
        "failure_reviews",
        ["project_id", "trace_id", "issue_type"],
        unique=True,
        postgresql_where=sa.text("review_status <> 'closed'"),
    )
    op.create_table(
        "test_cases",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("origin_trace_id", sa.Text(), nullable=True),
        sa.Column("origin_review_id", sa.BigInteger(), nullable=True),
        sa.Column("sanitized_input", sa.Text(), nullable=False),
        sa.Column("expected_output", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("rule", postgresql.JSONB(), nullable=False),
        sa.Column("redaction_applied", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin_trace_id ~ '^[0-9a-f]{32}$' AND origin_trace_id <> repeat('0', 32)",
            name=op.f("ck_test_cases_origin_trace_id_hex32"),
        ),
        sa.CheckConstraint(
            "char_length(sanitized_input) BETWEEN 1 AND 8000",
            name=op.f("ck_test_cases_sanitized_input_len"),
        ),
        sa.CheckConstraint(
            "COALESCE(rule->>'type', '') IN ('json_schema', 'field_match', 'exact', 'regex')",
            name=op.f("ck_test_cases_rule_type_enum"),
        ),
        sa.CheckConstraint(
            "char_length(approved_by) BETWEEN 1 AND 100",
            name=op.f("ck_test_cases_approved_by_len"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["test_datasets.id"],
            name=op.f("fk_test_cases_test_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["origin_review_id"],
            ["failure_reviews.id"],
            name=op.f("fk_test_cases_failure_reviews"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_cases")),
    )
    op.create_index(op.f("ix_test_cases_dataset_id"), "test_cases", ["dataset_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.select(sa.func.count()).select_from(sa.table(table))).scalar_one()
        for table in ("failure_reviews", "test_datasets", "test_cases")
    }
    if any(counts.values()):
        held = ", ".join(f"{n} {t}" for t, n in counts.items() if n)
        raise RuntimeError(
            f"refusing to downgrade: {held} rows exist; they are human-authored reviews, "
            "approvals and test content that no other table can restore"
        )
    op.drop_index(op.f("ix_test_cases_dataset_id"), table_name="test_cases")
    op.drop_table("test_cases")
    op.drop_index("uq_failure_reviews_project_id_trace_id_issue_type", table_name="failure_reviews")
    op.drop_index("ix_failure_reviews_project_id_trace_id", table_name="failure_reviews")
    op.drop_table("failure_reviews")
    op.drop_table("test_datasets")
