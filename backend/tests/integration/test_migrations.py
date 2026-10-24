"""T31 (Phase 1 scope): upgrade -> downgrade -> upgrade on a disposable DB, and the
migrated schema matches ARCHITECTURE.md §4.2–4.6 column for column."""

import re

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.models import Base
from tests.helpers import REPO_ROOT, alembic_config

pytestmark = pytest.mark.integration

PHASE1_TABLES = ["projects", "project_api_keys", "traces", "spans", "usage_records"]

DOC_TYPES = {
    "BIGSERIAL": ("bigint", None, None),
    "BIGINT": ("bigint", None, None),
    "INT": ("integer", None, None),
    "TEXT": ("text", None, None),
    "TIMESTAMPTZ": ("timestamp with time zone", None, None),
    "JSONB": ("jsonb", None, None),
    "BOOLEAN": ("boolean", None, None),
    "NUMERIC(20,10)": ("numeric", 20, 10),
}


def _contract() -> dict[str, list[list[str]]]:
    """Column rows (name, type, null, default, check, origin) per table from the doc."""
    doc = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    tables = {}
    for m in re.finditer(r"^### 4\.\d+ `(\w+)`[^\n]*\n(.*?)(?=^### |\Z)", doc, re.M | re.S):
        lines = [ln for ln in m.group(2).splitlines() if ln.startswith("|")]
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines[2:]]
        tables[m.group(1)] = [r for r in rows if len(r) == 6]
    return tables


def _schema(url: str) -> dict[str, dict]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            rows = c.execute(
                text(
                    "SELECT table_name, column_name, ordinal_position, data_type, is_nullable,"
                    " column_default, numeric_precision, numeric_scale, is_generated,"
                    " generation_expression FROM information_schema.columns"
                    " WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
                )
            ).mappings()
            schema: dict[str, dict] = {}
            for r in rows:
                schema.setdefault(r["table_name"], {})[r["column_name"]] = dict(r)
            return schema
    finally:
        eng.dispose()


def _tables(url: str) -> set[str]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def test_upgrade_downgrade_upgrade_cycle(disposable_db):
    url = disposable_db("llminsight_migration_test")
    cfg = alembic_config(url)
    assert _tables(url) == set()

    command.upgrade(cfg, "head")
    assert _tables(url) == {*PHASE1_TABLES, "alembic_version"}
    first = _schema(url)

    command.downgrade(cfg, "base")
    assert _tables(url) == {"alembic_version"}  # only the (empty) version table remains

    command.upgrade(cfg, "head")
    assert _tables(url) == {*PHASE1_TABLES, "alembic_version"}
    assert _schema(url) == first  # recreate is identical


def test_models_and_migration_have_no_drift(engine):
    with engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == []


def test_schema_matches_architecture_contract(migrated_url):
    contract = _contract()
    schema = _schema(migrated_url)
    assert set(schema) == {*PHASE1_TABLES, "alembic_version"}
    for table in PHASE1_TABLES:
        rows = contract[table]
        assert rows, f"{table}: no contract rows parsed"
        assert [r[0] for r in rows] == list(schema[table]), f"{table}: column set/order"
        for name, typ, null, default, _check, _origin in rows:
            col = schema[table][name]
            data_type, precision, scale = DOC_TYPES[typ]
            where = f"{table}.{name}"
            assert col["data_type"] == data_type, where
            if precision is not None:
                assert (col["numeric_precision"], col["numeric_scale"]) == (precision, scale), where
            assert col["is_nullable"] == ("YES" if null == "NULL" else "NO"), where
            actual_default = col["column_default"]
            if typ == "BIGSERIAL":
                assert actual_default == f"nextval('{table}_{name}_seq'::regclass)", where
            elif default.startswith("GENERATED ALWAYS AS"):
                expr = re.search(r"`(.+?)`", default).group(1)
                assert col["is_generated"] == "ALWAYS", where
                assert col["generation_expression"] == f"({expr})", where
            elif default:
                assert actual_default is not None, where
                assert actual_default.split("::")[0] == default.strip("`"), where
            else:
                assert actual_default is None, where


def _constraint_defs(conn, contype: str) -> dict[str, str]:
    rows = conn.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE connamespace = 'public'::regnamespace AND contype = :t"
        ),
        {"t": contype},
    )
    return dict(rows.all())


def test_foreign_keys(conn):
    fks = _constraint_defs(conn, "f")
    assert fks == {
        "fk_project_api_keys_projects": "FOREIGN KEY (project_id)"
        " REFERENCES projects(id) ON DELETE CASCADE",
        "fk_traces_projects": "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE",
        "fk_spans_traces": "FOREIGN KEY (project_id, trace_id)"
        " REFERENCES traces(project_id, trace_id) ON DELETE CASCADE",
        "fk_usage_records_spans": "FOREIGN KEY (project_id, trace_id, span_id)"
        " REFERENCES spans(project_id, trace_id, span_id) ON DELETE CASCADE",
    }
    # No FK on spans.parent_span_id (orphans allowed) or usage_records.model_price_id (Phase 2).


def test_unique_constraints(conn):
    assert _constraint_defs(conn, "u") == {
        "uq_projects_name": "UNIQUE (name)",
        "uq_project_api_keys_prefix": "UNIQUE (prefix)",
        "uq_traces_project_id_trace_id": "UNIQUE (project_id, trace_id)",
        "uq_spans_project_id_trace_id_span_id": "UNIQUE (project_id, trace_id, span_id)",
        "uq_usage_records_project_id_canonical_call_key": "UNIQUE (project_id, canonical_call_key)",
        "uq_usage_records_project_id_trace_id_span_id": "UNIQUE (project_id, trace_id, span_id)",
    }


def test_indexes(conn):
    rows = conn.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname LIKE 'ix_%'"
        )
    )
    defs = {name: d.split(" USING btree ", 1)[1] for name, d in rows.all()}
    assert defs == {
        "ix_project_api_keys_project_id": "(project_id)",
        "ix_traces_project_id_started_at": "(project_id, started_at DESC)",
        "ix_spans_project_id_trace_id_start_ns": "(project_id, trace_id, start_ns)",
        "ix_usage_records_project_created_provider_model": (
            "(project_id, created_at, provider, response_model)"
        ),
        "ix_usage_records_project_id_started_at": "(project_id, started_at)",
        "ix_usage_records_project_id_call_id": "(project_id, call_id) WHERE (call_id IS NOT NULL)",
    }


def test_check_constraints_match_models(conn):
    db_checks = set(_constraint_defs(conn, "c"))
    model_checks = {
        c.name
        for t in Base.metadata.tables.values()
        for c in t.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert db_checks == model_checks
    assert len(db_checks) == 53  # 1 + 3 + 7 + 18 + 24
