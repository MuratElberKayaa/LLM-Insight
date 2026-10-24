"""`GET /api/v1/traces` and `GET /api/v1/traces/{trace_id}` against real PostgreSQL, with data
written through the real `/v1/traces` ingest. All telemetry is SYNTHETIC.

T25 (pagination), T26 (API timestamps), T27 (orphan returned), T28 (project isolation), plus
admin auth, rate limits, validation, unknown-cost/provenance output and index use.
"""

import base64
import logging
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.main import create_app
from app.repositories import traces as repo
from tests.helpers import DUMMY_TOKEN, make_settings
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.otlp import (
    FIXTURE_LLM,
    FIXTURE_ROOT,
    FIXTURE_TRACE_ID,
    FIXTURE_VALIDATE,
    MS,
    T0,
    body,
    inference_attrs,
    load_fixture,
    request,
    span,
)

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
WINDOW = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-31T00:00:00Z"}  # contains T0
UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "missing or invalid credentials"}}
NOT_FOUND = {"error": {"code": "not_found", "message": "resource not found"}}


def tid(i: int) -> str:
    return f"{i:032x}"


def api_get(client, path, headers=ADMIN_AUTH, **params):
    return client.get(path, params=params, headers=headers)


def list_traces(client, project, **params):
    return api_get(client, "/api/v1/traces", project=project, **{**WINDOW, **params})


def seed(client, key, n, *, start=T0, step=SEC, same_start=False, first=1):
    """n one-span traces, trace i starting at start + i*step (or all at `start`)."""
    spans = [
        span(
            trace_id=tid(i),
            span_id=f"{i:016x}",
            name=f"op-{i}",
            start=start if same_start else start + i * step,
        )
        for i in range(first, first + n)
    ]
    assert post(client, body(request(*spans)), key).status_code == 200


def ids(response):
    return [item["trace_id"] for item in response.json()["items"]]


# ---------------------------------------------------------------- authentication
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer wrong-token-value"},
        {"Authorization": f"Bearer {DUMMY_TOKEN} "},
        {"Authorization": f"Basic {DUMMY_TOKEN}"},
        {"Authorization": DUMMY_TOKEN},
        {"X-Admin-Token": DUMMY_TOKEN},
    ],
    ids=["none", "scheme-only", "empty", "wrong", "trailing-space", "basic", "raw", "other-header"],
)
def test_missing_or_invalid_credentials_get_one_identical_401(api_client, make_key, headers):
    k = make_key()
    r = api_get(api_client, "/api/v1/traces", headers=headers, project=k.project_id)
    assert r.status_code == 401
    assert r.json() == UNAUTHORIZED
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.headers["cache-control"] == "no-store"


def test_multiple_authorization_headers_are_rejected(api_client, make_key):
    k = make_key()
    r = api_client.get(
        "/api/v1/traces",
        params={"project": k.project_id},
        headers=[("Authorization", f"Bearer {DUMMY_TOKEN}"), ("Authorization", "Bearer x")],
    )
    assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_ingest_key_is_not_a_read_credential(api_client, make_key):
    k = make_key()
    for headers in ({"Authorization": f"Bearer {k.key}"}, {"X-Project-Key": k.key}):
        r = api_get(api_client, "/api/v1/traces", headers=headers, project=k.project_id)
        assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_bearer_scheme_is_case_insensitive(api_client, make_key):
    k = make_key()
    r = api_get(
        api_client,
        "/api/v1/traces",
        headers={"Authorization": f"bearer {DUMMY_TOKEN}"},
        project=k.project_id,
    )
    assert r.status_code == 200


def test_authentication_runs_before_parameter_validation(api_client):
    """No token: bad parameters and bad IDs are not even looked at (no parameter oracle)."""
    for path, params in (
        ("/api/v1/traces", {"project": "abc", "limit": "999", "from": "yesterday"}),
        ("/api/v1/traces/NOT-A-TRACE-ID", {}),
        ("/api/v1/traces/" + FIXTURE_TRACE_ID, {"project": "1"}),
    ):
        r = api_client.get(path, params=params)
        assert r.status_code == 401 and r.json() == UNAUTHORIZED


