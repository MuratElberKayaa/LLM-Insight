"""`GET /api/v1/traces/{trace_id}/diagnostics` (Phase 4A) against real PostgreSQL, with every
trace written through the real `/v1/traces` ingest. All telemetry is SYNTHETIC.

Each diagnostic is checked against the stored rows it claims to be based on (the trace
detail API and direct SQL), not only against the expected category.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.main import create_app
from app.repositories import traces as repo
from tests.helpers import DUMMY_TOKEN, make_settings
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import counts, post, rows
from tests.otlp import (
    FIXTURE_TRACE_ID,
    MS,
    T0,
    body,
    inference_attrs,
    load_fixture,
    request,
    span,
)

pytestmark = pytest.mark.integration

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
ROOT = "00f067aa0ba90200"
SERVER, CLIENT = 2, 3  # OTLP SpanKind
UNSET, ERROR = 0, 2  # OTLP StatusCode
CALL = "5f0c6e1a-3b7d-4c2e-9a41-00000000a001"
UNAUTHORIZED = {"error": {"code": "unauthorized", "message": "missing or invalid credentials"}}
NOT_FOUND = {"error": {"code": "not_found", "message": "resource not found"}}


def path(trace_id: str = TRACE) -> str:
    return f"/api/v1/traces/{trace_id}/diagnostics"


def diagnostics(client, project: int, trace_id: str = TRACE) -> dict:
    r = client.get(path(trace_id), params={"project": project}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def detail(client, project: int, trace_id: str = TRACE) -> dict:
    r = client.get(f"/api/v1/traces/{trace_id}", params={"project": project}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def sid(i: int) -> str:
    return f"{i:016x}"


def root(status: int = UNSET, **kw):
    return span(
        trace_id=TRACE,
        span_id=ROOT,
        name="POST /v1/summarize",
        kind=SERVER,
        status_code=status,
        **kw,
    )


def attempt(
    i: int,
    n: int | None,
    *,
    failed: str | None = None,
    call_id: str | None = CALL,
    parent: str | None = ROOT,
    **overrides,
):
    """One SYNTHETIC inference attempt; `failed` is the recorded error.type of a failure."""
    values = {
        "gen_ai__response__id": None if failed else f"chatcmpl-SYNTHETIC-{i}",
        "llminsight__call_id": call_id,
        "llminsight__attempt": n,
    }
    if failed:
        values |= {
            "gen_ai__usage__input_tokens": None,
            "gen_ai__usage__output_tokens": None,
            "llminsight__usage_source": None,
            "error__type": failed,
        }
    attrs = inference_attrs(**(values | overrides))
    return span(
        trace_id=TRACE,
        span_id=sid(i),
        parent=parent,
        name="chat gpt-4o-mini",
        kind=CLIENT,
        start=T0 + i * MS,
        attrs=attrs,
        status_code=ERROR if failed else UNSET,
    )


def ingest(client, key, *spans) -> None:
    r = post(client, body(request(*spans)), key)
    assert r.status_code == 200, r.content


def by_category(result: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in result["findings"]:
        out.setdefault(f["category"], []).append(f)
    return out


def facts(finding: dict) -> dict:
    return {f["name"]: f["value"] for f in finding["observed_facts"]}


# ---------------------------------------------------------------- access control
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": "Basic abc"}],
    ids=["missing", "wrong", "scheme"],
)
def test_authentication_runs_before_any_validation_or_lookup(api_client, make_key, headers):
    k = make_key()
    ingest(api_client, k.key, root())
    for trace_id, params in (
        ("NOT-A-TRACE-ID", {"project": "abc"}),
        (TRACE, {"project": str(k.project_id)}),  # an existing trace: still nothing learned
    ):
        r = api_client.get(path(trace_id), params=params, headers=headers)
        assert r.status_code == 401 and r.json() == UNAUTHORIZED
        assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "bad",
    [TRACE.upper(), TRACE[:-1], TRACE + "0", "g" * 32, "0" * 31 + "%2F"],
    ids=["uppercase", "31-chars", "33-chars", "non-hex", "slash"],
)
def test_malformed_trace_ids_are_422_without_echo(api_client, make_key, bad):
    k = make_key()
    r = api_client.get(path(bad), params={"project": k.project_id}, headers=ADMIN_AUTH)
    assert r.status_code in (404, 422)
    if r.status_code == 422:
        assert r.json()["error"]["fields"] == ["trace_id"]
    assert bad not in r.text


@pytest.mark.parametrize("project", ["0", "-1", "abc", str(2**63)])
def test_invalid_project_is_422(api_client, project):
    r = api_client.get(path(), params={"project": project}, headers=ADMIN_AUTH)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["project"]


def test_cross_project_unknown_trace_and_unknown_project_are_one_identical_404(
    api_client, make_key
):
    a, b = make_key("a"), make_key("b")
    ingest(api_client, b.key, root(), attempt(1, 1, failed="500"))
    cross = api_client.get(path(), params={"project": a.project_id}, headers=ADMIN_AUTH)
    missing = api_client.get(path("1" * 32), params={"project": a.project_id}, headers=ADMIN_AUTH)
    no_project = api_client.get(path(), params={"project": 999_999}, headers=ADMIN_AUTH)
    detail_404 = api_client.get(
        f"/api/v1/traces/{TRACE}", params={"project": a.project_id}, headers=ADMIN_AUTH
    )
    assert cross.status_code == missing.status_code == no_project.status_code == 404
    assert cross.content == missing.content == no_project.content == detail_404.content
    assert cross.json() == NOT_FOUND
    assert dict(cross.headers) == dict(missing.headers)
    assert diagnostics(api_client, b.project_id)["findings"]  # the owner sees its trace


def test_results_are_project_scoped_even_with_identical_trace_ids(api_client, make_key):
    a, b = make_key("a"), make_key("b")
    ingest(api_client, a.key, root(), attempt(1, 1, failed="500"), attempt(2, 2, failed="500"))
    ingest(api_client, b.key, root(), attempt(1, 1))
    da, db_ = diagnostics(api_client, a.project_id), diagnostics(api_client, b.project_id)
    assert set(by_category(da)) >= {"inference_call_failed_after_retries"}
    assert "inference_call_failed_after_retries" not in by_category(db_)
    assert db_["summary"]["failed_inference_attempts"] == 0
    assert da["summary"]["inference_attempts"] == 2 and db_["summary"]["inference_attempts"] == 1


def test_response_headers_and_other_methods(api_client, make_key):
    k = make_key()
    ingest(api_client, k.key, root())
    r = api_client.get(path(), params={"project": k.project_id}, headers=ADMIN_AUTH)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("application/json")
    assert "access-control-allow-origin" not in r.headers
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        m = api_client.request(method, path(), params={"project": k.project_id}, headers=ADMIN_AUTH)
        assert m.status_code == 405 and m.json()["error"]["code"] == "method_not_allowed"


def test_api_bucket_applies(api_settings, make_key):
    settings = api_settings.model_copy(update={"rate_limit_api_rps": 1, "rate_limit_api_burst": 2})
    k = make_key()
    with TestClient(create_app(settings)) as c:
        ingest(c, k.key, root())
        codes = [
            c.get(path(), params={"project": k.project_id}, headers=ADMIN_AUTH).status_code
            for _ in range(4)
        ]
    assert codes[:2] == [200, 200] and 429 in codes[2:]


def test_database_outage_is_503_without_details():
    settings = make_settings(rate_limit_api_rps=1000, rate_limit_api_burst=1000)
    with TestClient(create_app(settings)) as c:  # 127.0.0.1:1, nothing listens
        r = c.get(path(), params={"project": 1}, headers=ADMIN_AUTH)
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert r.json()["error"]["code"] == "storage_unavailable"


# ---------------------------------------------------------------- rules against stored rows
def test_recovered_retry_matches_the_stored_spans_and_stays_distinct_from_trace_error(
    api_client, make_key
):
    k = make_key()
    ingest(api_client, k.key, root(), attempt(1, 1, failed="429"), attempt(2, 2))
    d, det = diagnostics(api_client, k.project_id), detail(api_client, k.project_id)
    assert det["trace"]["status"] == "error"  # stored trace status (R27)
    (f,) = by_category(d)["inference_call_recovered_after_retry"]
    stored_attempts = [s for s in det["spans"] if s["attributes"].get("llminsight.call_id") == CALL]
    assert [e["span_id"] for e in f["evidence"]] == [s["span_id"] for s in stored_attempts]
    assert [e["status"] for e in f["evidence"]] == [s["status"] for s in stored_attempts]
    assert [e["error_type"] for e in f["evidence"]] == ["429", None]
    assert facts(f)["other_error_spans"] == 0 and facts(f)["trace_status"] == "error"
    assert f["hypothesis"]["error_interpretation"]["code"] == "rate_limit_status_recorded"
    assert "inference_call_failed" not in by_category(d)
    assert "non_inference_span_error" not in by_category(d)


def test_failed_after_retries_with_failing_root(api_client, make_key):
    k = make_key()
    ingest(
        api_client,
        k.key,
        root(status=ERROR, attrs={"http.response.status_code": 502}),
        *(attempt(i, i, failed="500") for i in (1, 2, 3)),
    )
    d = diagnostics(api_client, k.project_id)
    cat = by_category(d)
    (failed,) = cat["inference_call_failed_after_retries"]
    assert facts(failed)["attempts_observed"] == 3 and facts(failed)["error_types"] == ["500"]
    (root_error,) = cat["non_inference_span_error"]
    assert root_error["subject"] == {"type": "span", "span_id": ROOT}
    assert facts(root_error)["http_response_status_code"] == 502
    assert root_error["hypothesis"]["code"] == "related_to_failed_inference_below"
    assert d["summary"]["failed_inference_attempts"] == 3
    assert d["summary"]["cost_unknown"]["usage_missing"] == 3


def test_missing_usage_is_unknown_not_zero_in_storage_api_and_diagnostics(api_client, make_key, db):
    k = make_key()
    no_output = attempt(1, 1, gen_ai__usage__output_tokens=None)
    ingest(api_client, k.key, root(), no_output)
    (stored,) = rows(
        db,
        "SELECT input_tokens, output_tokens, usage_source, cost_usd, cost_status,"
        " cost_unknown_reason FROM usage_records",
    )
    assert stored == {
        "input_tokens": 100,
        "output_tokens": None,
        "usage_source": "unknown",
        "cost_usd": None,
        "cost_status": "unknown",
        "cost_unknown_reason": "usage_missing",
    }
    assert detail(api_client, k.project_id)["trace"]["usage"]["known_cost_usd"] is None
    d = diagnostics(api_client, k.project_id)
    (f,) = by_category(d)["token_usage_missing"]
    assert f["evidence"][0]["usage"]["output_tokens"] is None
    assert facts(f)["output_tokens_unknown"] == 1
    assert "cost_usd" not in json.dumps(d)


def test_unpriced_successful_call_is_unknown_cost(api_client, make_key):
    k = make_key()  # no model_prices rows are seeded in this database
    ingest(api_client, k.key, root(), attempt(1, 1))
    d = diagnostics(api_client, k.project_id)
    (f,) = by_category(d)["cost_unknown_price_missing"]
    assert facts(f)["models"] == [
        {
            "provider": "openai",
            "requested_model": "gpt-4o-mini",
            "response_model": "gpt-4o-mini-2024-07-18",
        }
    ]
    assert d["summary"]["cost_unknown"] == {
        "price_missing": 1,
        "usage_missing": 0,
        "usage_inconsistent": 0,
    }
    assert d["summary"]["cost_computed"] == 0


def test_inconsistent_usage_from_ingest(api_client, make_key):
    k = make_key()
    ingest(
        api_client, k.key, root(), attempt(1, 1, gen_ai__usage__cache_read__input_tokens=500)
    )  # > 100 input
    (f,) = by_category(diagnostics(api_client, k.project_id))["usage_inconsistent"]
    assert facts(f)["input_subsets_exceed_input"] == 1
    assert f["evidence"][0]["usage"]["cached_input_tokens"] == 500


def test_double_instrumentation_leaves_one_ledger_row_and_is_explained(api_client, make_key, db):
    k = make_key()
    same_response = "chatcmpl-SYNTHETIC-shared"
    ingest(
        api_client,
        k.key,
        root(),
        attempt(1, 1, gen_ai__response__id=same_response),
        attempt(
            2, 1, call_id="5f0c6e1a-3b7d-4c2e-9a41-00000000a002", gen_ai__response__id=same_response
        ),
    )
    assert counts(db, k.project_id) == (1, 3, 1)
    (f,) = by_category(diagnostics(api_client, k.project_id))["usage_ledger_row_missing"]
    assert facts(f) == {"inference_spans_without_usage_row": 1, "duplicate_identity_in_trace": 1}
    (missing,) = rows(
        db,
        "SELECT s.span_id FROM spans s LEFT JOIN usage_records u USING"
        " (project_id, trace_id, span_id) WHERE u.id IS NULL"
        " AND s.attributes ? 'gen_ai.operation.name'",
    )
    assert [e["span_id"] for e in f["evidence"]] == [missing["span_id"]]


def test_partial_traces_orphans_and_multiple_roots(api_client, make_key):
    k = make_key()
    orphan = attempt(1, 1, parent="00000000000000ff")
    ingest(
        api_client,
        k.key,
        root(),
        orphan,
        span(trace_id=TRACE, span_id=sid(9), name="worker", start=T0 + 9 * MS),
    )
    d = diagnostics(api_client, k.project_id)
    det = detail(api_client, k.project_id)
    stored_orphans = [s["span_id"] for s in det["spans"] if s["role"] == "orphan"]
    stored_roots = [s["span_id"] for s in det["spans"] if s["role"] == "root"]
    (o,) = by_category(d)["orphan_spans"]
    assert [e["span_id"] for e in o["evidence"]] == stored_orphans == [sid(1)]
    (m,) = by_category(d)["multiple_root_spans"]
    assert [e["span_id"] for e in m["evidence"]] == stored_roots
    assert "no_root_span" not in by_category(d)


def test_orphan_finding_disappears_when_the_parent_arrives(api_client, make_key):
    k = make_key()
    child = attempt(1, 1)
    ingest(api_client, k.key, child)
    first = diagnostics(api_client, k.project_id)
    assert {"orphan_spans", "no_root_span"} <= set(by_category(first))
    ingest(api_client, k.key, root())
    second = diagnostics(api_client, k.project_id)
    assert not {"orphan_spans", "no_root_span"} & set(by_category(second))


def test_malformed_client_metadata_is_dropped_at_ingest_and_reported(api_client, make_key):
    k = make_key()
    ingest(api_client, k.key, root(), attempt(1, "two"), attempt(2, 0, call_id="not-a-uuid"))
    d = diagnostics(api_client, k.project_id)
    cat = by_category(d)
    (dropped,) = cat["attributes_dropped_at_ingest"]
    assert facts(dropped) == {"spans_affected": 2, "attributes_dropped_total": 3}
    conditions = sorted(
        c for f in cat["attempt_metadata_inconsistent"] for c in facts(f)["conditions"]
    )
    assert conditions == ["attempt_number_missing_or_invalid"]  # span 2 kept neither value
    for f in d["findings"]:
        if f["category"] != "attributes_dropped_at_ingest":
            assert "evidence_attributes_dropped" in {lim["code"] for lim in f["limitations"]}


def test_application_validation_and_finish_reasons(api_client, make_key):
    k = make_key()
    validation = span(
        trace_id=TRACE,
        span_id=sid(5),
        parent=ROOT,
        name="validate",
        start=T0 + 5 * MS,
        attrs={
            "llminsight.validation.rule": "json_schema.v1",
            "llminsight.validation.passed": False,
        },
    )
    ingest(
        api_client,
        k.key,
        root(),
        attempt(1, 1, gen_ai__response__finish_reasons=["length"], gen_ai__request__max_tokens=64),
        validation,
    )
    cat = by_category(diagnostics(api_client, k.project_id))
    assert facts(cat["application_validation_failed"][0])["rule"] == "json_schema.v1"
    assert facts(cat["inference_output_truncated"][0]) == {
        "attempts_affected": 1,
        "attempts_with_request_max_tokens": 1,
    }


def test_the_documented_fixture_is_clean_apart_from_its_unknown_cost(api_client, make_key):
    k = make_key()
    assert post(api_client, body(load_fixture()), k.key).status_code == 200
    d = diagnostics(api_client, k.project_id, FIXTURE_TRACE_ID)
    assert sorted(by_category(d)) == ["cost_unknown_price_missing"]  # no price is seeded
    assert d["coverage"]["complete"] is True


# ---------------------------------------------------------------- privacy
SENTINEL = "PROMPT-SENTINEL-4a91"
FAKE_KEY = "sk-" + "SENTINEL" + "0" * 22  # built at runtime: no key-shaped literal in source


def test_no_content_or_error_text_reaches_the_response_or_logs(api_client, make_key, caplog):
    caplog.set_level(logging.DEBUG)
    k = make_key()
    hostile = attempt(
        1,
        1,
        failed="500",
        gen_ai__input__messages=SENTINEL,
        gen_ai__output__messages=SENTINEL,
        gen_ai__system_instructions=SENTINEL,
        exception__message=SENTINEL,
    )
    hostile.status.message = f"{SENTINEL} {FAKE_KEY}"
    ingest(api_client, k.key, root(attrs={"enduser.id": SENTINEL}), hostile)
    r = api_client.get(path(), params={"project": k.project_id}, headers=ADMIN_AUTH)
    assert r.status_code == 200 and r.json()["findings"]
    assert SENTINEL not in r.text and FAKE_KEY not in r.text
    assert DUMMY_TOKEN not in r.text and k.key not in r.text
    assert SENTINEL not in caplog.text and DUMMY_TOKEN not in caplog.text


def test_span_names_and_raw_attributes_are_not_echoed(api_client, make_key):
    k = make_key()
    named = span(
        trace_id=TRACE,
        span_id=ROOT,
        name="customer-name-Ayse",
        kind=SERVER,
        status_code=ERROR,
        attrs={"service.marker": "x"},
    )
    ingest(api_client, k.key, named)
    r = api_client.get(path(), params={"project": k.project_id}, headers=ADMIN_AUTH)
    assert r.json()["findings"] and "customer-name-Ayse" not in r.text
    assert '"attributes"' not in r.text


# ---------------------------------------------------------------- determinism and bounds
def _mixed_trace():
    return (
        root(status=ERROR),
        attempt(1, 1, failed="429"),
        attempt(2, 2),
        attempt(3, 1, failed="500", call_id="5f0c6e1a-3b7d-4c2e-9a41-00000000a003"),
        attempt(4, 2, failed="500", call_id="5f0c6e1a-3b7d-4c2e-9a41-00000000a003"),
        attempt(6, 1, parent="00000000000000ee", call_id="5f0c6e1a-3b7d-4c2e-9a41-00000000a004"),
    )


def test_reads_are_idempotent_and_independent_of_ingest_order(api_client, make_key):
    a, b = make_key("a"), make_key("b")
    spans_ = _mixed_trace()
    ingest(api_client, a.key, *spans_)
    for s in reversed(spans_):  # one span per batch, in the opposite order
        ingest(api_client, b.key, s)
    ingest(api_client, b.key, *spans_)  # a full re-export changes nothing
    ra = api_client.get(path(), params={"project": a.project_id}, headers=ADMIN_AUTH)
    ra2 = api_client.get(path(), params={"project": a.project_id}, headers=ADMIN_AUTH)
    rb = api_client.get(path(), params={"project": b.project_id}, headers=ADMIN_AUTH)
    assert ra.content == ra2.content == rb.content
    assert len(ra.json()["findings"]) >= 5


def test_reads_never_write(api_client, make_key, db):
    k = make_key()
    ingest(api_client, k.key, *_mixed_trace())
    before = counts(db, k.project_id)
    with db.connect() as c:
        before_tables = c.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        ).scalar_one()
    for _ in range(3):
        diagnostics(api_client, k.project_id)
    assert counts(db, k.project_id) == before
    with db.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")).scalar_one()
            == before_tables
        )


def test_statement_count_is_constant_no_n_plus_one(api_client, make_key):
    """The same fixed set of statements whatever the trace size (no per-span query)."""
    statements: list[str] = []

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    engine = api_client.app.state.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        small, large = make_key("small"), make_key("large")
        ingest(api_client, small.key, root(), attempt(1, 1, failed="429"))
        calls = [
            attempt(10 + i, 1, failed="500", call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{i:012d}")
            for i in range(60)
        ]
        ingest(api_client, large.key, root(), *calls)
        statements.clear()
        diagnostics(api_client, small.project_id)
        n_small = len(statements)
        statements.clear()
        d = diagnostics(api_client, large.project_id)
        n_large = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert d["summary"]["inference_attempts"] == 60
    assert n_small == n_large <= 5, (n_small, n_large)


def test_truncated_trace_is_reported_incomplete(api_client, make_key, monkeypatch):
    monkeypatch.setattr(repo, "DETAIL_MAX_SPANS", 2)
    k = make_key()
    ingest(api_client, k.key, root(), *(attempt(i, i, failed="500") for i in (1, 2, 3)))
    d = diagnostics(api_client, k.project_id)
    assert d["coverage"] == d["coverage"] | {
        "complete": False,
        "spans_stored": 4,
        "spans_analyzed": 2,
        "spans_truncated": True,
    }
    assert d["findings"] and all(f["data_complete"] is False for f in d["findings"])
    # the third attempt was not analysed: the call is reported as a single failed attempt,
    # explicitly incomplete, never as a complete history
    (f,) = by_category(d)["inference_call_failed"]
    assert "analysis_truncated" in {lim["code"] for lim in f["limitations"]}


def test_read_transaction_is_read_only(db):
    with Session(db) as s:
        repo.begin_read_only(s)
        assert s.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
