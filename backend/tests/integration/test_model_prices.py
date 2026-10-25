"""`model_prices` on real PostgreSQL (ARCHITECTURE.md §4.7, docs/PRICING.md §5): column
rules, no ambiguous overlap, append-only history, FK RESTRICT, and the seed CLI.

Rows named `synthetic-*` / provider `synthetic.*` are SYNTHETIC test prices."""

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.pool import NullPool

from app import cli
from app.models import ModelPrice
from app.services import pricing_catalog
from tests.helpers import alembic_config, make_settings

pytestmark = pytest.mark.integration

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _price(**kw):
    return {
        "version": "synthetic-v1",
        "provider": "synthetic.test",
        "model": "synthetic-model",
        "effective_from": T0,
        "input_per_million": Decimal("1"),
        "output_per_million": Decimal("2"),
        "source_url": "https://example.invalid/synthetic",
        "verified_at": T0,
    } | kw


def _rejected(conn, stmt, *, constraint=None, sqlstate=None):
    savepoint = conn.begin_nested()
    with pytest.raises(DBAPIError) as exc:
        conn.execute(stmt)
    savepoint.rollback()
    diag = exc.value.orig.diag
    if constraint is not None:
        assert diag.constraint_name == constraint
    if sqlstate is not None:
        assert exc.value.orig.sqlstate == sqlstate
    return exc.value


# ---------------------------------------------------------------- column rules
def test_valid_row_defaults_and_exact_decimals(conn):
    conn.execute(
        insert(ModelPrice).values(
            **_price(
                input_per_million=Decimal("0.0000000001"),
                output_per_million=Decimal("9999999999.9999999999"),
                cached_input_per_million=Decimal("0.075"),
            )
        )
    )
    row = conn.execute(select(ModelPrice)).one()
    assert row.currency == "USD"
    assert row.created_at.tzinfo is not None
    assert row.input_per_million == Decimal("0.0000000001")
    assert row.output_per_million == Decimal("9999999999.9999999999")
    assert row.cached_input_per_million == Decimal("0.075")
    assert row.cache_write_input_per_million is None
    assert isinstance(row.input_per_million, Decimal)


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        (
            {"input_per_million": Decimal("-0.0000000001")},
            "ck_model_prices_input_per_million_nonneg",
        ),
        ({"output_per_million": Decimal("-1")}, "ck_model_prices_output_per_million_nonneg"),
        (
            {"cached_input_per_million": Decimal("-1")},
            "ck_model_prices_cached_input_per_million_nonneg",
        ),
        (
            {"cache_write_input_per_million": Decimal("-1")},
            "ck_model_prices_cache_write_input_per_million_nonneg",
        ),
        ({"currency": "EUR"}, "ck_model_prices_currency_usd"),
        ({"source_url": "http://example.invalid/x"}, "ck_model_prices_source_url_https"),
        ({"source_url": "example.invalid"}, "ck_model_prices_source_url_https"),
        ({"version": ""}, "ck_model_prices_version_len"),
        ({"version": "v" * 65}, "ck_model_prices_version_len"),
        ({"provider": ""}, "ck_model_prices_provider_format"),  # checked before _len
        ({"provider": "open ai"}, "ck_model_prices_provider_format"),
        ({"provider": "openai:x"}, "ck_model_prices_provider_format"),
        ({"model": "m" * 257}, "ck_model_prices_model_len"),
        ({"model": "gpt-*"}, "ck_model_prices_model_format"),  # no wildcard
        ({"model": "gpt-5-mini%"}, "ck_model_prices_model_format"),
        ({"model": "gpt 5"}, "ck_model_prices_model_format"),
    ],
)
def test_column_rules(conn, overrides, constraint):
    _rejected(conn, insert(ModelPrice).values(**_price(**overrides)), constraint=constraint)


@pytest.mark.parametrize(
    "column",
    [
        "input_per_million",
        "output_per_million",
        "effective_from",
        "verified_at",
        "source_url",
        "version",
    ],
)
def test_required_columns(conn, column):
    values = _price()
    values[column] = None
    _rejected(conn, insert(ModelPrice).values(**values), sqlstate="23502")


def test_no_two_prices_start_at_the_same_instant(conn):
    conn.execute(insert(ModelPrice).values(**_price()))
    _rejected(
        conn,
        insert(ModelPrice).values(**_price(version="synthetic-v2")),
        constraint="uq_model_prices_provider_model_effective_from",
    )


def test_version_is_unique_per_model(conn):
    conn.execute(insert(ModelPrice).values(**_price()))
    _rejected(
        conn,
        insert(ModelPrice).values(**_price(effective_from=T0 + timedelta(days=1))),
        constraint="uq_model_prices_provider_model_version",
    )