def test_tokens_never_appear_in_logs_or_responses(api_client, make_key, caplog):
    caplog.set_level(logging.DEBUG)
    k = make_key()
    seed(api_client, k.key, 1)
    wrong = "WRONG-ADMIN-TOKEN-" + "q" * 30
    api_get(api_client, "/api/v1/traces", headers={"Authorization": f"Bearer {wrong}"})
    ok = list_traces(api_client, k.project_id)
    assert ok.status_code == 200
    assert "api auth failed reason=mismatch" in caplog.text
    for secret in (DUMMY_TOKEN, wrong, k.key):
        assert secret not in caplog.text
        assert secret not in ok.text


def test_auth_failures_share_the_per_address_bucket_with_ingest(api_settings, make_key):
    settings = api_settings.model_copy(update={"rate_limit_authfail_per_min": 3})
    k = make_key()
    with TestClient(create_app(settings)) as c:
        assert post(c, b"", "lli_short_x").status_code == 401  # ingest failure counts too
        for _ in range(2):
            r = api_get(c, "/api/v1/traces", headers={}, project=k.project_id)
            assert r.status_code == 401
        r = list_traces(c, k.project_id)  # even the right token now waits
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"
        assert int(r.headers["retry-after"]) >= 1


def test_api_bucket_returns_429_with_retry_after(api_settings, make_key):
    settings = api_settings.model_copy(
        update={"rate_limit_api_rps": 0.01, "rate_limit_api_burst": 2}
    )
    k = make_key()
    with TestClient(create_app(settings)) as c:
        assert list_traces(c, k.project_id).status_code == 200
        assert list_traces(c, k.project_id).status_code == 200
        r = list_traces(c, k.project_id)
        assert r.status_code == 429
        assert r.json() == {
            "error": {"code": "rate_limited", "message": "too many requests; retry later"}
        }
        assert int(r.headers["retry-after"]) >= 1


