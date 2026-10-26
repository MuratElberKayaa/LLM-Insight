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


def test_routes_are_health_otlp_ingest_and_read_api(client):
    # Phase 1B adds POST /v1/traces; Phase 1C adds the two /api/v1 trace read endpoints;
    # Phase 2C adds the two aggregate endpoints; Phase 3C adds the bounded project listing;
    # Phase 4A adds the read-only trace diagnostics (ARCHITECTURE.md §5.6); Phase 4B adds
    # failure reviews (CR-03) and datasets / test cases (§5.7); Phase 4C adds the two
    # contract experiment endpoints (§5, §5.9); Phase 4D adds the read-only experiment listing
    # and run options for the dashboard (§5.10).
    assert set(client.app.openapi()["paths"]) == {
        "/api/v1/failure-reviews",
        "/api/v1/failure-reviews/{review_id}",
        "/api/v1/datasets",
        "/api/v1/datasets/{dataset_id}/cases",
        "/api/v1/experiments",
        "/api/v1/experiments/{experiment_id}",
        "/api/v1/experiment-options",
        "/health/live",
        "/health/ready",
        "/v1/traces",
        "/api/v1/traces",
        "/api/v1/traces/{trace_id}",
        "/api/v1/traces/{trace_id}/diagnostics",
        "/api/v1/overview",
        "/api/v1/models/compare",
        "/api/v1/projects",
    }
    # Authentication comes first: no DB access and no route detail without the token.
    r = client.get("/api/v1/traces")
    assert r.status_code == 401
    assert r.json() == {
        "error": {"code": "unauthorized", "message": "missing or invalid credentials"}
    }
