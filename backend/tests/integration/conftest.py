"""Disposable PostgreSQL databases for integration tests.

Each database is created fresh on the docker-compose server (same credentials as
LLMI_DATABASE_URL) and dropped afterwards. Names must end in `_test`, so the dev
database is never touched. If the server is unreachable the tests are skipped with a
reason; set LLMI_TEST_REQUIRE_DB=1 to turn that skip into a failure (used for gates).
"""

import os
from collections.abc import Callable, Iterator

import pytest
from alembic import command
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from tests.helpers import alembic_config, make_settings

REQUIRE_DB = os.environ.get("LLMI_TEST_REQUIRE_DB") == "1"


def _unavailable(reason: str):
    if REQUIRE_DB:
        pytest.fail(f"PostgreSQL required (LLMI_TEST_REQUIRE_DB=1): {reason}")
    pytest.skip(f"PostgreSQL unavailable: {reason}")


def _server_url() -> URL:
    try:
        raw = Settings().database_url.get_secret_value()
    except ValidationError as exc:
        _unavailable(f"LLMI settings invalid or .env missing ({exc.error_count()} errors)")
    return make_url(raw)


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def disposable_db() -> Iterator[Callable[[str], str]]:
    """Factory: create an empty `<name>` database (must end with _test), return its URL."""
    server = _server_url()
    admin = create_engine(
        server.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"connect_timeout": 2},
        hide_parameters=True,
    )
    created: list[str] = []

    def _create(name: str) -> str:
        assert name.endswith("_test") and name.isidentifier(), name
        try:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        except OperationalError as exc:
            _unavailable(type(exc).__name__)
        created.append(name)
        return _render(server.set(database=name))

    yield _create

    if created:  # nothing to drop (and no server to reach) when every test was skipped
        with admin.connect() as conn:
            for name in created:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def migrated_url(disposable_db) -> str:
    url = disposable_db("llminsight_it_test")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def engine(migrated_url) -> Iterator[Engine]:
    eng = create_engine(migrated_url, poolclass=NullPool, hide_parameters=True)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine):
    """Connection inside a transaction that is always rolled back."""
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            yield connection
        finally:
            trans.rollback()


@pytest.fixture
def settings_for(migrated_url) -> Callable[..., Settings]:
    return lambda url=migrated_url: make_settings(database_url=url)