def test_no_cross_origin_access_is_granted(api_client, make_key):
    """Browser JavaScript on another origin cannot read the API: no CORS headers at all."""
    k = make_key()
    r = api_client.get(
        "/api/v1/traces",
        params={"project": k.project_id, **WINDOW},
        headers={**ADMIN_AUTH, "Origin": "http://evil.example"},
    )
    assert r.status_code == 200
    assert not any(h.startswith("access-control-") for h in r.headers)
    pre = api_client.options(
        "/api/v1/traces",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert pre.status_code in (401, 405)
    assert not any(h.startswith("access-control-") for h in pre.headers)


# ---------------------------------------------------------------- list: validation
@pytest.mark.parametrize(
    "params,field",
    [
        ({}, "project"),
        ({"project": "0"}, "project"),
        ({"project": "abc"}, "project"),
        ({"project": "1", "limit": "0"}, "limit"),
        ({"project": "1", "limit": "201"}, "limit"),
        ({"project": "1", "limit": "-5"}, "limit"),
        ({"project": "1", "status": "failed"}, "status"),
        ({"project": "1", "provenance": "verified"}, "provenance"),
        ({"project": "1", "model": "m" * 257}, "model"),
        ({"project": "1", "cursor": "c" * 129}, "cursor"),
    ],
    ids=lambda v: str(v)[:40],
)
def test_invalid_list_parameters_are_422_naming_the_field_without_echo(api_client, params, field):
    r = api_get(api_client, "/api/v1/traces", **params)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_request" and field in err["fields"]
    for value in params.values():
        if len(value) > 3:
            assert value not in r.text  # FastAPI's default 422 would echo the input


@pytest.mark.parametrize(
    "window,field",
    [
        ({"from": "2026-10-24T10:00:00"}, "from"),  # naive
        ({"from": "2026-10-24T10:00:00+03:00"}, "from"),  # not UTC
        ({"from": "1792836000"}, "from"),  # epoch number, not ISO-8601
        ({"from": "2026-10-24"}, "from"),  # date only: naive
        ({"from": "yesterday"}, "from"),
        ({"to": "2026-13-01T00:00:00Z"}, "to"),
        ({"from": "2026-09-01T00:00:00Z", "to": "2026-10-03T00:00:00Z"}, "from"),  # 32 days
        ({"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"}, "from"),  # reversed
        ({"from": "2026-10-01T00:00:00Z", "to": "2026-10-01T00:00:00Z"}, "from"),  # empty
    ],
    ids=lambda v: str(v)[:48],
)
def test_invalid_time_windows_are_422(api_client, make_key, window, field):
    k = make_key()
    r = api_get(api_client, "/api/v1/traces", project=k.project_id, **window)
    assert r.status_code == 422
    assert r.json()["error"]["fields"] == [field]


@pytest.mark.parametrize(
    "cursor",
    [
        "not base64 !!",
        base64.urlsafe_b64encode(b"v1:abc:" + b"0" * 32).decode(),
        base64.urlsafe_b64encode(b"v2:1:" + b"a" * 32).decode(),
        base64.urlsafe_b64encode(b"v1:1:" + b"A" * 32).decode(),
        base64.urlsafe_b64encode(b"v1:9999999999999999999:" + b"a" * 32).decode(),
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
    ],
    ids=["junk", "non-numeric", "version", "uppercase-id", "overflow", "non-ascii"],
)
def test_invalid_cursors_are_422(api_client, make_key, cursor):
    k = make_key()
    r = list_traces(api_client, k.project_id, cursor=cursor)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["cursor"]


def test_unknown_project_is_404(api_client, make_key):
    make_key()
    r = list_traces(api_client, 999_999)
    assert r.status_code == 404 and r.json() == NOT_FOUND


# ---------------------------------------------------------------- list: content and order
def test_list_item_shape_with_unknown_cost_and_provenance(api_client, make_key):
    k = make_key()
    assert post(api_client, body(load_fixture()), k.key).status_code == 200
    r = list_traces(api_client, k.project_id)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert data["next_cursor"] is None
    assert data["window"] == {
        "from": "2026-10-01T00:00:00.000000Z",
        "to": "2026-10-31T00:00:00.000000Z",
    }
    (item,) = data["items"]
    assert item == {
        "trace_id": FIXTURE_TRACE_ID,
        "service_name": "demo-summarizer",
        "started_at": "2026-10-24T10:00:00.000000000Z",
        "ended_at": "2026-10-24T10:00:01.240000000Z",
        "start_time_unix_nano": str(T0),
        "end_time_unix_nano": str(T0 + 1240 * MS),
        "duration_ns": str(1240 * MS),
        "status": "ok",
        "prompt_version": "v1",
        "provenance": "synthetic",
        "provenance_verified": False,
        "span_count": 3,
        "usage": {
            "inference_calls": 1,
            "input_tokens": 1250,
            "output_tokens": 180,
            "calls_with_unknown_tokens": 0,
            "known_cost_usd": None,  # unknown cost is null, never 0
            "unknown_cost_calls": 1,
        },
    }


def test_trace_without_inference_has_null_tokens_and_zero_counts(api_client, make_key):
    k = make_key()
    seed(api_client, k.key, 1)
    usage = list_traces(api_client, k.project_id).json()["items"][0]["usage"]
    assert usage == {
        "inference_calls": 0,
        "input_tokens": None,
        "output_tokens": None,
        "calls_with_unknown_tokens": 0,
        "known_cost_usd": None,
        "unknown_cost_calls": 0,
    }


def test_partially_unknown_tokens_are_summed_and_flagged(api_client, make_key):
    k = make_key()
    known = inference_attrs(gen_ai__response__id="r1")
    unknown = inference_attrs(gen_ai__response__id="r2", gen_ai__usage__input_tokens=None)
    spans = [
        span(span_id="1" * 16, attrs=known),
        span(span_id="2" * 16, start=T0 + MS, attrs=unknown),
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    usage = list_traces(api_client, k.project_id).json()["items"][0]["usage"]
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 40
    assert usage["calls_with_unknown_tokens"] == 1 and usage["unknown_cost_calls"] == 2


# ---------------------------------------------------------------- T25: pagination
def test_t25_cursor_pages_are_newest_first_complete_and_disjoint(api_client, make_key):
    k = make_key()
    seed(api_client, k.key, 7)
    pages, cursor = [], None
    while True:
        params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        r = list_traces(api_client, k.project_id, **params)
        assert r.status_code == 200
        pages.append(ids(r))
        cursor = r.json()["next_cursor"]
        if cursor is None:
            break
    assert pages == [[tid(7), tid(6), tid(5)], [tid(4), tid(3), tid(2)], [tid(1)]]


def test_t25_ties_on_start_time_are_ordered_by_trace_id_across_pages(api_client, make_key):
    k = make_key()
    seed(api_client, k.key, 5, same_start=True)
    first = list_traces(api_client, k.project_id, limit=2)
    second = list_traces(api_client, k.project_id, limit=2, cursor=first.json()["next_cursor"])
    third = list_traces(api_client, k.project_id, limit=2, cursor=second.json()["next_cursor"])
    assert ids(first) + ids(second) + ids(third) == [tid(i) for i in (5, 4, 3, 2, 1)]
    assert third.json()["next_cursor"] is None


def test_t25_keyset_is_stable_when_newer_traces_arrive_between_pages(api_client, make_key):
    k = make_key()
    seed(api_client, k.key, 6)
    first = list_traces(api_client, k.project_id, limit=3)
    seed(api_client, k.key, 2, first=100)  # newer than everything already listed
    second = list_traces(api_client, k.project_id, limit=3, cursor=first.json()["next_cursor"])
    assert ids(first) == [tid(6), tid(5), tid(4)]
    assert ids(second) == [tid(3), tid(2), tid(1)]  # no duplicates, nothing skipped


def test_t25_limit_default_is_50_and_maximum_200_is_accepted(api_client, make_key):
    k = make_key()
    seed(api_client, k.key, 55, step=MS)
    default = list_traces(api_client, k.project_id)
    assert len(default.json()["items"]) == 50 and default.json()["next_cursor"] is not None
    maximum = list_traces(api_client, k.project_id, limit=200)
    assert len(maximum.json()["items"]) == 55 and maximum.json()["next_cursor"] is None


# ---------------------------------------------------------------- filters and window
def test_status_provenance_and_model_filters(api_client, make_key):
    k = make_key()
    ok_a = inference_attrs(gen_ai__response__model="gpt-a", gen_ai__response__id="r1")
    real_b = inference_attrs(
        gen_ai__request__model="gpt-b",
        gen_ai__response__model="gpt-b-2026",
        gen_ai__response__id="r2",
        llminsight__provenance="real",
    )
    spans = [
        span(trace_id=tid(1), span_id="1" * 16, attrs=ok_a),
        span(trace_id=tid(2), span_id="2" * 16, start=T0 + SEC, status_code=2),
        span(trace_id=tid(3), span_id="3" * 16, start=T0 + 2 * SEC, attrs=real_b),
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    assert ids(list_traces(api_client, k.project_id, status="error")) == [tid(2)]
    assert ids(list_traces(api_client, k.project_id, status="ok")) == [tid(3), tid(1)]
    assert ids(list_traces(api_client, k.project_id, provenance="real")) == [tid(3)]
    assert ids(list_traces(api_client, k.project_id, provenance="synthetic")) == [tid(1)]
    assert ids(list_traces(api_client, k.project_id, provenance="unknown")) == [tid(2)]
    assert ids(list_traces(api_client, k.project_id, model="gpt-a")) == [tid(1)]
    assert ids(list_traces(api_client, k.project_id, model="gpt-b")) == [tid(3)]  # requested
    assert ids(list_traces(api_client, k.project_id, model="gpt-b-2026")) == [tid(3)]  # response
    assert ids(list_traces(api_client, k.project_id, model="gpt")) == []  # exact match only


def test_window_is_from_inclusive_to_exclusive(api_client, make_key):
    k = make_key()
    seed(api_client, k.key, 1, start=T0 - SEC)  # trace 1 starts exactly at T0
    at_t0 = "2026-10-24T10:00:00Z"
    assert ids(
        api_get(
            api_client,
            "/api/v1/traces",
            project=k.project_id,
            **{"from": at_t0, "to": "2026-10-25T00:00:00Z"},
        )
    ) == [tid(1)]
    assert (
        ids(
            api_get(
                api_client,
                "/api/v1/traces",
                project=k.project_id,
                **{"from": "2026-10-23T00:00:00Z", "to": at_t0},
            )
        )
        == []
    )
    plus = api_get(
        api_client,
        "/api/v1/traces",
        project=k.project_id,
        **{"from": "2026-10-24T10:00:00+00:00", "to": "2026-10-24T10:00:00.000001+00:00"},
    )
    assert ids(plus) == [tid(1)]


def test_default_window_is_the_last_31_days(api_client, make_key):
    k = make_key()
    now = time.time_ns()
    seed(api_client, k.key, 1, start=now - 3600 * SEC, first=1)
    seed(api_client, k.key, 1, start=now - 40 * 86400 * SEC, first=2)
    r = api_get(api_client, "/api/v1/traces", project=k.project_id)
    assert r.status_code == 200 and ids(r) == [tid(1)]
    w = r.json()["window"]
    assert w["from"] < w["to"]


# ---------------------------------------------------------------- T26: timestamps
def test_t26_api_returns_iso_utc_z_with_all_nanoseconds(api_client, make_key):
    k = make_key()
    s = span(start=T0 + 123_456_789, end=T0 + 2 * SEC + 1)
    assert post(api_client, body(request(s)), k.key).status_code == 200
    item = list_traces(api_client, k.project_id).json()["items"][0]
    assert item["started_at"] == "2026-10-24T10:00:00.123456789Z"
    assert item["ended_at"] == "2026-10-24T10:00:02.000000001Z"
    assert item["start_time_unix_nano"] == str(T0 + 123_456_789)
    assert item["duration_ns"] == str(2 * SEC + 1 - 123_456_789)
    detail = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=k.project_id).json()
    assert detail["spans"][0]["started_at"] == "2026-10-24T10:00:00.123456789Z"
    assert detail["spans"][0]["end_time_unix_nano"] == str(T0 + 2 * SEC + 1)


def test_t26_largest_storable_timestamp_round_trips_exactly(api_client, make_key):
    k = make_key()
    top = 2**63 - 1
    assert post(api_client, body(request(span(start=top - 5, end=top))), k.key).status_code == 200
    r = api_get(
        api_client,
        "/api/v1/traces",
        project=k.project_id,
        **{"from": "2262-04-01T00:00:00Z", "to": "2262-04-30T00:00:00Z"},
    )
    item = r.json()["items"][0]
    assert item["ended_at"] == "2262-04-11T23:47:16.854775807Z"
    assert item["end_time_unix_nano"] == str(top)


# ---------------------------------------------------------------- detail
def test_detail_of_the_fixture(api_client, make_key):
    k = make_key()
    assert post(api_client, body(load_fixture()), k.key).status_code == 200
    r = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=k.project_id)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert set(d) == {"trace", "spans", "spans_truncated", "usage", "usage_truncated", "reviews"}
    assert d["trace"]["trace_id"] == FIXTURE_TRACE_ID and d["trace"]["span_count"] == 3
    assert (d["spans_truncated"], d["usage_truncated"], d["reviews"]) == (False, False, [])
    spans = {s["span_id"]: s for s in d["spans"]}
    assert [s["span_id"] for s in d["spans"]] == [FIXTURE_ROOT, FIXTURE_LLM, FIXTURE_VALIDATE]
    assert spans[FIXTURE_ROOT]["role"] == "root"
    assert spans[FIXTURE_ROOT]["children"] == [FIXTURE_LLM, FIXTURE_VALIDATE]
    assert spans[FIXTURE_LLM]["role"] == "child" and spans[FIXTURE_LLM]["kind"] == "CLIENT"
    assert spans[FIXTURE_LLM]["parent_span_id"] == FIXTURE_ROOT
    assert spans[FIXTURE_LLM]["provenance"] == "synthetic"
    assert all(s["provenance_verified"] is False for s in d["spans"])
    assert all("status_message" not in s for s in d["spans"])
    assert spans[FIXTURE_LLM]["attributes_schema_version"] == "llminsight.attrmap.v1"
    (u,) = d["usage"]
    assert u["span_id"] == FIXTURE_LLM
    assert u["canonical_call_key"] == "resp:openai:chatcmpl-SYNTHETIC-0001"
    assert (u["input_tokens"], u["cached_input_tokens"], u["output_tokens"]) == (1250, 1024, 180)
    assert u["cache_write_input_tokens"] is None and u["reasoning_output_tokens"] is None
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert u["price_version"] is None and u["provenance_verified"] is False
    assert u["started_at"].endswith("Z")


def test_t27_orphan_is_returned_as_orphan_until_its_parent_arrives(api_client, make_key):
    k = make_key()
    child = span(span_id=FIXTURE_LLM, parent=FIXTURE_ROOT, start=T0 + MS)
    assert post(api_client, body(request(child)), k.key).status_code == 200
    d = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=k.project_id).json()
    (s,) = d["spans"]
    assert (s["role"], s["parent_span_id"], s["children"]) == ("orphan", FIXTURE_ROOT, [])

    assert post(api_client, body(request(span(span_id=FIXTURE_ROOT))), k.key).status_code == 200
    d = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=k.project_id).json()
    roles = {s["span_id"]: (s["role"], s["children"]) for s in d["spans"]}
    assert roles == {FIXTURE_ROOT: ("root", [FIXTURE_LLM]), FIXTURE_LLM: ("child", [])}


