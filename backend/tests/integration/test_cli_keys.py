"""Key administration CLI against real PostgreSQL: HMAC-only storage, print once, revoke (T05)."""

import io

import pytest

from app import cli
from tests.integration.ingest_helpers import post, rows
from tests.otlp import body, load_fixture

pytestmark = pytest.mark.integration


def run(settings, *argv):
    out = io.StringIO()
    code = cli.run(list(argv), settings, out=out)
    return code, out.getvalue()


def test_create_list_revoke_cycle(ingest_settings, client, db):
    code, out = run(ingest_settings, "create-key", "--project", "demo")
    assert code == 0
    key = out.strip().splitlines()[-1]
    assert key.startswith("lli_")
    (stored,) = rows(db, "SELECT prefix, key_hash FROM project_api_keys")
    assert key.startswith(f"lli_{stored['prefix']}_")
    assert len(stored["key_hash"]) == 64 and stored["key_hash"] not in key
    assert key not in str(rows(db, "SELECT row_to_json(k)::text FROM project_api_keys k"))

    assert post(client, body(load_fixture()), key).status_code == 200

    code, out = run(ingest_settings, "list-keys", "--project", "demo")
    assert code == 0 and stored["prefix"] in out and "active" in out and key not in out

    assert run(ingest_settings, "revoke-key", "--prefix", stored["prefix"])[0] == 0
    assert run(ingest_settings, "revoke-key", "--prefix", stored["prefix"])[0] == 1  # already
    assert post(client, body(load_fixture()), key).status_code == 401
    assert "revoked" in run(ingest_settings, "list-keys", "--project", "demo")[1]


def test_second_key_reuses_the_project(ingest_settings, db):
    run(ingest_settings, "create-key", "--project", "demo")
    run(ingest_settings, "create-key", "--project", "demo")
    assert rows(db, "SELECT count(*) AS n FROM projects") == [{"n": 1}]
    assert rows(db, "SELECT count(*) AS n FROM project_api_keys") == [{"n": 2}]


def test_invalid_input(ingest_settings):
    assert run(ingest_settings, "create-key", "--project", "x" * 101)[0] == 2
    assert run(ingest_settings, "list-keys", "--project", "missing")[0] == 1
