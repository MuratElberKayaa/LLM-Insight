"""Phase 1 core schema: ARCHITECTURE.md §4.2–4.6 (ADR-21).

Tables: projects, project_api_keys, traces, spans, usage_records.
`usage_records.model_price_id` is created without its FK: `model_prices` (§4.7) and the
FK `model_prices(id) ON DELETE RESTRICT` are added by the Phase 2 migration.

Revision ID: 0001
Revises:
Create Date: 2026-10-24 16:25:32.339389+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100", name=op.f("ck_projects_name_len")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("name", name=op.f("uq_projects_name")),
    )
    op.create_table(
        "project_api_keys",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_project_api_keys_key_hash_hmac_sha256_hex")
        ),
        sa.CheckConstraint(
            "prefix ~ '^[A-Za-z0-9]{8}$'", name=op.f("ck_project_api_keys_prefix_format")
        ),
        sa.CheckConstraint(
            "revoked_at >= created_at", name=op.f("ck_project_api_keys_revoked_after_created")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_api_keys_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_api_keys")),
        sa.UniqueConstraint("prefix", name=op.f("uq_project_api_keys_prefix")),
    )
    op.create_index(
        op.f("ix_project_api_keys_project_id"), "project_api_keys", ["project_id"], unique=False
    )
    op.create_table(
        "traces",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column(
            "service_name", sa.Text(), server_default=sa.text("'unknown_service'"), nullable=False
        ),
        sa.Column("start_ns", sa.BigInteger(), nullable=False),
        sa.Column("end_ns", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("span_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provenance IN ('real', 'synthetic', 'mixed', 'unknown')",
            name=op.f("ck_traces_provenance_enum"),
        ),
        sa.CheckConstraint("status IN ('ok', 'error')", name=op.f("ck_traces_status_enum")),
        sa.CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)",
            name=op.f("ck_traces_trace_id_hex32"),
        ),
        sa.CheckConstraint(
            "char_length(service_name) BETWEEN 1 AND 256", name=op.f("ck_traces_service_name_len")
        ),
        sa.CheckConstraint("end_ns >= start_ns", name=op.f("ck_traces_end_after_start")),
        sa.CheckConstraint("span_count >= 1", name=op.f("ck_traces_span_count_positive")),
        sa.CheckConstraint("start_ns > 0", name=op.f("ck_traces_start_ns_positive")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_traces_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_traces")),
        sa.UniqueConstraint("project_id", "trace_id", name=op.f("uq_traces_project_id_trace_id")),
    )
    op.create_index(
        "ix_traces_project_id_started_at",
        "traces",
        ["project_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_table(
        "spans",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("span_id", sa.Text(), nullable=False),
        sa.Column("parent_span_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("start_ns", sa.BigInteger(), nullable=False),
        sa.Column("end_ns", sa.BigInteger(), nullable=False),
        sa.Column(
            "duration_ns",
            sa.BigInteger(),
            sa.Computed("end_ns - start_ns", persisted=True),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("scope_name", sa.Text(), nullable=True),
        sa.Column("scope_version", sa.Text(), nullable=True),
        sa.Column(
            "resource_attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("attributes_schema_version", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column(
            "server_dropped_attributes_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes) = 'object'", name=op.f("ck_spans_attributes_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resource_attributes) = 'object'",
            name=op.f("ck_spans_resource_attributes_object"),
        ),
        sa.CheckConstraint(
            "kind IN ('UNSPECIFIED', 'INTERNAL', 'SERVER', 'CLIENT', 'PRODUCER', 'CONSUMER')",
            name=op.f("ck_spans_kind_enum"),
        ),
        sa.CheckConstraint(
            "parent_span_id ~ '^[0-9a-f]{16}$' AND parent_span_id <> repeat('0', 16)",
            name=op.f("ck_spans_parent_span_id_hex16"),
        ),
        sa.CheckConstraint(
            "provenance IN ('real', 'synthetic', 'unknown')", name=op.f("ck_spans_provenance_enum")
        ),
        sa.CheckConstraint(
            "span_id ~ '^[0-9a-f]{16}$' AND span_id <> repeat('0', 16)",
            name=op.f("ck_spans_span_id_hex16"),
        ),
        sa.CheckConstraint("status IN ('UNSET', 'OK', 'ERROR')", name=op.f("ck_spans_status_enum")),
        sa.CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)",
            name=op.f("ck_spans_trace_id_hex32"),
        ),
        sa.CheckConstraint(
            "char_length(error_type) <= 256", name=op.f("ck_spans_error_type_maxlen")
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 256", name=op.f("ck_spans_name_len")),
        sa.CheckConstraint(
            "char_length(scope_name) <= 256", name=op.f("ck_spans_scope_name_maxlen")
        ),
        sa.CheckConstraint(
            "char_length(scope_version) <= 64", name=op.f("ck_spans_scope_version_maxlen")
        ),
        sa.CheckConstraint(
            "char_length(status_message) <= 256", name=op.f("ck_spans_status_message_maxlen")
        ),
        sa.CheckConstraint("duration_ns >= 0", name=op.f("ck_spans_duration_ns_nonneg")),
        sa.CheckConstraint("end_ns >= start_ns", name=op.f("ck_spans_end_after_start")),
        sa.CheckConstraint("parent_span_id <> span_id", name=op.f("ck_spans_parent_not_self")),
        sa.CheckConstraint(
            "server_dropped_attributes_count >= 0",
            name=op.f("ck_spans_server_dropped_attributes_count_nonneg"),
        ),
        sa.CheckConstraint("start_ns > 0", name=op.f("ck_spans_start_ns_positive")),
        sa.ForeignKeyConstraint(
            ["project_id", "trace_id"],
            ["traces.project_id", "traces.trace_id"],
            name=op.f("fk_spans_traces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spans")),
        sa.UniqueConstraint(
            "project_id", "trace_id", "span_id", name=op.f("uq_spans_project_id_trace_id_span_id")
        ),
    )
    op.create_index(
        "ix_spans_project_id_trace_id_start_ns",
        "spans",
        ["project_id", "trace_id", "start_ns"],
        unique=False,
    )
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_call_key", sa.Text(), nullable=False),
        sa.Column("call_key_source", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("span_id", sa.Text(), nullable=False),
        sa.Column("call_id", sa.Text(), nullable=True),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("provider_response_id", sa.Text(), nullable=True),
        sa.Column("operation_name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("requested_model", sa.Text(), nullable=True),
        sa.Column("response_model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("usage_source", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("cost_status", sa.Text(), nullable=False),
        sa.Column("cost_unknown_reason", sa.Text(), nullable=True),
        sa.Column("price_version", sa.Text(), nullable=True),
        sa.Column("model_price_id", sa.BigInteger(), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(cost_status = 'computed') = (cost_usd IS NOT NULL)",
            name=op.f("ck_usage_records_cost_computed_has_value"),
        ),
        sa.CheckConstraint(
            "(cost_status = 'unknown') = (cost_unknown_reason IS NOT NULL)",
            name=op.f("ck_usage_records_cost_unknown_has_reason"),
        ),
        sa.CheckConstraint(
            "call_key_source IN ('provider_response_id', 'sdk_attempt', 'span_fallback')",
            name=op.f("ck_usage_records_call_key_source_enum"),
        ),
        sa.CheckConstraint(
            "cost_status <> 'computed' OR (price_version IS NOT NULL AND model_price_id IS NOT NULL)",
            name=op.f("ck_usage_records_cost_computed_has_price"),
        ),
        sa.CheckConstraint(
            "cost_status IN ('computed', 'unknown')", name=op.f("ck_usage_records_cost_status_enum")
        ),
        sa.CheckConstraint(
            "cost_unknown_reason IN ('price_missing', 'usage_missing', 'usage_inconsistent')",
            name=op.f("ck_usage_records_cost_unknown_reason_enum"),
        ),
        sa.CheckConstraint(
            "operation_name IN ('chat', 'text_completion', 'generate_content', 'embeddings')",
            name=op.f("ck_usage_records_operation_name_enum"),
        ),
        sa.CheckConstraint(
            "provenance IN ('real', 'synthetic', 'unknown')",
            name=op.f("ck_usage_records_provenance_enum"),
        ),
        sa.CheckConstraint(
            "span_id ~ '^[0-9a-f]{16}$' AND span_id <> repeat('0', 16)",
            name=op.f("ck_usage_records_span_id_hex16"),
        ),
        sa.CheckConstraint(
            "trace_id ~ '^[0-9a-f]{32}$' AND trace_id <> repeat('0', 32)",
            name=op.f("ck_usage_records_trace_id_hex32"),
        ),
        sa.CheckConstraint(
            "usage_source IN ('provider_reported', 'estimated', 'unknown')",
            name=op.f("ck_usage_records_usage_source_enum"),
        ),
        sa.CheckConstraint("attempt_id >= 1", name=op.f("ck_usage_records_attempt_id_positive")),
        sa.CheckConstraint(
            "cache_write_input_tokens >= 0",
            name=op.f("ck_usage_records_cache_write_input_tokens_nonneg"),
        ),
        sa.CheckConstraint(
            "cached_input_tokens >= 0", name=op.f("ck_usage_records_cached_input_tokens_nonneg")
        ),
        sa.CheckConstraint(
            "char_length(call_id) <= 64", name=op.f("ck_usage_records_call_id_maxlen")
        ),
        sa.CheckConstraint(
            "char_length(canonical_call_key) BETWEEN 1 AND 300",
            name=op.f("ck_usage_records_canonical_call_key_len"),
        ),
        sa.CheckConstraint(
            "char_length(provider) BETWEEN 1 AND 64", name=op.f("ck_usage_records_provider_len")
        ),
        sa.CheckConstraint(
            "char_length(provider_response_id) <= 256",
            name=op.f("ck_usage_records_provider_response_id_maxlen"),
        ),
        sa.CheckConstraint(
            "char_length(requested_model) <= 256",
            name=op.f("ck_usage_records_requested_model_maxlen"),
        ),
        sa.CheckConstraint(
            "char_length(response_model) <= 256",
            name=op.f("ck_usage_records_response_model_maxlen"),
        ),
        sa.CheckConstraint("cost_usd >= 0", name=op.f("ck_usage_records_cost_usd_nonneg")),
        sa.CheckConstraint("input_tokens >= 0", name=op.f("ck_usage_records_input_tokens_nonneg")),
        sa.CheckConstraint(
            "output_tokens >= 0", name=op.f("ck_usage_records_output_tokens_nonneg")
        ),
        sa.CheckConstraint(
            "reasoning_output_tokens >= 0",
            name=op.f("ck_usage_records_reasoning_output_tokens_nonneg"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "trace_id", "span_id"],
            ["spans.project_id", "spans.trace_id", "spans.span_id"],
            name=op.f("fk_usage_records_spans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
        sa.UniqueConstraint(
            "project_id",
            "canonical_call_key",
            name=op.f("uq_usage_records_project_id_canonical_call_key"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "trace_id",
            "span_id",
            name=op.f("uq_usage_records_project_id_trace_id_span_id"),
        ),
    )
    op.create_index(
        "ix_usage_records_project_created_provider_model",
        "usage_records",
        ["project_id", "created_at", "provider", "response_model"],
        unique=False,
    )
    op.create_index(
        "ix_usage_records_project_id_call_id",
        "usage_records",
        ["project_id", "call_id"],
        unique=False,
        postgresql_where=sa.text("call_id IS NOT NULL"),
    )
    op.create_index(
        "ix_usage_records_project_id_started_at",
        "usage_records",
        ["project_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_project_id_started_at", table_name="usage_records")
    op.drop_index(
        "ix_usage_records_project_id_call_id",
        table_name="usage_records",
        postgresql_where=sa.text("call_id IS NOT NULL"),
    )
    op.drop_index("ix_usage_records_project_created_provider_model", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_spans_project_id_trace_id_start_ns", table_name="spans")
    op.drop_table("spans")
    op.drop_index("ix_traces_project_id_started_at", table_name="traces")
    op.drop_table("traces")
    op.drop_index(op.f("ix_project_api_keys_project_id"), table_name="project_api_keys")
    op.drop_table("project_api_keys")
    op.drop_table("projects")