def test_detail_is_bounded_and_roles_stay_correct_when_truncated(api_client, make_key, monkeypatch):
    monkeypatch.setattr(repo, "DETAIL_MAX_SPANS", 2)
    k = make_key()
    spans = [span(span_id=FIXTURE_ROOT)] + [
        span(span_id=f"{i:016x}", parent=FIXTURE_ROOT, start=T0 + i * MS) for i in range(1, 4)
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    d = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=k.project_id).json()
    assert d["spans_truncated"] is True and len(d["spans"]) == 2
    assert d["trace"]["span_count"] == 4  # the summary still reports the full count
    assert [s["role"] for s in d["spans"]] == ["root", "child"]


@pytest.mark.parametrize(
    "bad",
    [FIXTURE_TRACE_ID.upper(), FIXTURE_TRACE_ID[:-1], FIXTURE_TRACE_ID + "0", "g" * 32],
    ids=["uppercase", "31-chars", "33-chars", "non-hex"],
)
def test_detail_rejects_malformed_trace_ids_with_422(api_client, make_key, bad):
    k = make_key()
    r = api_get(api_client, f"/api/v1/traces/{bad}", project=k.project_id)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["trace_id"]


# ---------------------------------------------------------------- T28: project isolation
def test_t28_other_projects_trace_is_an_indistinguishable_404(api_client, make_key):
    a, b = make_key("a"), make_key("b")
    assert post(api_client, body(load_fixture()), b.key).status_code == 200
    cross = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=a.project_id)
    missing = api_get(api_client, f"/api/v1/traces/{'1' * 32}", project=a.project_id)
    no_project = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=999_999)
    assert cross.status_code == missing.status_code == no_project.status_code == 404
    assert cross.content == missing.content == no_project.content
    assert cross.json() == NOT_FOUND
    assert dict(cross.headers) == dict(missing.headers)  # not even a header differs
    own = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=b.project_id)
    assert own.status_code == 200


