from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import Base

PHASE1_TABLES = {"projects", "project_api_keys", "traces", "spans", "usage_records"}


def test_only_phase1_tables_defined():
    assert set(Base.metadata.tables) == PHASE1_TABLES


def test_surrogate_keys_are_bigserial():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "id BIGSERIAL NOT NULL" in ddl, table.name
        assert [c.name for c in table.primary_key.columns] == ["id"]


def test_every_constraint_is_named():
    for table in Base.metadata.sorted_tables:
        for constraint in table.constraints:
            assert constraint.name, f"unnamed constraint on {table.name}"


def test_unknown_cost_checks_present():
    checks = {
        c.name
        for c in Base.metadata.tables["usage_records"].constraints
        if isinstance(c, CheckConstraint)
    }
    assert {
        "ck_usage_records_cost_computed_has_value",
        "ck_usage_records_cost_unknown_has_reason",
        "ck_usage_records_cost_computed_has_price",
    } <= checks


def test_model_price_fk_deferred_to_phase2():
    usage = Base.metadata.tables["usage_records"]
    fk_columns = {
        col.name
        for c in usage.constraints
        if isinstance(c, ForeignKeyConstraint)
        for col in c.columns
    }
    assert "model_price_id" in usage.c
    assert "model_price_id" not in fk_columns  # model_prices arrives in Phase 2
