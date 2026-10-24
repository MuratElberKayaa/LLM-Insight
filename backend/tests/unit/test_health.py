import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api import health
from app.main import create_app
from tests.helpers import make_settings


@pytest.fixture
def client():
    with TestClient(create_app(make_settings())) as c:
        yield c


def test_live_ok_without_db_access(client, monkeypatch):
    def must_not_run(_engine):
        raise AssertionError("/health/live must not touch the database")

    monkeypatch.setattr(health, "ping", must_not_run)
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "live"}
    assert r.headers["cache-control"] == "no-store"


def test_ready_ok_when_ping_succeeds(client, monkeypatch):
    monkeypatch.setattr(health, "ping", lambda _engine: None)
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_ready_503_on_db_error_without_details(client, monkeypatch, caplog):
    def fail(_engine):
        raise OperationalError("SELECT 1", {}, Exception("password=hunter2 host=db"))

    monkeypatch.setattr(health, "ping", fail)
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready"}
    assert "hunter2" not in r.text
    assert "hunter2" not in caplog.text
    assert "OperationalError" in caplog.text


def test_ready_503_within_one_second_bound_when_db_hangs(client, monkeypatch):
    monkeypatch.setattr(health, "ping", lambda _engine: time.sleep(1.6))
    start = time.monotonic()
    r = client.get("/health/ready")
    elapsed = time.monotonic() - start
    assert r.status_code == 503
    assert elapsed < 1.5  # 1 s bound plus scheduling slack


def test_ready_503_with_real_driver_against_closed_port(client):
    """No monkeypatching: the real psycopg connect to 127.0.0.1:1 must fail -> 503."""
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready"}


def test_no_otlp_route_in_phase_1a(client):
    paths = set(client.app.openapi()["paths"])
    assert paths == {"/health/live", "/health/ready"}
    assert client.post("/v1/traces").status_code == 404  # OTLP ingest is Phase 1B