def test_t28_list_never_mixes_projects_even_with_identical_trace_ids(api_client, make_key):
    a, b = make_key("a"), make_key("b")
    assert post(api_client, body(load_fixture()), a.key).status_code == 200
    other = request(span(span_id=FIXTURE_ROOT, name="b-only"), resource={"service.name": "svc-b"})
    assert post(api_client, body(other), b.key).status_code == 200
    seed(api_client, b.key, 2, first=50)
    items_a = list_traces(api_client, a.project_id).json()["items"]
    items_b = list_traces(api_client, b.project_id).json()["items"]
    assert [(i["trace_id"], i["service_name"]) for i in items_a] == [
        (FIXTURE_TRACE_ID, "demo-summarizer")
    ]
    assert {i["trace_id"] for i in items_b} == {FIXTURE_TRACE_ID, tid(50), tid(51)}
    assert all(i["service_name"] != "demo-summarizer" for i in items_b)
    detail_b = api_get(api_client, f"/api/v1/traces/{FIXTURE_TRACE_ID}", project=b.project_id)
    assert [s["name"] for s in detail_b.json()["spans"]] == ["b-only"]
    assert detail_b.json()["usage"] == []


# ---------------------------------------------------------------- storage and SQL
def test_read_transactions_are_read_only(db):
    with Session(db) as s:
        repo.begin_read_only(s)
        with pytest.raises(DBAPIError) as e:
            s.execute(text("INSERT INTO projects (name) VALUES ('x')"))
        assert e.value.orig.sqlstate == "25006"  # read_only_sql_transaction


