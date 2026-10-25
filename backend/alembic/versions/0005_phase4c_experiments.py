"""Phase 4C Gate B: ARCHITECTURE.md §4.11 `experiments` and §4.12 `experiment_results`, column
for column. No existing table is altered.

Beyond the column checks, two trigger functions keep the recorded evidence consistent (stricter
than the contract, no column added; ADR-25):
- `experiment_results_integrity` (BEFORE INSERT OR UPDATE on `experiment_results`): results
  are append-only (any UPDATE is refused); a result's `prompt_version` must be one of its
  experiment's two versions; its test case must belong to the experiment's dataset; a `mock`
  experiment's results must be `synthetic` (T39); no result is added after `finished_at`.
- `experiments_integrity` (BEFORE UPDATE on `experiments`): every column is immutable except
  `finished_at`, which may be set once.

Downgrade refuses to run while any experiment exists: the rows are recorded evidence of a run
(its time, budget and outcomes), which no other table holds.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESULTS_FUNCTION = """
CREATE FUNCTION experiment_results_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    e record;
    case_dataset bigint;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'experiment results are append-only'
            USING ERRCODE = 'integrity_constraint_violation',
                  CONSTRAINT = 'experiment_results_append_only';
    END IF;
    SELECT x.dataset_id, x.prompt_version_a, x.prompt_version_b, x.mode, x.finished_at INTO e
        FROM experiments x WHERE x.id = NEW.experiment_id;
    IF NOT FOUND THEN
        RETURN NEW;  -- the foreign key reports a missing experiment
    END IF;
    SELECT c.dataset_id INTO case_dataset FROM test_cases c WHERE c.id = NEW.test_case_id;
    IF e.finished_at IS NOT NULL
       OR NEW.prompt_version NOT IN (e.prompt_version_a, e.prompt_version_b)
       OR (case_dataset IS NOT NULL AND case_dataset <> e.dataset_id)
       OR (e.mode = 'mock' AND NEW.provenance <> 'synthetic') THEN
        RAISE EXCEPTION 'the result does not belong to this experiment'
            USING ERRCODE = 'integrity_constraint_violation',
                  CONSTRAINT = 'experiment_results_integrity';
    END IF;
    RETURN NEW;
