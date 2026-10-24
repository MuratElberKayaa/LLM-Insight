"""T31: upgrade -> downgrade -> upgrade on a disposable DB, and the migrated schema matches
ARCHITECTURE.md §4.2–4.7 column for column. Phase 2B adds migration 0002 (`model_prices`,
the deferred `usage_records.model_price_id` FK, append-only triggers) and its data cases."""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.models import Base
from tests.helpers import REPO_ROOT, alembic_config

pytestmark = pytest.mark.integration

PHASE1_TABLES = ["projects", "project_api_keys", "traces", "spans", "usage_records"]
TABLES = [*PHASE1_TABLES, "model_prices"]

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
    assert _tables(url) == {*TABLES, "alembic_version"}
    first = _schema(url)

    command.downgrade(cfg, "0001")
    assert _tables(url) == {*PHASE1_TABLES, "alembic_version"}

    command.downgrade(cfg, "base")
    assert _tables(url) == {"alembic_version"}  # only the (empty) version table remains

    command.upgrade(cfg, "head")
    assert _tables(url) == {*TABLES, "alembic_version"}
    assert _schema(url) == first  # recreate is identical


def test_models_and_migration_have_no_drift(engine):
    with engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == []


def test_schema_matches_architecture_contract(migrated_url):
    contract = _contract()
    schema = _schema(migrated_url)
    assert set(schema) == {*TABLES, "alembic_version"}
    for table in TABLES:
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
        "fk_usage_records_model_prices": "FOREIGN KEY (model_price_id)"
        " REFERENCES model_prices(id) ON DELETE RESTRICT",
    }
    # No FK on spans.parent_span_id: orphans are allowed (T27).


def test_unique_constraints(conn):
    assert _constraint_defs(conn, "u") == {
        "uq_projects_name": "UNIQUE (name)",
        "uq_project_api_keys_prefix": "UNIQUE (prefix)",
        "uq_traces_project_id_trace_id": "UNIQUE (project_id, trace_id)",
        "uq_spans_project_id_trace_id_span_id": "UNIQUE (project_id, trace_id, span_id)",
        "uq_usage_records_project_id_canonical_call_key": "UNIQUE (project_id, canonical_call_key)",
        "uq_usage_records_project_id_trace_id_span_id": "UNIQUE (project_id, trace_id, span_id)",
        "uq_model_prices_provider_model_version": "UNIQUE (provider, model, version)",
        "uq_model_prices_provider_model_effective_from": (
            "UNIQUE (provider, model, effective_from)"
        ),
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
    assert len(db_checks) == 64  # 1 + 3 + 7 + 18 + 24 + 11 (model_prices)


def test_model_prices_append_only_triggers_installed(conn):
    rows = conn.execute(
        text(
            "SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger"
            " WHERE tgrelid = 'model_prices'::regclass AND NOT tgisinternal"
        )
    ).all()
    defs = dict(rows)
    assert set(defs) == {"model_prices_append_only_rows", "model_prices_append_only_truncate"}
    assert "BEFORE INSERT OR DELETE OR UPDATE" in defs["model_prices_append_only_rows"]
    assert "FOR EACH ROW" in defs["model_prices_append_only_rows"]
    assert "BEFORE TRUNCATE" in defs["model_prices_append_only_truncate"]


# ---------------------------------------------------------------- 0001 -> 0002 with data
_T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
_TRACE, _SPAN = "4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"


def _phase1_rows(c) -> None:
    """Telemetry written under schema 0001 (no price could exist: cost unknown)."""
    c.execute(text("INSERT INTO projects (id, name) VALUES (1, 'demo')"))
    c.execute(
        text(
            "INSERT INTO traces (project_id, trace_id, start_ns, end_ns, started_at, ended_at,"
            " status, span_count) VALUES (1, :t, 1, 2, :at, :at, 'ok', 1)"
        ),
        {"t": _TRACE, "at": _T0},
    )
    c.execute(
        text(
            "INSERT INTO spans (project_id, trace_id, span_id, name, kind, start_ns, end_ns,"
            " status, attributes_schema_version) VALUES"
            " (1, :t, :s, 'chat gpt-5-mini', 'CLIENT', 1, 2, 'OK', 'llminsight.attrmap.v1')"
        ),
        {"t": _TRACE, "s": _SPAN},
    )
    c.execute(
        text(
            "INSERT INTO usage_records (project_id, canonical_call_key, call_key_source,"
            " trace_id, span_id, operation_name, provider, requested_model, input_tokens,"
            " output_tokens, usage_source, cost_status, cost_unknown_reason, provenance,"
            " started_at) VALUES (1, 'resp:openai:chatcmpl-SYNTHETIC-mig',"
            " 'provider_response_id', :t, :s, 'chat', 'openai', 'gpt-5-mini', 10, 5,"
            " 'provider_reported', 'unknown', 'price_missing', 'synthetic', :at)"
        ),
        {"t": _TRACE, "s": _SPAN, "at": _T0},
    )


def _dump(url: str) -> dict[str, list[tuple]]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            return {
                t: [tuple(r) for r in c.execute(text(f"SELECT * FROM {t} ORDER BY id"))]  # noqa: S608 - constant names
                for t in PHASE1_TABLES
            }
    finally:
        eng.dispose()


def _exec(url: str, fn) -> None:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.begin() as c:
            fn(c)
    finally:
        eng.dispose()


def _scalar(url: str, sql: str):
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).one()
    finally:
        eng.dispose()


