"""Disposable PostgreSQL databases for integration tests.

Each database is created fresh on the docker-compose server (same credentials as
LLMI_DATABASE_URL) and dropped afterwards. Names must end in `_test`, so the dev
database is never touched. If the server is unreachable the tests are skipped with a
reason; set LLMI_TEST_REQUIRE_DB=1 to turn that skip into a failure (used for gates).
"""

import os
import threading
import time
from collections.abc import Callable, Iterator
from typing import NamedTuple

import pytest
import uvicorn
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.main import create_app
from app.repositories import api_keys
from tests.helpers import DUMMY_PEPPER, alembic_config, make_settings

REQUIRE_DB = os.environ.get("LLMI_TEST_REQUIRE_DB") == "1"


class ProjectKey(NamedTuple):
    project_id: int
    prefix: str
    key: str


class LiveServer(NamedTuple):
    url: str
    app: FastAPI


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


# ---------------------------------------------------------------- Phase 1B: ingest fixtures
# A separate disposable database whose tables are truncated before every ingest test, so
# committed ingest rows never leak between tests (the constraint tests above roll back).

INGEST_TABLES = "projects, project_api_keys, traces, spans, usage_records"


@pytest.fixture(scope="session")
def ingest_url(disposable_db) -> str:
    url = disposable_db("llminsight_ingest_test")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def ingest_engine(ingest_url) -> Iterator[Engine]:
    eng = create_engine(ingest_url, poolclass=NullPool, hide_parameters=True)
    yield eng
    eng.dispose()


@pytest.fixture
def db(ingest_engine) -> Engine:
    with ingest_engine.begin() as c:
        c.execute(text(f"TRUNCATE {INGEST_TABLES} RESTART IDENTITY CASCADE"))
    return ingest_engine


@pytest.fixture
def ingest_settings(ingest_url, db) -> Settings:
    return make_settings(database_url=ingest_url)


@pytest.fixture
def client(ingest_settings) -> Iterator[TestClient]:
    with TestClient(create_app(ingest_settings)) as c:
        yield c


@pytest.fixture
def make_key(db) -> Callable[..., ProjectKey]:
    """Create a project (if needed) plus a fresh ingest key; returns ids and the raw key."""

    def _make(project: str = "demo") -> ProjectKey:
        with Session(db) as s, s.begin():
            pid = api_keys.get_or_create_project(s, project)
            generated = api_keys.create_key(s, pid, DUMMY_PEPPER.encode())
        return ProjectKey(pid, generated.prefix, generated.key)

    return _make


@pytest.fixture
def live_server(ingest_settings) -> Iterator[LiveServer]:
    """The real app under uvicorn on a free loopback port (for the OTel exporter and for
    concurrent HTTP clients)."""
    app = create_app(ingest_settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            pytest.fail("uvicorn test server did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield LiveServer(f"http://127.0.0.1:{port}", app)
    server.should_exit = True
    thread.join(timeout=15)
