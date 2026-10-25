"""T31: upgrade -> downgrade -> upgrade on a disposable DB, and the migrated schema matches
ARCHITECTURE.md §4.2–4.10 column for column. Phase 2B adds migration 0002 (`model_prices`,
the deferred `usage_records.model_price_id` FK, append-only triggers) and its data cases;
Phase 4B adds 0003 (`test_datasets`, `failure_reviews`, `test_cases`) and its data cases;
Phase 4C Gate A adds 0004 (no column: a partial unique index and two integrity triggers) and
its data cases; Gate B adds 0005 (§4.11 `experiments`, §4.12 `experiment_results`, two integrity
triggers) and its data cases."""

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
PHASE2_TABLES = [*PHASE1_TABLES, "model_prices"]
PHASE4B_TABLES = ["test_datasets", "failure_reviews", "test_cases"]
PHASE4C_TABLES = ["experiments", "experiment_results"]
PRE_EXPERIMENT_TABLES = [*PHASE2_TABLES, *PHASE4B_TABLES]
TABLES = [*PRE_EXPERIMENT_TABLES, *PHASE4C_TABLES]
HEAD = "0005"
GATE_A_TRIGGERS = {
    "failure_reviews": {"failure_reviews_integrity"},
    "test_cases": {"test_cases_review_eligibility"},
}
TRIGGERS = {
    **GATE_A_TRIGGERS,
    "experiments": {"experiments_integrity"},
    "experiment_results": {"experiment_results_integrity"},
}

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


def _triggers(url: str) -> dict[str, set[str]]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            out: dict[str, set[str]] = {}
            for table, name in c.execute(
                text(
                    "SELECT tgrelid::regclass::text, tgname FROM pg_trigger"
                    " WHERE NOT tgisinternal AND tgrelid::regclass::text IN"
                    " ('failure_reviews', 'test_cases', 'experiments', 'experiment_results')"
                )
            ):
                out.setdefault(table, set()).add(name)
            return out
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

    command.downgrade(cfg, "0004")
    assert _tables(url) == {*PRE_EXPERIMENT_TABLES, "alembic_version"}
    assert _triggers(url) == GATE_A_TRIGGERS

    command.downgrade(cfg, "0003")
    assert _tables(url) == {*PRE_EXPERIMENT_TABLES, "alembic_version"}
    assert _triggers(url) == {}  # 0004 removed its triggers and index only

    command.downgrade(cfg, "0002")
    assert _tables(url) == {*PHASE2_TABLES, "alembic_version"}

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
        "fk_test_datasets_projects": "FOREIGN KEY (project_id)"
        " REFERENCES projects(id) ON DELETE CASCADE",
        "fk_failure_reviews_projects": "FOREIGN KEY (project_id)"
        " REFERENCES projects(id) ON DELETE CASCADE",
        "fk_test_cases_test_datasets": "FOREIGN KEY (dataset_id)"
        " REFERENCES test_datasets(id) ON DELETE CASCADE",
        "fk_test_cases_failure_reviews": "FOREIGN KEY (origin_review_id)"
        " REFERENCES failure_reviews(id) ON DELETE SET NULL",
        "fk_experiments_test_datasets": "FOREIGN KEY (dataset_id)"
        " REFERENCES test_datasets(id) ON DELETE CASCADE",
        "fk_experiment_results_experiments": "FOREIGN KEY (experiment_id)"
        " REFERENCES experiments(id) ON DELETE CASCADE",
        "fk_experiment_results_test_cases": "FOREIGN KEY (test_case_id)"
        " REFERENCES test_cases(id) ON DELETE CASCADE",
    }
    # No FK on spans.parent_span_id: orphans are allowed (T27). No FK from failure_reviews or
    # test_cases to traces: human work references telemetry softly (§4.1, survives retention).


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
        "uq_test_datasets_project_id_name": "UNIQUE (project_id, name)",
        "uq_experiment_results_experiment_id_test_case_id_prompt_version": (
            "UNIQUE (experiment_id, test_case_id, prompt_version)"
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
        "ix_failure_reviews_project_id_trace_id": "(project_id, trace_id)",
        "ix_test_cases_dataset_id": "(dataset_id)",
        "ix_experiments_dataset_id": "(dataset_id)",
    }


