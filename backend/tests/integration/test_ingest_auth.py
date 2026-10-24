"""Ingest authentication, throttling and log/body sanitization (real PostgreSQL).

T05, T44, and the ingest/auth-failure halves of T43 (the T43 gate is Phase 3).
"""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.services.ingestion import body as body_mod
from tests.integration.ingest_helpers import counts, post, status_of
from tests.otlp import body, load_fixture

pytestmark = pytest.mark.integration

GENERIC_401 = "missing or invalid project key"


@pytest.fixture
def capture_all_logs(caplog):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    return caplog


def test_valid_key_is_accepted(client, make_key, db):
    k = make_key()
    assert post(client, body(load_fixture()), k.key).status_code == 200
    assert counts(db, k.project_id) == (1, 3, 1)


def _variants(k, revoked_key):
    forged = k.key[:-4] + ("AAAA" if not k.key.endswith("AAAA") else "BBBB")
    unknown_prefix = "lli_ZZZZ9999_" + k.key.split("_", 2)[2]
    return {
        "missing": None,
        "empty": "",
        "malformed": "not-a-key",
        "bearer_style": "Bearer " + k.key,
        "forged_secret": forged,
        "unknown_prefix": unknown_prefix,
        "revoked": revoked_key,
    }


def test_t05_t44_missing_unknown_revoked_keys_get_identical_401_and_nothing_logged(
    client, make_key, db, capture_all_logs
):
    k = make_key()
    revoked = make_key()
    with db.begin() as c:
        c.execute(
            text("UPDATE project_api_keys SET revoked_at = now() WHERE prefix = :p"),
            {"p": revoked.prefix},
        )
    bodies = set()
    for name, value in _variants(k, revoked.key).items():
        r = post(client, body(load_fixture()), value)
        assert r.status_code == 401, name
        assert status_of(r).message == GENERIC_401, name
        bodies.add(r.content)
        if value:
            secret = value.split("_", 2)[-1]
            assert value.encode() not in r.content and secret.encode() not in r.content
    assert len(bodies) == 1  # byte-identical responses: no oracle between failure kinds
    assert counts(db) == (0, 0, 0)

    logged = capture_all_logs.text
    for value in (k.key, revoked.key, *_variants(k, revoked.key).values()):
        if value and len(value) > 12:
            assert value not in logged
            assert value.split("_", 2)[-1] not in logged  # the secret part
    assert "reason=revoked" in logged and "reason=hash_mismatch" in logged
    assert "reason=unknown_prefix" in logged and "reason=malformed" in logged
    assert f"prefix={revoked.prefix}" in logged  # prefix only, as SECURITY.md §2 allows


def test_t44_success_path_logs_no_key_and_no_body(client, make_key, capture_all_logs):
    k = make_key()
    post(client, body(load_fixture()), k.key)
    assert k.key not in capture_all_logs.text
    assert k.key.split("_", 2)[2] not in capture_all_logs.text
    assert "chatcmpl-SYNTHETIC-0001" not in capture_all_logs.text  # no body content


def test_duplicate_key_headers_are_rejected(client, make_key):
    k = make_key()
    r = client.post(
        "/v1/traces",
        content=body(load_fixture()),
        headers=[
            ("Content-Type", "application/x-protobuf"),
            ("X-Project-Key", k.key),
            ("X-Project-Key", k.key),
        ],
    )
    assert r.status_code == 401


def test_unauthenticated_body_is_never_read(client, monkeypatch):
    def must_not_read(*_a, **_k):
        raise AssertionError("body read before authentication")

    monkeypatch.setattr(body_mod, "read_bounded", must_not_read)
    r = post(client, b"\x00" * (8 * 1024 * 1024), "lli_short_x")  # over the wire limit, too
    assert r.status_code == 401


def test_repeated_auth_failures_are_throttled_per_client_address(ingest_settings, make_key):
    settings = ingest_settings.model_copy(update={"rate_limit_authfail_per_min": 5})
    k = make_key()
    with TestClient(create_app(settings)) as c:
        for _ in range(5):
            assert post(c, b"", "lli_short_x").status_code == 401
        r = post(c, b"", "lli_short_x")
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1
        assert status_of(r).message == "too many requests; retry later"
        # checked before verification: even a valid key from this address waits
        assert post(c, body(load_fixture()), k.key).status_code == 429


def test_per_key_ingest_bucket_returns_429_with_retry_after(ingest_settings, make_key):
    settings = ingest_settings.model_copy(
        update={"rate_limit_ingest_rps": 0.01, "rate_limit_ingest_burst": 2}
    )
    a, b = make_key("a"), make_key("b")
    with TestClient(create_app(settings)) as c:
        assert post(c, body(load_fixture()), a.key).status_code == 200
        assert post(c, body(load_fixture()), a.key).status_code == 200
        r = post(c, body(load_fixture()), a.key)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        # keyed by the verified key: another key is unaffected, and failures cannot drain it
        assert post(c, body(load_fixture()), b.key).status_code == 200
        for _ in range(3):
            post(c, b"", "lli_short_x")
        assert post(c, body(load_fixture()), b.key).status_code == 200


def test_key_header_is_not_reflected_in_any_error_response(client, make_key):
    k = make_key()
    for payload, headers in (
        (b"\xff\xff", {}),
        (b"junk", {"Content_Encoding": "gzip"}),
        (b"\x00" * (4 * 1024 * 1024 + 1), {}),
    ):
        r = post(client, payload, k.key, **headers)
        assert r.status_code in (400, 413)
        assert k.key.encode() not in r.content and k.prefix.encode() not in r.content