def test_same_start_is_fine_for_another_model_or_provider(conn):
    conn.execute(insert(ModelPrice).values(**_price()))
    conn.execute(insert(ModelPrice).values(**_price(model="synthetic-other")))
    conn.execute(insert(ModelPrice).values(**_price(provider="synthetic.other")))


# ---------------------------------------------------------------- append-only history
@pytest.fixture
def stored(conn) -> int:
    return conn.execute(insert(ModelPrice).values(**_price()).returning(ModelPrice.id)).scalar_one()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE model_prices SET input_per_million = 5",
        "UPDATE model_prices SET source_url = 'https://example.invalid/other'",
        "UPDATE model_prices SET effective_from = effective_from - interval '1 day'",
        "DELETE FROM model_prices",
        "TRUNCATE model_prices CASCADE",
    ],
)
def test_stored_prices_are_never_rewritten_or_removed(conn, stored, sql):
    err = _rejected(conn, text(sql), sqlstate="23001")  # restrict_violation
    assert "append-only" in str(err.orig)
    assert conn.execute(select(ModelPrice.input_per_million)).scalar_one() == Decimal("1")


def test_plain_truncate_is_already_refused_by_the_foreign_key(conn, stored):
    _rejected(conn, text("TRUNCATE model_prices"), sqlstate="0A000")
    assert conn.execute(select(ModelPrice.id)).scalar_one() == stored


def test_a_later_price_closes_the_period_of_the_latest_one(conn, stored):
    conn.execute(
        insert(ModelPrice).values(
            **_price(version="synthetic-v2", effective_from=T0 + timedelta(days=30))
        )
    )
    assert conn.execute(
        select(ModelPrice.version).order_by(ModelPrice.effective_from)
    ).scalars().all() == [
        "synthetic-v1",
        "synthetic-v2",
    ]


def test_back_dated_insert_is_rejected(conn, stored):
    err = _rejected(
        conn,
        insert(ModelPrice).values(
            **_price(version="synthetic-v0", effective_from=T0 - timedelta(microseconds=1))
        ),
        sqlstate="23514",  # check_violation
    )
    assert "a later price already exists" in str(err.orig)


def test_back_dating_check_is_per_provider_and_model(conn, stored):
    earlier = T0 - timedelta(days=1)
    conn.execute(insert(ModelPrice).values(**_price(model="synthetic-b", effective_from=earlier)))
    conn.execute(
        insert(ModelPrice).values(**_price(provider="synthetic.b", effective_from=earlier))
    )


def test_applied_price_is_protected_by_fk_restrict_even_without_the_trigger(conn, stored):
    """FK RESTRICT is a second, independent guard: disable the append-only trigger (inside
    this rolled-back transaction) and a referenced price still cannot be deleted."""
    conn.execute(text("INSERT INTO projects (id, name) VALUES (1, 'demo')"))
    conn.execute(
        text(
            "INSERT INTO traces (project_id, trace_id, start_ns, end_ns, started_at, ended_at,"
            " status, span_count) VALUES (1, :t, 1, 2, :at, :at, 'ok', 1)"
        ),
        {"t": "a" * 32, "at": T0},
    )
    conn.execute(
        text(
            "INSERT INTO spans (project_id, trace_id, span_id, name, kind, start_ns, end_ns,"
            " status, attributes_schema_version) VALUES"
            " (1, :t, :s, 'chat', 'CLIENT', 1, 2, 'OK', 'llminsight.attrmap.v1')"
        ),
        {"t": "a" * 32, "s": "b" * 16},
    )
    conn.execute(
        text(
            "INSERT INTO usage_records (project_id, canonical_call_key, call_key_source,"
            " trace_id, span_id, operation_name, provider, input_tokens, output_tokens,"
            " usage_source, cost_usd, cost_status, price_version, model_price_id, provenance,"
            " started_at) VALUES (1, 'span:x', 'span_fallback', :t, :s, 'chat',"
            " 'synthetic.test', 1, 1, 'provider_reported', 0.000003, 'computed',"
            " 'synthetic-v1', :pid, 'synthetic', :at)"
        ),
        {"t": "a" * 32, "s": "b" * 16, "pid": stored, "at": T0},
    )
    conn.execute(text("ALTER TABLE model_prices DISABLE TRIGGER model_prices_append_only_rows"))
    with pytest.raises(IntegrityError) as exc:
        conn.execute(text("DELETE FROM model_prices WHERE id = :id"), {"id": stored})
    assert exc.value.orig.diag.constraint_name == "fk_usage_records_model_prices"