def test_open_review_partial_unique_index(conn):
    """§4.9: at most one not-closed review per (project, trace, issue_type)."""
    row = conn.execute(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname = 'uq_failure_reviews_project_id_trace_id_issue_type'"
        )
    ).scalar_one()
    assert row.startswith("CREATE UNIQUE INDEX")
    assert row.split(" USING btree ", 1)[1] == (
        "(project_id, trace_id, issue_type) WHERE (review_status <> 'closed'::text)"
    )


def test_check_constraints_match_models(conn):
    db_checks = set(_constraint_defs(conn, "c"))
    model_checks = {
        c.name
        for t in Base.metadata.tables.values()
        for c in t.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert db_checks == model_checks
    # 1 + 3 + 7 + 18 + 24 + 11 (model_prices) + 1 (test_datasets) + 9 (failure_reviews)
    # + 4 (test_cases) + 8 (experiments) + 11 (experiment_results)
    assert len(db_checks) == 97


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
    command.upgrade(cfg, "0002")
    _exec(url, _price_and_computed_usage)
    before = _dump(url)

    with pytest.raises(RuntimeError, match="refusing to downgrade: 1 usage_records rows"):
        command.downgrade(cfg, "0001")
    assert _tables(url) == {*PHASE2_TABLES, "alembic_version"}  # nothing was dropped
    assert _dump(url) == before
    assert _scalar(url, "SELECT version_num FROM alembic_version")[0] == "0002"
    assert _scalar(url, "SELECT count(*) FROM model_prices")[0] == 1


def test_fk_rejects_a_dangling_model_price_id(conn):
    """The FK is real: a usage row cannot point at a price that does not exist."""
    _phase1_rows(conn)
    with pytest.raises(IntegrityError, match="fk_usage_records_model_prices"):
        conn.execute(text("UPDATE usage_records SET model_price_id = 999999"))


# ---------------------------------------------------------------- 0002 -> 0003 with data
_REVIEW = (
    "INSERT INTO failure_reviews (project_id, trace_id, issue_type, evidence, hypothesis,"
    " created_by) VALUES (1, :t, 'schema_violation', 'SYNTHETIC evidence',"
    " 'SYNTHETIC hypothesis', 'migration-test') RETURNING id"
)
_EXACT_RULE = '{"type": "exact"}'


def _dump_priced(url: str) -> dict[str, list[tuple]]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            return {
                t: [tuple(r) for r in c.execute(text(f"SELECT * FROM {t} ORDER BY id"))]  # noqa: S608 - constant names
                for t in PHASE2_TABLES
            }
    finally:
        eng.dispose()


def test_upgrade_from_0002_preserves_telemetry_usage_and_prices(disposable_db):
    url = disposable_db("llminsight_migration_4b_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0002")
    _exec(url, _phase1_rows)
    _exec(url, _price_and_computed_usage)
    before = _dump_priced(url)

    command.upgrade(cfg, "head")
    assert _scalar(url, "SELECT version_num FROM alembic_version")[0] == HEAD
    assert _dump_priced(url) == before  # telemetry, usage, costs and prices unchanged
    for table in [*PHASE4B_TABLES, *PHASE4C_TABLES]:
        assert _scalar(url, f"SELECT count(*) FROM {table}")[0] == 0  # noqa: S608 - constant names

    command.downgrade(cfg, "0002")  # no human rows: allowed, and nothing else is touched
    assert _tables(url) == {*PHASE2_TABLES, "alembic_version"}
    assert _dump_priced(url) == before


@pytest.mark.parametrize("table", PHASE4B_TABLES)
def test_downgrade_refused_while_human_rows_exist(disposable_db, table):
    url = disposable_db(f"llminsight_migration_4b_{table}_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0002")
    _exec(url, _phase1_rows)
    command.upgrade(cfg, "head")

    def add(c) -> None:
        if table == "failure_reviews":
            c.execute(text(_REVIEW), {"t": _TRACE})
            return
        ds = c.execute(
            text("INSERT INTO test_datasets (project_id, name) VALUES (1, 'mig') RETURNING id")
        ).scalar_one()
        if table == "test_cases":
            c.execute(
                text(
                    "INSERT INTO test_cases (dataset_id, sanitized_input, rule,"
                    " redaction_applied, approved_by, approved_at) VALUES"
                    " (:d, 'SYNTHETIC input', CAST(:r AS jsonb), true, 'mig', now())"
                ),
                {"d": ds, "r": _EXACT_RULE},
            )

    _exec(url, add)
    before = _dump_priced(url)
    with pytest.raises(RuntimeError, match="refusing to downgrade"):
        command.downgrade(cfg, "0002")
    assert _tables(url) == {*TABLES, "alembic_version"}  # nothing was dropped
    assert _scalar(url, "SELECT version_num FROM alembic_version")[0] == HEAD
    assert _triggers(url) == TRIGGERS  # the whole downgrade was rolled back
    assert _scalar(url, f"SELECT count(*) FROM {table}")[0] == 1  # noqa: S608 - constant names
    assert _dump_priced(url) == before


def test_review_constraints_enforced_by_the_database(conn):
    """§4.9 checks hold in the database, not only in the API."""
    _phase1_rows(conn)
    base = {
        "trace_id": _TRACE,
        "span_id": None,
        "issue_type": "unknown",
        "evidence": "e",
        "hypothesis": "h",
        "review_status": "open",
        "reviewer_decision": "pending",
        "created_by": "c",
    }
    bad = {
        "ck_failure_reviews_issue_type_enum": {"issue_type": "root_cause"},
        "ck_failure_reviews_review_status_enum": {"review_status": "approved"},
        "ck_failure_reviews_reviewer_decision_enum": {"reviewer_decision": "proven"},
        "ck_failure_reviews_closed_needs_decision": {"review_status": "closed"},
        "ck_failure_reviews_span_id_hex16": {"span_id": "0" * 16},
        "ck_failure_reviews_trace_id_hex32": {"trace_id": "A" * 32},
        "ck_failure_reviews_evidence_len": {"evidence": "x" * 4001},
        "ck_failure_reviews_hypothesis_len": {"hypothesis": ""},
        "ck_failure_reviews_created_by_len": {"created_by": "x" * 101},
    }
    for constraint, override in bad.items():
        nested = conn.begin_nested()
        with pytest.raises(IntegrityError, match=constraint):
            conn.execute(
                text(
                    "INSERT INTO failure_reviews (project_id, trace_id, span_id, issue_type,"
                    " evidence, hypothesis, review_status, reviewer_decision, created_by)"
                    " VALUES (1, :trace_id, :span_id, :issue_type, :evidence, :hypothesis,"
                    " :review_status, :reviewer_decision, :created_by)"
                ),
                {**base, **override},
            )
        nested.rollback()


@pytest.mark.parametrize("rule", ['{"type": "llm_judge"}', '{"schema": {}}', "[]", '"exact"'])
def test_case_rule_type_enforced_by_the_database(conn, rule):
    """§4.10 `rule->>'type' IN (...)`, including a missing type (NULL would pass a bare IN)."""
    _phase1_rows(conn)
    ds = conn.execute(
        text("INSERT INTO test_datasets (project_id, name) VALUES (1, 'c') RETURNING id")
    ).scalar_one()
    with pytest.raises(IntegrityError, match="ck_test_cases_rule_type_enum"):
        conn.execute(
            text(
                "INSERT INTO test_cases (dataset_id, sanitized_input, rule,"
                " redaction_applied, approved_by, approved_at)"
                " VALUES (:d, 'i', CAST(:r AS jsonb), true, 'a', now())"
            ),
            {"d": ds, "r": rule},
        )


def test_open_review_uniqueness_and_review_fk_set_null(conn):
    _phase1_rows(conn)
    first = conn.execute(text(_REVIEW), {"t": _TRACE}).scalar_one()
    nested = conn.begin_nested()
    with pytest.raises(IntegrityError, match="uq_failure_reviews_project_id_trace_id_issue_type"):
        conn.execute(text(_REVIEW), {"t": _TRACE})
    nested.rollback()
    # Once the first one is closed (with a decision), a new open review of that type is allowed.
    conn.execute(
        text(
            "UPDATE failure_reviews SET review_status = 'closed',"
            " reviewer_decision = 'inconclusive' WHERE id = :i"
        ),
        {"i": first},
    )
    second = conn.execute(text(_REVIEW), {"t": _TRACE}).scalar_one()
    conn.execute(  # Phase 4C (0004): only a decided, eligible review can be cited by a case
        text("UPDATE failure_reviews SET reviewer_decision = 'hypothesis_supported' WHERE id = :i"),
        {"i": second},
    )
    ds = conn.execute(
        text("INSERT INTO test_datasets (project_id, name) VALUES (1, 'fk') RETURNING id")
    ).scalar_one()
    case = conn.execute(
        text(
            "INSERT INTO test_cases (dataset_id, origin_trace_id, origin_review_id,"
            " sanitized_input, rule, redaction_applied, approved_by, approved_at) VALUES"
            " (:d, :t, :r, 'i', CAST(:rule AS jsonb), true, 'a', now()) RETURNING id"
        ),
        {"d": ds, "t": _TRACE, "r": second, "rule": _EXACT_RULE},
    ).scalar_one()
    conn.execute(text("DELETE FROM failure_reviews WHERE id = :i"), {"i": second})
    row = conn.execute(
        text("SELECT origin_review_id, origin_trace_id FROM test_cases WHERE id = :i"),
        {"i": case},
    ).one()
    assert tuple(row) == (None, _TRACE)  # lineage to the trace survives the review's removal


# ---------------------------------------------------------------- 0004 (Phase 4C Gate A)
class _constraint:  # noqa: N801 - a context manager like pytest.raises
    """`pytest.raises(IntegrityError)` that also checks the reported constraint name (trigger
    errors carry it in `diag.constraint_name`, not in the message)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        self._cm = pytest.raises(IntegrityError)
        self._info = self._cm.__enter__()
        return self._info

    def __exit__(self, *exc):
        handled = self._cm.__exit__(*exc)
        assert self._info.value.orig.diag.constraint_name == self.name
        return handled


def test_case_review_partial_unique_index(conn):
    """Decision D5 in the database: one case per (dataset, review); standalone cases free."""
    row = conn.execute(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname = 'uq_test_cases_dataset_id_origin_review_id'"
        )
    ).scalar_one()
    assert row.startswith("CREATE UNIQUE INDEX")
    assert row.split(" USING btree ", 1)[1] == (
        "(dataset_id, origin_review_id) WHERE (origin_review_id IS NOT NULL)"
    )


def test_integrity_triggers_installed(migrated_url):
    assert _triggers(migrated_url) == TRIGGERS


def _decided_review(c, decision="hypothesis_supported"):
    rid = c.execute(text(_REVIEW), {"t": _TRACE}).scalar_one()
    c.execute(
        text("UPDATE failure_reviews SET reviewer_decision = :d WHERE id = :i"),
        {"d": decision, "i": rid},
    )
    return rid


def _case(c, ds, review_id, expected="{}", trace=_TRACE):
    return c.execute(
        text(
            "INSERT INTO test_cases (dataset_id, origin_trace_id, origin_review_id,"
            " sanitized_input, expected_output, rule, redaction_applied, approved_by,"
            " approved_at) VALUES (:d, :t, :r, 'SYNTHETIC input', CAST(:e AS jsonb),"
            " CAST(:rule AS jsonb), true, 'mig', now()) RETURNING id"
        ),
        {"d": ds, "t": trace, "r": review_id, "e": expected, "rule": _EXACT_RULE},
    ).scalar_one()


def _dataset(c, name="mig"):
    return c.execute(
        text("INSERT INTO test_datasets (project_id, name) VALUES (1, :n) RETURNING id"),
        {"n": name},
    ).scalar_one()


def _all_rows(url: str) -> dict[str, list[tuple]]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            return {
                t: [tuple(r) for r in c.execute(text(f"SELECT * FROM {t} ORDER BY id"))]  # noqa: S608 - constant names
                for t in PRE_EXPERIMENT_TABLES
            }
    finally:
        eng.dispose()


def test_upgrade_to_0004_keeps_existing_human_rows_and_downgrade_keeps_them(disposable_db):
    url = disposable_db("llminsight_migration_4c_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0003")
    _exec(url, _phase1_rows)

    def add(c) -> None:
        ds = _dataset(c)
        _case(c, ds, _decided_review(c))
        _case(c, ds, None, trace=None)  # standalone
        _case(c, ds, None, trace=None)  # a second standalone case is still allowed

    _exec(url, add)
    before = _all_rows(url)
    command.upgrade(cfg, "0004")
    assert _all_rows(url) == before
    assert _triggers(url) == GATE_A_TRIGGERS
    command.downgrade(cfg, "0003")
    assert _all_rows(url) == before
    assert _triggers(url) == {}


@pytest.mark.parametrize("problem", ["duplicate", "inconclusive", "rejected_without_expected"])
def test_upgrade_to_0004_refuses_rows_the_new_rules_reject(disposable_db, problem):
    url = disposable_db(f"llminsight_migration_4c_{problem}_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0003")
    _exec(url, _phase1_rows)

    def add(c) -> None:
        ds = _dataset(c)
        if problem == "duplicate":
            rid = _decided_review(c)
            _case(c, ds, rid)
            _case(c, ds, rid)
        elif problem == "inconclusive":
            _case(c, ds, _decided_review(c, "inconclusive"))
        else:
            _case(c, ds, _decided_review(c, "hypothesis_rejected"), expected=None)

    _exec(url, add)
    before = _all_rows(url)
    with pytest.raises(RuntimeError, match="refusing to upgrade"):
        command.upgrade(cfg, "0004")
    assert _scalar(url, "SELECT version_num FROM alembic_version")[0] == "0003"
    assert _all_rows(url) == before  # reported, never rewritten
    assert _triggers(url) == {}


def test_review_triggers_enforce_immutability_and_the_decision_freeze(conn):
    _phase1_rows(conn)
    rid = _decided_review(conn)
    for column, value in (
        ("evidence", "SYNTHETIC changed evidence"),
        ("hypothesis", "SYNTHETIC changed hypothesis"),
        ("issue_type", "unknown"),
        ("created_by", "someone-else"),
        ("span_id", "00f067aa0ba902b8"),
    ):
        nested = conn.begin_nested()
        with _constraint("failure_reviews_immutable"):
            conn.execute(
                text(f"UPDATE failure_reviews SET {column} = :v WHERE id = :i"),  # noqa: S608 - constant names
                {"v": value, "i": rid},
            )
        nested.rollback()
    # No case yet: the decision may still change.
    conn.execute(
        text("UPDATE failure_reviews SET reviewer_decision = 'inconclusive' WHERE id = :i"),
        {"i": rid},
    )
    conn.execute(
        text("UPDATE failure_reviews SET reviewer_decision = 'hypothesis_supported' WHERE id = :i"),
        {"i": rid},
    )
    _case(conn, _dataset(conn), rid)
    for decision in ("pending", "inconclusive", "hypothesis_rejected"):
        nested = conn.begin_nested()
        with _constraint("failure_reviews_decision_frozen"):
            conn.execute(
                text("UPDATE failure_reviews SET reviewer_decision = :d WHERE id = :i"),
                {"d": decision, "i": rid},
            )
        nested.rollback()
    # The status may still change within the §4.9 invariant.
    conn.execute(
        text(
            "UPDATE failure_reviews SET review_status = 'closed', updated_at = now() WHERE id = :i"
        ),
        {"i": rid},
    )


@pytest.mark.parametrize(
    ("decision", "expected", "trace"),
    [
        ("pending", "{}", _TRACE),
        ("inconclusive", "{}", _TRACE),
        ("hypothesis_rejected", None, _TRACE),
        ("hypothesis_supported", "{}", "5bf92f3577b34da6a3ce929d0e0e4737"),
    ],
    ids=["pending", "inconclusive", "rejected-without-expected", "other-trace"],
)
def test_case_trigger_refuses_ineligible_reviews(conn, decision, expected, trace):
    _phase1_rows(conn)
    rid = conn.execute(text(_REVIEW), {"t": _TRACE}).scalar_one()
    if decision != "pending":
        conn.execute(
            text("UPDATE failure_reviews SET reviewer_decision = :d WHERE id = :i"),
            {"d": decision, "i": rid},
        )
    ds = _dataset(conn)
    with _constraint("test_cases_review_eligibility"):
        _case(conn, ds, rid, expected=expected, trace=trace)


def test_case_trigger_accepts_eligible_reviews_and_refuses_another_project(conn):
    _phase1_rows(conn)
    ds = _dataset(conn)
    _case(conn, ds, _decided_review(conn))  # supported, with or without expected output
    closed = conn.execute(
        text("UPDATE failure_reviews SET review_status = 'closed' RETURNING id")
    ).scalar_one()
    rejected = _decided_review(conn, "hypothesis_rejected")
    _case(conn, ds, rejected, expected='"SYNTHETIC independent expected"')
    conn.execute(text("INSERT INTO projects (id, name) VALUES (2, 'other')"))
    foreign = conn.execute(
        text("INSERT INTO test_datasets (project_id, name) VALUES (2, 'f') RETURNING id")
    ).scalar_one()
    with _constraint("test_cases_review_eligibility"):
        _case(conn, foreign, closed)


def test_cascades_still_work_with_the_triggers(conn):
    """Deleting a review sets the case reference NULL (§4.10); deleting the project removes
    reviews, datasets and cases (the triggers never block a cascade)."""
    _phase1_rows(conn)
    ds = _dataset(conn)
    rid = _decided_review(conn)
    case = _case(conn, ds, rid)
    conn.execute(text("DELETE FROM failure_reviews WHERE id = :i"), {"i": rid})
    assert (
        conn.execute(
            text("SELECT origin_review_id FROM test_cases WHERE id = :i"), {"i": case}
        ).scalar_one()
        is None
    )
    _case(conn, ds, _decided_review(conn))
    conn.execute(text("DELETE FROM projects WHERE id = 1"))
    for table in PHASE4B_TABLES:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0  # noqa: S608 - constant names


def test_partial_unique_index_blocks_a_second_case_per_review(conn):
    _phase1_rows(conn)
    ds = _dataset(conn)
    rid = _decided_review(conn)
    _case(conn, ds, rid)
    nested = conn.begin_nested()
    with pytest.raises(IntegrityError, match="uq_test_cases_dataset_id_origin_review_id"):
        _case(conn, ds, rid)
    nested.rollback()
    _case(conn, _dataset(conn, "second"), rid)  # another dataset: allowed


# ---------------------------------------------------------------- 0005 (Phase 4C Gate B)
def _experiment(c, ds, mode="mock", a="golden-prompt-v1", b="golden-prompt-v2", budget=None):
    return c.execute(
        text(
            "INSERT INTO experiments (dataset_id, prompt_version_a, prompt_version_b, model, mode,"
            " budget_usd) VALUES (:d, :a, :b, 'synthetic-model', :m, :budget) RETURNING id"
        ),
        {"d": ds, "a": a, "b": b, "m": mode, "budget": budget},
    ).scalar_one()


def _result(c, experiment, case, version="golden-prompt-v1", provenance="synthetic", **cols):
    values = {
        "passed": True,
        "evaluation_detail": None,
        "cost_usd": None,
        "cost_status": "unknown",
        "cost_unknown_reason": "price_missing",
        **cols,
    }
    return c.execute(
        text(
            "INSERT INTO experiment_results (experiment_id, test_case_id, prompt_version, passed,"
            " evaluation_detail, cost_usd, cost_status, cost_unknown_reason, provenance) VALUES"
            " (:e, :c, :v, :passed, :evaluation_detail, :cost_usd, :cost_status,"
            " :cost_unknown_reason, :p) RETURNING id"
        ),
        {"e": experiment, "c": case, "v": version, "p": provenance, **values},
    ).scalar_one()


def _standalone_case(c, ds):
    return _case(c, ds, None, trace=None)


def test_experiment_column_checks_are_enforced_by_the_database(conn):
    _phase1_rows(conn)
    ds = _dataset(conn)
    bad = {
        "ck_experiments_prompt_versions_differ": {"a": "v1", "b": "v1"},
        "ck_experiments_real_needs_budget": {"mode": "real"},
        "ck_experiments_budget_usd_positive": {"budget": Decimal("0")},
        "ck_experiments_mode_enum": {"mode": "live"},
        "ck_experiments_prompt_version_a_len": {"a": "x" * 65},
    }
    for constraint, override in bad.items():
        nested = conn.begin_nested()
        with pytest.raises(IntegrityError, match=constraint):
            _experiment(conn, ds, **override)
        nested.rollback()
    _experiment(conn, ds, mode="real", budget=Decimal("0.0000000001"))  # allowed by the schema


def test_result_cost_checks_match_the_ledger_rule(conn):
    _phase1_rows(conn)
    ds = _dataset(conn)
    case = _standalone_case(conn, ds)
    exp = _experiment(conn, ds)
    bad = {
        "ck_experiment_results_cost_computed_has_value": {
            "cost_status": "computed",
            "cost_unknown_reason": None,
        },
        "ck_experiment_results_cost_unknown_has_reason": {"cost_unknown_reason": None},
        "ck_experiment_results_cost_usd_nonneg": {
            "cost_usd": Decimal("-1"),
            "cost_status": "computed",
            "cost_unknown_reason": None,
        },
        "ck_experiment_results_evaluation_detail_maxlen": {"evaluation_detail": "x" * 1001},
        "ck_experiment_results_provenance_enum": {"provenance": "mixed"},
    }
    for constraint, override in bad.items():
        nested = conn.begin_nested()
        kwargs = dict(override)
        provenance = kwargs.pop("provenance", "synthetic")
        # The mock trigger runs before the CHECKs; the provenance enum itself is checked on a
        # real-mode experiment, where the trigger does not constrain provenance.
        target = exp if provenance == "synthetic" else _experiment(conn, ds, "real", budget=1)
        with pytest.raises(IntegrityError, match=constraint):
            _result(conn, target, case, provenance=provenance, **kwargs)
        nested.rollback()
    # Unknown cost stays NULL, never 0; a computed cost keeps its 10 decimal places.
    _result(conn, exp, case, passed=None)
    _result(
        conn,
        exp,
        case,
        version="golden-prompt-v2",
        cost_usd=Decimal("0.0000012345"),
        cost_status="computed",
        cost_unknown_reason=None,
    )
    stored = (
        conn.execute(text("SELECT cost_usd::text FROM experiment_results ORDER BY id"))
        .scalars()
        .all()
    )
    assert stored == [None, "0.0000012345"]
    nested = conn.begin_nested()
    with pytest.raises(IntegrityError, match="uq_experiment_results_experiment_id_test_case_id"):
        _result(conn, exp, case)
    nested.rollback()


@pytest.mark.parametrize(
    "problem", ["foreign_version", "other_dataset_case", "mock_claims_real", "after_finish"]
)
def test_result_trigger_keeps_results_inside_their_experiment(conn, problem):
    _phase1_rows(conn)
    ds = _dataset(conn)
    case = _standalone_case(conn, ds)
    exp = _experiment(conn, ds)
    kwargs = {}
    if problem == "foreign_version":
        kwargs["version"] = "golden-prompt-v3"
    elif problem == "other_dataset_case":
        case = _standalone_case(conn, _dataset(conn, "other"))
    elif problem == "mock_claims_real":
        kwargs["provenance"] = "real"
    else:
        conn.execute(text("UPDATE experiments SET finished_at = now() WHERE id = :i"), {"i": exp})
    with _constraint("experiment_results_integrity"):
        _result(conn, exp, case, **kwargs)


def test_results_are_append_only_and_experiments_finish_once(conn):
    _phase1_rows(conn)
    ds = _dataset(conn)
    exp = _experiment(conn, ds)
    rid = _result(conn, exp, _standalone_case(conn, ds))
    nested = conn.begin_nested()
    with _constraint("experiment_results_append_only"):
        conn.execute(text("UPDATE experiment_results SET passed = false WHERE id = :i"), {"i": rid})
    nested.rollback()
    for column, value in (("model", "other"), ("prompt_version_a", "x"), ("budget_usd", 5)):
        nested = conn.begin_nested()
        with _constraint("experiments_immutable"):
            conn.execute(
                text(f"UPDATE experiments SET {column} = :v WHERE id = :i"),  # noqa: S608 - constant names
                {"v": value, "i": exp},
            )
        nested.rollback()
    conn.execute(text("UPDATE experiments SET finished_at = now() WHERE id = :i"), {"i": exp})
    nested = conn.begin_nested()
    with _constraint("experiments_immutable"):
        conn.execute(text("UPDATE experiments SET finished_at = NULL WHERE id = :i"), {"i": exp})
    nested.rollback()


def test_cascades_remove_experiments_with_their_dataset_and_project(conn):
    _phase1_rows(conn)
    ds = _dataset(conn)
    exp = _experiment(conn, ds)
    _result(conn, exp, _standalone_case(conn, ds))
    conn.execute(text("DELETE FROM test_datasets WHERE id = :i"), {"i": ds})
    assert conn.execute(text("SELECT count(*) FROM experiments")).scalar_one() == 0
    assert conn.execute(text("SELECT count(*) FROM experiment_results")).scalar_one() == 0
    ds2 = _dataset(conn, "again")
    _result(conn, _experiment(conn, ds2), _standalone_case(conn, ds2))
    conn.execute(text("DELETE FROM projects WHERE id = 1"))
    for table in PHASE4C_TABLES:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0  # noqa: S608 - constant names


def test_upgrade_to_0005_keeps_every_row_and_downgrade_refuses_with_experiments(disposable_db):
    url = disposable_db("llminsight_migration_4c_exp_test")
    cfg = alembic_config(url)
    command.upgrade(cfg, "0004")
    _exec(url, _phase1_rows)

    def add_cases(c) -> None:
        ds = _dataset(c)
        _case(c, ds, _decided_review(c))
        _standalone_case(c, ds)

    _exec(url, add_cases)
    before = _all_rows(url)
    command.upgrade(cfg, "0005")
    assert _all_rows(url) == before  # the pre-existing tables are untouched
    assert _triggers(url) == TRIGGERS
    command.downgrade(cfg, "0004")  # no experiment yet: allowed
    assert _all_rows(url) == before
    command.upgrade(cfg, "0005")

    def add_experiment(c) -> None:
        ds = c.execute(text("SELECT id FROM test_datasets")).scalar_one()
        case = c.execute(text("SELECT min(id) FROM test_cases")).scalar_one()
        _result(c, _experiment(c, ds), case)

    _exec(url, add_experiment)
    with pytest.raises(RuntimeError, match="refusing to downgrade: 1 experiments rows"):
        command.downgrade(cfg, "0004")
    assert _scalar(url, "SELECT version_num FROM alembic_version")[0] == "0005"
    assert _scalar(url, "SELECT count(*) FROM experiment_results")[0] == 1
