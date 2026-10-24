"""Database failure: never acknowledge success (T30, T30-S1, ADR-18)."""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.main import create_app
from app.repositories import telemetry
from tests.helpers import make_settings
from tests.integration.ingest_helpers import counts, post, status_of
from tests.otlp import body, load_fixture

pytestmark = pytest.mark.integration


def _boom(*_a, **_k):
    # The original driver message must never reach the response or the log.
    raise OperationalError("INSERT ...", {}, Exception("password=hunter2 host=db.internal"))


def test_t30_s1_db_error_after_partial_insert_rolls_back_then_retry_succeeds(
    client, make_key, db, monkeypatch, caplog
):
    k = make_key()
    caplog.set_level(logging.DEBUG)
    # Fails after traces and spans were already inserted inside the transaction.
    monkeypatch.setattr(telemetry, "_insert_usage", _boom)
    r = post(client, body(load_fixture()), k.key)
    assert r.status_code == 503
    assert r.headers["retry-after"] == "5"
    assert status_of(r).message == "storage unavailable; retry later"
    assert counts(db) == (0, 0, 0)  # nothing from the batch survived
    assert b"hunter2" not in r.content and "hunter2" not in caplog.text

    monkeypatch.undo()
    assert post(client, body(load_fixture()), k.key).status_code == 200
    assert counts(db) == (1, 3, 1)
    assert post(client, body(load_fixture()), k.key).status_code == 200  # idempotent retry
    assert counts(db) == (1, 3, 1)


def test_t30_commit_failure_is_never_acknowledged(client, make_key, db, monkeypatch):
    k = make_key()
    real_commit = Session.commit

    def failing_commit(self):
        raise OperationalError("COMMIT", {}, Exception("connection lost"))

    monkeypatch.setattr(Session, "commit", failing_commit)
    r = post(client, body(load_fixture()), k.key)
    monkeypatch.setattr(Session, "commit", real_commit)
    assert r.status_code == 503
    assert counts(db) == (0, 0, 0)


def test_t30_non_retryable_db_error_is_500_and_rolled_back(client, make_key, db, monkeypatch):
    k = make_key()

    def integrity(*_a, **_k):
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key value (secret)"))

    monkeypatch.setattr(telemetry, "_recompute_traces", integrity)
    r = post(client, body(load_fixture()), k.key)
    assert r.status_code == 500
    assert status_of(r).message == "internal error"
    assert b"secret" not in r.content
    assert counts(db) == (0, 0, 0)


def test_unexpected_exception_is_500_without_details(client, make_key, db, monkeypatch):
    k = make_key()

    def bug(*_a, **_k):
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr(telemetry, "persist_batch", bug)
    r = post(client, body(load_fixture()), k.key)
    assert r.status_code == 500
    assert b"internal detail" not in r.content
    assert counts(db) == (0, 0, 0)


def test_t30_database_unreachable_is_503_with_retry_after(caplog):
    """No monkeypatching: the auth lookup's real connect to 127.0.0.1:1 fails."""
    caplog.set_level(logging.DEBUG)
    key = "lli_AbCd1234_" + "s" * 43
    with TestClient(create_app(make_settings())) as c:
        r = post(c, body(load_fixture()), key)
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert key not in caplog.text
