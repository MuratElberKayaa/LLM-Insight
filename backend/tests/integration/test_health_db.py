"""/health/ready against a real PostgreSQL (part of T30's readiness rule)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.main import create_app

pytestmark = pytest.mark.integration


def test_ready_200_with_real_database(settings_for):
    with TestClient(create_app(settings_for())) as client:
        r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_ready_503_when_database_missing_on_real_server(settings_for, migrated_url):
    """Real server, nonexistent database: connection is refused by PostgreSQL -> 503."""
    url = make_url(migrated_url).set(database="llminsight_absent_test")
    with TestClient(create_app(settings_for(url.render_as_string(hide_password=False)))) as client:
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready"}
