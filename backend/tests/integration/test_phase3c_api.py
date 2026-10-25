"""Phase 3C read-API additions against real PostgreSQL, with telemetry written through the
real `/v1/traces` ingest. All telemetry is SYNTHETIC.

- `GET /api/v1/projects`: admin auth before validation, bounded listing, no key material,
  names returned as stored (the dashboard renders them as text), read-only.
- `usage.models` on trace list items and the detail's `trace` (R40): every distinct
  provider/model combination of a trace with its call count, never one value picked for the
  whole trace; bounded per trace with a distinct count; project-isolated; index-backed.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.repositories import traces as repo
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.integration.test_trace_api import _BULK
from tests.otlp import MS, T0, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

WINDOW = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-31T00:00:00Z"}  # contains T0
UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "missing or invalid credentials"}}
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def get(client, path, headers=ADMIN_AUTH, **params):
    return client.get(path, params=params, headers=headers)


def tid(i: int) -> str:
    return f"{i:032x}"


def sid(i: int) -> str:
    return f"{i:016x}"


def call_span(trace: int, n: int, *, provider="openai", req="gpt-a", resp="gpt-a-2026", **kw):
    """One SYNTHETIC inference attempt `n` of trace `trace` (unique response id per span)."""
    overrides = {
        "gen_ai__provider__name": provider,
        "gen_ai__request__model": req,
        "gen_ai__response__model": resp,
        "gen_ai__response__id": f"resp-{trace}-{n}",
        "llminsight__call_id": f"5f0c6e1a-3b7d-4c2e-9a41-{trace:06d}{n:06d}",
        **kw,  # a None value removes the attribute
    }
    attrs = inference_attrs(**overrides)
    return span(trace_id=tid(trace), span_id=sid(trace * 1000 + n), start=T0 + n * MS, attrs=attrs)


def list_items(client, project, **params):
    r = get(client, "/api/v1/traces", project=project, **{**WINDOW, **params})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- GET /api/v1/projects
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": "Basic abc"}],
    ids=["missing", "wrong", "scheme"],
)
def test_projects_needs_the_admin_token_before_any_validation(api_client, headers):
    r = api_client.get("/api/v1/projects", params={"limit": "garbage"}, headers=headers)
    assert r.status_code == 401
    assert r.json() == UNAUTHORIZED
    assert r.headers["www-authenticate"] == "Bearer"


def test_projects_lists_id_name_and_creation_time_only(api_client, make_key):
    hostile = '<script>window.__llmiXss=1</script> "proj" & <img src=x>'
    a, b, c = make_key("zeta"), make_key("alpha"), make_key(hostile)
    r = get(api_client, "/api/v1/projects")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["limit"] == 100 and data["truncated"] is False
    assert [(p["id"], p["name"]) for p in data["items"]] == [
        (a.project_id, "zeta"),
        (b.project_id, "alpha"),  # id order, not name order
        (c.project_id, hostile),  # as stored: escaping is the renderer's job (T41)
    ]
    for p in data["items"]:
        assert set(p) == {"id", "name", "created_at"}
        assert ISO_Z.match(p["created_at"])
    for k in (a, b, c):  # no key material of any project
        assert k.key not in r.text and k.prefix not in r.text


def test_projects_listing_is_bounded_and_flags_truncation(api_client, make_key):
    ids = [make_key(f"p{i}").project_id for i in range(3)]
    r = get(api_client, "/api/v1/projects", limit=2).json()
    assert [p["id"] for p in r["items"]] == ids[:2]
    assert r["truncated"] is True and r["limit"] == 2
    full = get(api_client, "/api/v1/projects", limit=200).json()
    assert [p["id"] for p in full["items"]] == ids and full["truncated"] is False


@pytest.mark.parametrize("limit", ["0", "201", "x", "1.5", "-1"])
def test_projects_limit_is_validated_without_echo(api_client, make_key, limit):
    make_key("p")
    r = get(api_client, "/api/v1/projects", limit=limit)
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "invalid_request",
        "message": "request parameters are invalid",
        "fields": ["limit"],
    }


def test_projects_query_itself_is_bounded():
    """The bound is in SQL (limit + 1 rows for the truncation flag), not only in the response."""
    from sqlalchemy.dialects import postgresql

    from app.repositories import projects as projects_repo

    sql = str(
        projects_repo.list_projects_stmt(5).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert sql.rstrip().endswith("LIMIT 6")
    assert "ORDER BY projects.id" in sql
    assert "project_api_keys" not in sql and "key_hash" not in sql


def test_projects_on_an_empty_database(api_client):
    assert get(api_client, "/api/v1/projects").json() == {
        "items": [],
        "truncated": False,
        "limit": 100,
    }


def test_projects_is_read_only(api_client):
    for method in ("post", "put", "patch", "delete"):
        r = getattr(api_client, method)("/api/v1/projects", headers=ADMIN_AUTH)
        assert r.status_code == 405, method
        assert r.json()["error"]["code"] == "method_not_allowed"


def test_projects_uses_the_api_rate_limit(ingest_settings, make_key):
    make_key("p")
    tight = ingest_settings.model_copy(
        update={"rate_limit_api_rps": 0.001, "rate_limit_api_burst": 2}
    )
    with TestClient(create_app(tight)) as c:
        codes = [get(c, "/api/v1/projects").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------- usage.models (R40)
def test_a_trace_with_several_providers_and_models_lists_every_combination(api_client, make_key):
    k = make_key()
    spans = [
        call_span(1, 1),
        call_span(1, 2),  # same combination: 2 calls
        call_span(1, 3, provider="synthetic.other", req="m-x", resp="m-x"),
        # a failed attempt without a response model: its own combination, model unknown
        call_span(1, 4, resp=None, gen_ai__response__id=None),
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    (item,) = list_items(api_client, k.project_id)["items"]
    assert item["usage"]["inference_calls"] == 4
    assert item["usage"]["models"] == {
        "items": [
            {
                "provider": "openai",
                "requested_model": "gpt-a",
                "response_model": "gpt-a-2026",
                "calls": 2,
            },
            {"provider": "openai", "requested_model": "gpt-a", "response_model": None, "calls": 1},
            {
                "provider": "synthetic.other",
                "requested_model": "m-x",
                "response_model": "m-x",
                "calls": 1,
            },
        ],
        "distinct": 3,
        "truncated": False,
    }
    detail = get(api_client, f"/api/v1/traces/{tid(1)}", project=k.project_id).json()
    assert detail["trace"]["usage"]["models"] == item["usage"]["models"]


def test_combinations_beyond_the_bound_are_counted_not_listed(api_client, make_key):
    k = make_key()
    n = repo.MAX_MODELS_PER_TRACE + 2
    spans = [call_span(2, i, req=f"model-{i:02d}", resp=f"model-{i:02d}") for i in range(1, n + 1)]
    spans.append(call_span(2, 99, req="model-01", resp="model-01"))  # model-01 has 2 calls
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    models = list_items(api_client, k.project_id)["items"][0]["usage"]["models"]
    assert models["distinct"] == n and models["truncated"] is True
    assert len(models["items"]) == repo.MAX_MODELS_PER_TRACE
    assert models["items"][0] == {
        "provider": "openai",
        "requested_model": "model-01",
        "response_model": "model-01",
        "calls": 2,
    }
    assert [m["requested_model"] for m in models["items"][1:]] == [
        f"model-{i:02d}" for i in range(2, repo.MAX_MODELS_PER_TRACE + 1)
    ]


def test_models_never_cross_projects_even_with_identical_trace_ids(api_client, make_key):
    a, b = make_key("a"), make_key("b")
    assert (
        post(
            api_client, body(request(call_span(3, 1, req="only-a", resp="only-a"))), a.key
        ).status_code
        == 200
    )
    assert (
        post(
            api_client, body(request(call_span(3, 1, req="only-b", resp="only-b"))), b.key
        ).status_code
        == 200
    )
    for k, own, other in ((a, "only-a", "only-b"), (b, "only-b", "only-a")):
        listed = list_items(api_client, k.project_id)
        detail = get(api_client, f"/api/v1/traces/{tid(3)}", project=k.project_id)
        for text_ in (str(listed), detail.text):
            assert own in text_ and other not in text_


def test_models_follow_their_trace_across_cursor_pages(api_client, make_key):
    k = make_key()
    for i in (10, 11, 12):
        s = call_span(i, 1, req=f"model-of-{i}", resp=f"model-of-{i}")
        s.start_time_unix_nano = T0 + i * 1_000_000_000
        s.end_time_unix_nano = s.start_time_unix_nano + 5 * MS
        assert post(api_client, body(request(s)), k.key).status_code == 200
    first = list_items(api_client, k.project_id, limit=2)
    second = list_items(api_client, k.project_id, limit=2, cursor=first["next_cursor"])
    items = first["items"] + second["items"]
    assert [t["trace_id"] for t in items] == [tid(12), tid(11), tid(10)]
    for t in items:
        (m,) = t["usage"]["models"]["items"]
        assert m["requested_model"] == f"model-of-{int(t['trace_id'], 16)}"


def test_trace_models_query_uses_the_usage_index(db, make_key):
    """EXPLAIN ANALYZE over 10,000 traces / 20,000 usage rows: one page of trace ids is
    resolved through the usage unique key, with no sequential scan."""
    a, b = make_key("a").project_id, make_key("b").project_id
    with db.begin() as c:
        for stmt in _BULK:
            c.execute(text(stmt), {"a": a, "b": b})
        ids = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT trace_id FROM traces WHERE project_id = :p"
                    " ORDER BY started_at DESC LIMIT 200"
                ),
                {"p": a},
            )
        ]
    stmt = repo.trace_models_stmt(a, ids)
    compiled = stmt.compile(db, compile_kwargs={"render_postcompile": True})
    with db.connect() as c:
        plan = "\n".join(
            r[0]
            for r in c.exec_driver_sql(
                "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) " + compiled.string, compiled.params
            )
        )
        rows = c.execute(stmt).all()
        expected = dict(
            c.execute(
                text(
                    "SELECT trace_id, count(DISTINCT response_model) FROM usage_records"
                    " WHERE project_id = :p AND trace_id = ANY(:ids) GROUP BY trace_id"
                ),
                {"p": a, "ids": ids},
            ).all()
        )
    assert "Seq Scan" not in plan
    assert "uq_usage_records_project_id_trace_id_span_id" in plan
    assert {r.trace_id: r.distinct_count for r in rows} == expected
    assert set(expected.values()) == {1, 2}  # the bulk data mixes one- and two-model traces


def test_trace_models_with_no_ids_runs_no_query(db):
    from sqlalchemy.orm import Session

    with Session(db) as s:
        assert repo.trace_models(s, 1, []) == []


def test_trace_list_window_is_unchanged_by_the_models_summary(api_client, make_key):
    """The summary is computed for the returned page only; the echoed window and cursor
    semantics are those of Phase 1C."""
    k = make_key()
    assert post(api_client, body(request(call_span(4, 1))), k.key).status_code == 200
    data = list_items(api_client, k.project_id)
    assert data["window"] == {
        "from": "2026-10-01T00:00:00.000000Z",
        "to": "2026-10-31T00:00:00.000000Z",
    }
    assert data["next_cursor"] is None