def test_database_outage_is_503_without_details():
    settings = make_settings(rate_limit_api_rps=1000, rate_limit_api_burst=1000)
    with TestClient(create_app(settings)) as c:  # 127.0.0.1:1, nothing listens
        r = api_get(c, "/api/v1/traces", project=1)
        assert r.status_code == 503
        assert r.json() == {
            "error": {"code": "storage_unavailable", "message": "storage unavailable; retry later"}
        }
        assert r.headers["retry-after"] == "5"


# Bulk SYNTHETIC rows straight into the tables (ingest would take minutes): per project,
# 5000 traces one minute apart (all inside the window), 2 spans and 2 usage rows each.
_BULK = (
    """INSERT INTO traces (project_id, trace_id, start_ns, end_ns, started_at, ended_at,
           status, span_count, provenance)
       SELECT p, lpad(to_hex(p * 1000000 + g), 32, '0'), s, s + 1000, to_timestamp(s / 1e9),
           to_timestamp((s + 1000) / 1e9), 'ok', 2, 'synthetic'
       FROM generate_series(1, 5000) g, (VALUES (:a), (:b)) AS pr(p),
           LATERAL (SELECT 1792836000000000000::bigint - g::bigint * 60000000000 AS s) x""",
    """INSERT INTO spans (project_id, trace_id, span_id, name, kind, start_ns, end_ns, status,
           attributes_schema_version, provenance)
       SELECT t.project_id, t.trace_id, lpad(to_hex(k * 100000000 + t.id), 16, '0'), 'op',
           'CLIENT', t.start_ns, t.end_ns, 'UNSET', 'llminsight.attrmap.v1', 'synthetic'
       FROM traces t, generate_series(1, 2) k""",
    """INSERT INTO usage_records (project_id, canonical_call_key, call_key_source, trace_id,
           span_id, operation_name, provider, response_model, input_tokens, output_tokens,
           usage_source, cost_status, cost_unknown_reason, provenance, started_at)
       SELECT s.project_id, 'span:' || s.trace_id || ':' || s.span_id, 'span_fallback',
           s.trace_id, s.span_id, 'chat', 'openai',
           CASE WHEN s.id % 7 = 0 THEN 'gpt-a' ELSE 'gpt-b' END, 10, 2, 'provider_reported',
           'unknown', 'price_missing', 'synthetic', to_timestamp(s.start_ns / 1e9)
       FROM spans s""",
    "ANALYZE traces",
    "ANALYZE spans",
    "ANALYZE usage_records",
)