END
$$
"""

_EXPERIMENTS_FUNCTION = """
CREATE FUNCTION experiments_integrity() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.prompt_version_a IS DISTINCT FROM OLD.prompt_version_a
       OR NEW.prompt_version_b IS DISTINCT FROM OLD.prompt_version_b
       OR NEW.model IS DISTINCT FROM OLD.model
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.budget_usd IS DISTINCT FROM OLD.budget_usd
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR (OLD.finished_at IS NOT NULL
           AND NEW.finished_at IS DISTINCT FROM OLD.finished_at) THEN
        RAISE EXCEPTION 'an experiment is immutable except for setting finished_at once'
            USING ERRCODE = 'integrity_constraint_violation',
                  CONSTRAINT = 'experiments_immutable';
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_version_a", sa.Text(), nullable=False),
        sa.Column("prompt_version_b", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("budget_usd", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(prompt_version_a) BETWEEN 1 AND 64",
            name=op.f("ck_experiments_prompt_version_a_len"),
        ),
        sa.CheckConstraint(
            "char_length(prompt_version_b) BETWEEN 1 AND 64",
            name=op.f("ck_experiments_prompt_version_b_len"),
        ),
        sa.CheckConstraint(
            "prompt_version_b <> prompt_version_a",
            name=op.f("ck_experiments_prompt_versions_differ"),
        ),
        sa.CheckConstraint(
            "char_length(model) BETWEEN 1 AND 256", name=op.f("ck_experiments_model_len")
        ),
        sa.CheckConstraint("mode IN ('real', 'mock')", name=op.f("ck_experiments_mode_enum")),
        sa.CheckConstraint("budget_usd > 0", name=op.f("ck_experiments_budget_usd_positive")),
        sa.CheckConstraint(
            "mode = 'mock' OR budget_usd IS NOT NULL",
            name=op.f("ck_experiments_real_needs_budget"),
        ),
        sa.CheckConstraint(
            "finished_at >= started_at", name=op.f("ck_experiments_finished_after_start")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["test_datasets.id"],
            name=op.f("fk_experiments_test_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
    )
    op.create_index(op.f("ix_experiments_dataset_id"), "experiments", ["dataset_id"], unique=False)
    op.create_table(
        "experiment_results",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False),
        sa.Column("test_case_id", sa.BigInteger(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("evaluation_detail", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("cost_status", sa.Text(), nullable=False),
        sa.Column("cost_unknown_reason", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(prompt_version) BETWEEN 1 AND 64",
            name=op.f("ck_experiment_results_prompt_version_len"),
        ),
        sa.CheckConstraint(
            "char_length(evaluation_detail) <= 1000",
            name=op.f("ck_experiment_results_evaluation_detail_maxlen"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_experiment_results_duration_ms_nonneg")
        ),
        sa.CheckConstraint(
            "input_tokens >= 0", name=op.f("ck_experiment_results_input_tokens_nonneg")
        ),
        sa.CheckConstraint(
            "output_tokens >= 0", name=op.f("ck_experiment_results_output_tokens_nonneg")
        ),
        sa.CheckConstraint("cost_usd >= 0", name=op.f("ck_experiment_results_cost_usd_nonneg")),
        sa.CheckConstraint(
            "cost_status IN ('computed', 'unknown')",
            name=op.f("ck_experiment_results_cost_status_enum"),
        ),
        sa.CheckConstraint(
            "cost_unknown_reason IN ('price_missing', 'usage_missing', 'usage_inconsistent')",
            name=op.f("ck_experiment_results_cost_unknown_reason_enum"),
        ),
        sa.CheckConstraint(
            "provenance IN ('real', 'synthetic')",
            name=op.f("ck_experiment_results_provenance_enum"),
        ),
        sa.CheckConstraint(
            "(cost_status = 'computed') = (cost_usd IS NOT NULL)",
            name=op.f("ck_experiment_results_cost_computed_has_value"),
        ),
        sa.CheckConstraint(
            "(cost_status = 'unknown') = (cost_unknown_reason IS NOT NULL)",
            name=op.f("ck_experiment_results_cost_unknown_has_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_results_experiments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name=op.f("fk_experiment_results_test_cases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_results")),
        sa.UniqueConstraint(
            "experiment_id",
            "test_case_id",
            "prompt_version",
            name=op.f("uq_experiment_results_experiment_id_test_case_id_prompt_version"),
        ),
    )
    op.execute(_RESULTS_FUNCTION)
    op.execute(
        "CREATE TRIGGER experiment_results_integrity BEFORE INSERT OR UPDATE"
        " ON experiment_results FOR EACH ROW EXECUTE FUNCTION experiment_results_integrity()"
    )
    op.execute(_EXPERIMENTS_FUNCTION)
    op.execute(
        "CREATE TRIGGER experiments_integrity BEFORE UPDATE ON experiments"
        " FOR EACH ROW EXECUTE FUNCTION experiments_integrity()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    held = bind.execute(
        sa.select(sa.func.count()).select_from(sa.table("experiments"))
    ).scalar_one()
    if held:
        raise RuntimeError(
            f"refusing to downgrade: {held} experiments rows exist; they are recorded run"
            " evidence (time, budget, outcomes) that no other table can restore"
        )
    op.execute("DROP TRIGGER experiments_integrity ON experiments")
    op.execute("DROP FUNCTION experiments_integrity()")
    op.execute("DROP TRIGGER experiment_results_integrity ON experiment_results")
    op.execute("DROP FUNCTION experiment_results_integrity()")
    op.drop_table("experiment_results")
    op.drop_index(op.f("ix_experiments_dataset_id"), table_name="experiments")
    op.drop_table("experiments")
