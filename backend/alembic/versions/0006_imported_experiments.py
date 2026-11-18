"""Store completed live reports without enabling real execution or inventing a budget."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "imported_experiments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "project_id",
            sa.BigInteger(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("project_id", "run_id", "model"),
        sa.CheckConstraint(
            "run_id ~ '^[0-9a-f]{32}$' AND run_id <> repeat('0', 32)", name="run_id_hex32"
        ),
        sa.CheckConstraint("char_length(model) BETWEEN 1 AND 256", name="model_len"),
        sa.CheckConstraint(
            "char_length(payload_sha256) BETWEEN 1 AND 64", name="payload_sha256_len"
        ),
    )
    op.create_index("ix_imported_experiments_project_id", "imported_experiments", ["project_id"])
    op.execute("""
        CREATE FUNCTION imported_experiments_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'imported experiments are immutable'
                USING ERRCODE = 'integrity_constraint_violation';
        END; $$;
        CREATE TRIGGER imported_experiments_immutable BEFORE UPDATE ON imported_experiments
        FOR EACH ROW EXECUTE FUNCTION imported_experiments_immutable();
    """)


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM imported_experiments)"))
        .scalar()
    ):
        raise RuntimeError("refusing to discard imported evidence")
    op.drop_table("imported_experiments")
    op.execute("DROP FUNCTION imported_experiments_immutable()")
