from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import Base

TABLES = {
    "projects",
    "project_api_keys",
    "traces",
    "spans",
    "usage_records",
    "model_prices",
    # Phase 4B (§4.8–4.10); experiments (§4.11–4.12) belong to Phase 4C
    "test_datasets",
    "failure_reviews",
    "test_cases",
}


def test_only_phase1_to_phase4b_tables_defined():
    assert set(Base.metadata.tables) == TABLES


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


def test_model_price_fk_added_in_phase2_with_restrict():
    usage = Base.metadata.tables["usage_records"]
    fks = [
        c
        for c in usage.constraints
        if isinstance(c, ForeignKeyConstraint)
        and [col.name for col in c.columns] == ["model_price_id"]
    ]
    assert len(fks) == 1
    assert fks[0].referred_table.name == "model_prices"
    assert fks[0].ondelete == "RESTRICT"  # an applied price can never disappear