def test_upgrade_from_0001_preserves_existing_telemetry(disposable_db):
    url = disposable_db("llminsight_migration_data_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0001")
    _exec(url, _phase1_rows)
    before = _dump(url)

    command.upgrade(cfg, "head")
    assert _dump(url) == before  # no telemetry row changed, none repriced
    usage = _scalar(url, "SELECT cost_status, cost_usd, model_price_id FROM usage_records")
    assert tuple(usage) == ("unknown", None, None)
    assert _scalar(url, "SELECT count(*) FROM model_prices")[0] == 0

    command.downgrade(cfg, "0001")  # nothing references a price: allowed, data kept
    assert _dump(url) == before


def _price_and_computed_usage(c) -> None:
    """A SYNTHETIC price and a usage row priced with it (labelled synthetic everywhere)."""
    price_id = c.execute(
        text(
            "INSERT INTO model_prices (version, provider, model, effective_from,"
            " input_per_million, output_per_million, source_url, verified_at) VALUES"
            " ('synthetic-mig-v1', 'synthetic.test', 'synthetic-mig-model', :at, 1, 2,"
            " 'https://example.invalid/synthetic', :at) RETURNING id"
        ),
        {"at": _T0},
    ).scalar_one()
    c.execute(
        text(
            "UPDATE usage_records SET cost_status = 'computed', cost_unknown_reason = NULL,"
            " cost_usd = :cost, price_version = 'synthetic-mig-v1', model_price_id = :pid"
        ),
        {"cost": Decimal("0.0000200000"), "pid": price_id},
    )


def test_downgrade_refused_while_a_usage_row_references_a_price(disposable_db):
    url = disposable_db("llminsight_migration_ref_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0001")
    _exec(url, _phase1_rows)
    command.upgrade(cfg, "head")
    _exec(url, _price_and_computed_usage)
    before = _dump(url)

    with pytest.raises(RuntimeError, match="refusing to downgrade: 1 usage_records rows"):
        command.downgrade(cfg, "0001")
    assert _tables(url) == {*TABLES, "alembic_version"}  # nothing was dropped
    assert _dump(url) == before
    assert _scalar(url, "SELECT version_num FROM alembic_version")[0] == "0002"
    assert _scalar(url, "SELECT count(*) FROM model_prices")[0] == 1


def test_fk_rejects_a_dangling_model_price_id(conn):
    """The FK is real: a usage row cannot point at a price that does not exist."""
    _phase1_rows(conn)
    with pytest.raises(IntegrityError, match="fk_usage_records_model_prices"):
        conn.execute(text("UPDATE usage_records SET model_price_id = 999999"))
