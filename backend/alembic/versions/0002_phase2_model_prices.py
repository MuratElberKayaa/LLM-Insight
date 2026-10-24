"""Phase 2 pricing schema: ARCHITECTURE.md §4.7 `model_prices` plus the deferred FK.

- Creates `model_prices` column for column as in §4.7.
- Adds `usage_records.model_price_id` → `model_prices(id) ON DELETE RESTRICT` (deferred from
  `0001`). Existing usage rows keep their data; their `model_price_id` is NULL, because no
  row could be `computed` before a price existed.
- Makes `model_prices` append-only (docs/PRICING.md): UPDATE, DELETE and TRUNCATE are
  rejected, and an INSERT is rejected when the same (provider, model) already has a price
  starting later. A stored price is therefore never rewritten, and a new row can only
  close the validity period of the latest one.

Downgrade refuses to run while any usage row references a price: dropping the table would
leave `computed` costs without the price they were computed from.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 09:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPEND_ONLY_FUNCTION = """
CREATE FUNCTION model_prices_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Serialize inserts per (provider, model) so two concurrent transactions cannot
        -- both pass the check below.
        PERFORM pg_advisory_xact_lock(
            hashtextextended(NEW.provider || chr(31) || NEW.model, 0));
        IF EXISTS (
            SELECT 1 FROM model_prices p
            WHERE p.provider = NEW.provider AND p.model = NEW.model
              AND p.effective_from > NEW.effective_from
        ) THEN
            RAISE EXCEPTION 'model_prices is append-only: a later price already exists'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'model_prices is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$
"""


def upgrade() -> None:
    op.create_table(
        "model_prices",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.Text(), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("input_per_million", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column("output_per_million", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column("cached_input_per_million", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column(
            "cache_write_input_per_million", sa.Numeric(precision=20, scale=10), nullable=True
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(version) BETWEEN 1 AND 64", name=op.f("ck_model_prices_version_len")
        ),
        sa.CheckConstraint(
            "char_length(provider) BETWEEN 1 AND 64", name=op.f("ck_model_prices_provider_len")
        ),
        sa.CheckConstraint(
            "char_length(model) BETWEEN 1 AND 256", name=op.f("ck_model_prices_model_len")
        ),
        sa.CheckConstraint(
            "provider ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
            name=op.f("ck_model_prices_provider_format"),
        ),
        sa.CheckConstraint(
            "model ~ '^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$'", name=op.f("ck_model_prices_model_format")
        ),
        sa.CheckConstraint("currency = 'USD'", name=op.f("ck_model_prices_currency_usd")),
        sa.CheckConstraint(
            "input_per_million >= 0", name=op.f("ck_model_prices_input_per_million_nonneg")
        ),
        sa.CheckConstraint(
            "output_per_million >= 0", name=op.f("ck_model_prices_output_per_million_nonneg")
        ),
        sa.CheckConstraint(
            "cached_input_per_million >= 0",
            name=op.f("ck_model_prices_cached_input_per_million_nonneg"),
        ),
        sa.CheckConstraint(
            "cache_write_input_per_million >= 0",
            name=op.f("ck_model_prices_cache_write_input_per_million_nonneg"),
        ),
        sa.CheckConstraint(
            "source_url LIKE 'https://%'", name=op.f("ck_model_prices_source_url_https")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_prices")),
        sa.UniqueConstraint(
            "provider",
            "model",
            "effective_from",
            name=op.f("uq_model_prices_provider_model_effective_from"),
        ),
        sa.UniqueConstraint(
            "provider", "model", "version", name=op.f("uq_model_prices_provider_model_version")
        ),
    )
    op.create_foreign_key(
        op.f("fk_usage_records_model_prices"),
        "usage_records",
        "model_prices",
        ["model_price_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(_APPEND_ONLY_FUNCTION)
    op.execute(
        "CREATE TRIGGER model_prices_append_only_rows"
        " BEFORE INSERT OR UPDATE OR DELETE ON model_prices"
        " FOR EACH ROW EXECUTE FUNCTION model_prices_append_only()"
    )
    op.execute(
        "CREATE TRIGGER model_prices_append_only_truncate"
        " BEFORE TRUNCATE ON model_prices"
        " FOR EACH STATEMENT EXECUTE FUNCTION model_prices_append_only()"
    )


def downgrade() -> None:
    referenced = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM usage_records WHERE model_price_id IS NOT NULL"))
        .scalar_one()
    )
    if referenced:
        raise RuntimeError(
            f"refusing to downgrade: {referenced} usage_records rows reference model_prices; "
            "dropping the price catalogue would orphan computed costs"
        )
    op.drop_constraint(op.f("fk_usage_records_model_prices"), "usage_records", type_="foreignkey")
    op.execute("DROP TRIGGER model_prices_append_only_truncate ON model_prices")
    op.execute("DROP TRIGGER model_prices_append_only_rows ON model_prices")
    op.execute("DROP FUNCTION model_prices_append_only()")
    op.drop_table("model_prices")
