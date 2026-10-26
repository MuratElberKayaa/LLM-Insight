"""Phase 5A: T43 (request rate limit) re-verified in full, against real PostgreSQL.

T43 (TEST_MATRIX.md): a burst over the limit gets `429` + `Retry-After` from each of the three
in-process buckets of SECURITY.md §4 (CR-08):
- ingest, keyed by the verified key (per-key isolation: `test_ingest_auth.py`);
- auth-failure, keyed by client address and shared by `/v1/traces` and `/api/v1`;
- `/api/v1`, keyed by client address after authentication.

Earlier phases tested the buckets on the routes that existed then (traces, metrics, projects,
diagnostics). This module covers every `/api/v1` route the app registers, including the
Phase 4 writes, and shows that a refused request neither reads its body nor writes a row.
An injected clock makes `Retry-After` exact and the refill observable, instead of `>= 1`.
The dashboard half of T43 is `frontend/tests/e2e/phase5a.spec.ts`.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api
from app.main import create_app
from tests.helpers import DUMMY_TOKEN
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import counts, post, rows, status_of
from tests.otlp import body, load_fixture, request, span

pytestmark = pytest.mark.integration

RATE_LIMITED = {"error": {"code": "rate_limited", "message": "too many requests; retry later"}}
WRITE_TABLES = (
    "traces",
    "spans",
    "usage_records",
    "failure_reviews",
    "test_datasets",
    "test_cases",
    "experiments",
    "experiment_results",
)
TRACE = "0" * 31 + "1"
API_DIR = Path(app.api.__file__).parent

# Every `/api/v1` route of this build (ARCHITECTURE.md §5). A new route must be added here, so
# it cannot ship without a rate-limit check.
API_ROUTES = {
    ("GET", "/api/v1/traces"),
    ("GET", "/api/v1/traces/{trace_id}"),
    ("GET", "/api/v1/traces/{trace_id}/diagnostics"),
    ("GET", "/api/v1/overview"),
    ("GET", "/api/v1/models/compare"),
    ("GET", "/api/v1/projects"),
    ("POST", "/api/v1/failure-reviews"),
    ("PATCH", "/api/v1/failure-reviews/{review_id}"),
    ("GET", "/api/v1/datasets"),
    ("POST", "/api/v1/datasets"),
    ("GET", "/api/v1/datasets/{dataset_id}/cases"),
    ("POST", "/api/v1/datasets/{dataset_id}/cases"),
    ("POST", "/api/v1/experiments"),
    ("GET", "/api/v1/experiments"),
    ("GET", "/api/v1/experiments/{experiment_id}"),
    ("GET", "/api/v1/experiment-options"),
}
PATH_VALUES = {"trace_id": TRACE, "review_id": "1", "dataset_id": "1", "experiment_id": "1"}
# Over the JSON body cap and not JSON: if a refused write read its body, it would answer
# 413 or 422 instead of 429.
UNREADABLE_BODY = b"{" * (300 * 1024)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def api_routes(app) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in operations
    }


def concrete(path: str) -> str:
    for name, value in PATH_VALUES.items():
        path = path.replace("{" + name + "}", value)
    return path


def call(client, method, path, project, *, headers=ADMIN_AUTH, content=None):
    h = dict(headers)
    if method in ("POST", "PATCH"):
        h["Content-Type"] = "application/json"
    return client.request(
        method, concrete(path), params={"project": project}, headers=h, content=content
    )


def write_counts(db) -> dict[str, int]:
    return {t: rows(db, f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in WRITE_TABLES}  # noqa: S608 - constant names


def other_trace() -> bytes:
    """A 1-span payload with its own trace id: a second distinct ingest request."""
    return body(request(span(trace_id="0" * 30 + "ab", span_id="0" * 15 + "1", name="t43")))


def test_the_route_list_is_every_api_route_and_each_router_requires_admin(api_settings):
    """`API_ROUTES` is the app's real route set, no `/api/v1` route is hidden from that listing,
    and every `/api/v1` router declares `require_admin` (which holds both `/api/v1` buckets).
    The tests below show the effect on each route."""
    assert api_routes(create_app(api_settings)) == API_ROUTES
    for module in API_DIR.glob("*.py"):
        src = module.read_text(encoding="utf-8")
        if 'prefix="/api/v1"' in src:
            assert "dependencies=[Depends(require_admin)]" in src, module.name
            assert "include_in_schema=False" not in src, module.name


def test_api_bucket_refuses_every_route_before_reading_the_body_or_writing(
    api_settings, make_key, db
):
    clock = FakeClock()
    settings = api_settings.model_copy(
        update={"rate_limit_api_rps": 0.5, "rate_limit_api_burst": 1}
    )
    k = make_key()
    with TestClient(create_app(settings, clock=clock)) as c:
        assert call(c, "GET", "/api/v1/projects", k.project_id).status_code == 200
        before = write_counts(db)
        for method, path in sorted(API_ROUTES):
            content = UNREADABLE_BODY if method in ("POST", "PATCH") else None
            r = call(c, method, path, k.project_id, content=content)
            assert r.status_code == 429, (method, path, r.status_code)
            assert r.json() == RATE_LIMITED, (method, path)
            # tokens = 0, rate 0.5/s: one token is exactly 2 s away
            assert r.headers["retry-after"] == "2", (method, path)
            assert r.headers["cache-control"] == "no-store", (method, path)
        assert write_counts(db) == before

        # The refill is real: after Retry-After a valid write succeeds once, then waits again.
        clock.now += 2
        created = c.post(
            "/api/v1/datasets",
            params={"project": k.project_id},
            headers={**ADMIN_AUTH, "Content-Type": "application/json"},
            content=b'{"name": "t43-after-refill"}',
        )
        assert created.status_code == 201, created.text
        assert call(c, "GET", "/api/v1/datasets", k.project_id).status_code == 429
        assert write_counts(db)["test_datasets"] == before["test_datasets"] + 1


def test_auth_failure_bucket_blocks_every_route_and_ingest_for_that_address(
    api_settings, make_key, db
):
    """Two `/api/v1` failures exhaust a limit of 2/min: the address then waits on every
    `/api/v1` route and on `/v1/traces`, even with the right token and a valid key, and the
    refused requests write nothing."""
    clock = FakeClock()
    settings = api_settings.model_copy(update={"rate_limit_authfail_per_min": 2})
    k = make_key()
    wrong = {"Authorization": "Bearer " + "w" * 40}
    with TestClient(create_app(settings, clock=clock)) as c:
        for _ in range(2):
            assert call(c, "GET", "/api/v1/traces", k.project_id, headers=wrong).status_code == 401
        before = write_counts(db)
        for method, path in sorted(API_ROUTES):
            content = UNREADABLE_BODY if method in ("POST", "PATCH") else None
            r = call(c, method, path, k.project_id, content=content)
            assert r.status_code == 429, (method, path, r.status_code)
            assert r.json() == RATE_LIMITED, (method, path)
            # 2 per minute refills one failure every 30 s
            assert r.headers["retry-after"] == "30", (method, path)
        r = post(c, body(load_fixture()), k.key)
        assert r.status_code == 429 and r.headers["retry-after"] == "30"
        assert status_of(r).message == "too many requests; retry later"
        assert write_counts(db) == before

        clock.now += 30
        assert call(c, "GET", "/api/v1/traces", k.project_id).status_code == 200
        assert post(c, body(load_fixture()), k.key).status_code == 200


def test_ingest_failures_block_the_api_with_an_exact_retry_after(api_settings, make_key):
    """The other direction of the shared bucket (`test_trace_api.py` checks ingest → API with
    a mixed sequence)."""
    clock = FakeClock()
    settings = api_settings.model_copy(update={"rate_limit_authfail_per_min": 3})
    k = make_key()
    with TestClient(create_app(settings, clock=clock)) as c:
        for _ in range(3):
            assert post(c, b"", "lli_short_x").status_code == 401
        r = call(c, "GET", "/api/v1/overview", k.project_id)
        assert r.status_code == 429 and r.headers["retry-after"] == "20"
        assert r.json() == RATE_LIMITED


def test_ingest_bucket_burst_then_exact_retry_after_then_refill(api_settings, make_key, db):
    clock = FakeClock()
    settings = api_settings.model_copy(
        update={"rate_limit_ingest_rps": 0.25, "rate_limit_ingest_burst": 1}
    )
    k = make_key()
    with TestClient(create_app(settings, clock=clock)) as c:
        assert post(c, body(load_fixture()), k.key).status_code == 200
        stored = counts(db, k.project_id)
        r = post(c, other_trace(), k.key)
        assert r.status_code == 429
        assert r.headers["retry-after"] == "4"  # tokens = 0, rate 0.25/s
        assert status_of(r).message == "too many requests; retry later"
        assert counts(db, k.project_id) == stored  # the refused batch wrote nothing

        clock.now += 4
        assert post(c, other_trace(), k.key).status_code == 200
        assert counts(db, k.project_id)[0] == stored[0] + 1


def test_rate_limited_responses_echo_no_credential(api_settings, make_key, caplog):
    settings = api_settings.model_copy(
        update={
            "rate_limit_api_rps": 0.001,
            "rate_limit_api_burst": 1,
            "rate_limit_ingest_rps": 0.001,
            "rate_limit_ingest_burst": 1,
        }
    )
    k = make_key()
    with TestClient(create_app(settings)) as c:
        for _ in range(2):
            api = call(c, "GET", "/api/v1/projects", k.project_id)
            ingest = post(c, body(load_fixture()), k.key)
        assert api.status_code == 429 and ingest.status_code == 429
        for secret in (DUMMY_TOKEN, k.key):
            assert secret not in api.text
            assert secret.encode() not in ingest.content
            assert secret not in caplog.text
            assert secret not in str(dict(api.headers)) + str(dict(ingest.headers))