# ---------------------------------------------------------------- seed CLI
@pytest.fixture
def fresh_url(disposable_db) -> str:
    url = disposable_db("llminsight_prices_test")  # recreated empty for every test
    command.upgrade(alembic_config(url), "head")
    return url


def _cli(url, *argv) -> tuple[int, str]:
    out = io.StringIO()
    return cli.run(list(argv), make_settings(database_url=url), out=out), out.getvalue()


def _stored(url) -> list[dict]:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            return [
                dict(r._mapping)
                for r in c.execute(
                    text(
                        "SELECT * FROM model_prices"
                        ' ORDER BY provider COLLATE "C", model COLLATE "C", effective_from'
                    )
                )
            ]
    finally:
        eng.dispose()


def _run_sql(url, sql, **params) -> None:
    eng = create_engine(url, poolclass=NullPool)
    try:
        with eng.begin() as c:
            c.execute(text(sql), params)
    finally:
        eng.dispose()


def test_seed_dry_run_writes_nothing(fresh_url):
    code, out = _cli(fresh_url, "seed-prices", "--dry-run")
    assert code == 0
    assert "would insert 6, unchanged 0 (catalogue openai-2026-10-24)" in out
    assert "would insert openai/gpt-5-mini openai-2026-10-24" in out
    assert _stored(fresh_url) == []


def test_seed_inserts_the_verified_catalogue_exactly_once(fresh_url):
    code, out = _cli(fresh_url, "seed-prices")
    assert code == 0 and "inserted 6, unchanged 0" in out
    rows = _stored(fresh_url)
    expected = sorted(pricing_catalog.ENTRIES, key=lambda e: (e.provider, e.model))
    assert [(r["provider"], r["model"]) for r in rows] == [(e.provider, e.model) for e in expected]
    for r, e in zip(rows, expected, strict=True):
        assert r["version"] == e.version == "openai-2026-10-24"
        assert r["input_per_million"] == e.input_per_million
        assert r["output_per_million"] == e.output_per_million
        assert r["cached_input_per_million"] == e.cached_input_per_million
        assert r["cache_write_input_per_million"] is None
        assert r["currency"] == "USD" and r["source_url"] == e.source_url
        assert r["effective_from"] == e.effective_from and r["verified_at"] == e.verified_at

    code, out = _cli(fresh_url, "seed-prices")  # idempotent
    assert code == 0 and "inserted 0, unchanged 6" in out
    assert _stored(fresh_url) == rows  # nothing rewritten (ids, created_at included)


def test_seed_refuses_to_change_a_differing_stored_price(fresh_url, capsys):
    e = next(e for e in pricing_catalog.ENTRIES if e.model == "gpt-5-mini")
    _run_sql(
        fresh_url,
        "INSERT INTO model_prices (version, provider, model, effective_from,"
        " input_per_million, output_per_million, source_url, verified_at)"
        " VALUES (:v, 'openai', 'gpt-5-mini', :at, 0.30, 2.00, :src, :at)",
        v=e.version,
        at=e.effective_from,
        src=e.source_url,
    )
    before = _stored(fresh_url)
    code, out = _cli(fresh_url, "seed-prices")
    assert code == 1 and out == ""
    assert "stored prices are never changed" in capsys.readouterr().err
    assert _stored(fresh_url) == before  # neither the conflict nor any other row was written


def test_seed_refuses_a_second_version_at_the_same_start(fresh_url, capsys):
    e = next(e for e in pricing_catalog.ENTRIES if e.model == "gpt-4.1-mini")
    _run_sql(
        fresh_url,
        "INSERT INTO model_prices (version, provider, model, effective_from,"
        " input_per_million, output_per_million, source_url, verified_at)"
        " VALUES ('manual-1', 'openai', 'gpt-4.1-mini', :at, 0.40, 1.60, :src, :at)",
        at=e.effective_from,
        src=e.source_url,
    )
    assert _cli(fresh_url, "seed-prices")[0] == 1
    assert "already starts at" in capsys.readouterr().err
    assert len(_stored(fresh_url)) == 1


def test_seed_never_touches_usage_records(fresh_url):
    _run_sql(fresh_url, "INSERT INTO projects (id, name) VALUES (1, 'demo')")
    eng = create_engine(fresh_url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            before = c.execute(text("SELECT count(*) FROM usage_records")).scalar_one()
    finally:
        eng.dispose()
    assert _cli(fresh_url, "seed-prices")[0] == 0
    eng = create_engine(fresh_url, poolclass=NullPool)
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT count(*) FROM usage_records")).scalar_one() == before
    finally:
        eng.dispose()