@pytest.mark.parametrize("with_model_and_cursor", [False, True], ids=["plain", "model+cursor"])
def test_list_query_plan_reads_one_page_through_the_indexes(db, make_key, with_model_and_cursor):
    """EXPLAIN ANALYZE on 10,000 traces / 20,000 usage rows with fresh statistics: the list is
    an index range scan that stops after about one page, and the per-trace usage summary and
    the model filter are index lookups by (project_id, trace_id), not scans."""
    from datetime import UTC, datetime

    a, b = make_key("a").project_id, make_key("b").project_id
    with db.begin() as c:
        for stmt in _BULK:
            c.execute(text(stmt), {"a": a, "b": b})
    f = repo.TraceFilter(
        a,
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 31, tzinfo=UTC),
        model="gpt-a" if with_model_and_cursor else None,
    )
    cursor = repo.Cursor(datetime(2026, 10, 22, tzinfo=UTC), "a" * 32)
    stmt = repo.list_traces_stmt(f, cursor if with_model_and_cursor else None, 50)
    compiled = stmt.compile(db)
    with db.connect() as c:
        plan = "\n".join(
            r[0]
            for r in c.exec_driver_sql(
                "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF) " + compiled.string, compiled.params
            )
        )
        assert len(c.execute(stmt).all()) == 51  # a full page plus the look-ahead row
    assert "Seq Scan" not in plan
    assert "Index Scan using ix_traces_project_id_started_at on traces" in plan
    usage_lookups = plan.count(
        "Index Cond: ((project_id = traces.project_id) AND (trace_id = traces.trace_id))"
    ) + plan.count(f"Index Cond: ((project_id = '{a}'::bigint) AND (trace_id = traces.trace_id))")
    assert usage_lookups == (2 if with_model_and_cursor else 1)
    scanned = [
        int(line.split("actual rows=")[1].split()[0])
        for line in plan.splitlines()
        if "ix_traces_project_id_started_at" in line
    ]
    assert scanned and scanned[0] < 1000  # one page (plus filtered rows), not 5000
