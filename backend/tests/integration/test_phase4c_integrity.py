"""Phase 4C Gate A: review integrity and conversion uniqueness (ADR-24; decisions D3, D4, D5 as
approved), and exact numbers in stored assertions (rule contract v1), against real PostgreSQL.
All traces, reviews and test content are SYNTHETIC.

Concurrency is tested twice: through the API over real HTTP (the handlers' row locks), and
with two raw database connections that bypass the API entirely (the migration-0004 triggers
and index alone must keep the data consistent).
"""

import concurrent.futures
import threading
import time

import httpx
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.api import datasets as datasets_api
from tests.integration.conftest import ADMIN_AUTH, run_live_server
from tests.integration.ingest_helpers import post, rows
from tests.integration.test_dataset_api import add_case, case_body, case_rows, make_dataset
from tests.integration.test_review_api import TRACE, create, failing_trace, patch
from tests.otlp import body, request

pytestmark = pytest.mark.integration

EXACT_TEXT = {"version": 1, "type": "exact", "output_format": "text"}


@pytest.fixture
def demo(api_client, make_key):
    key = make_key("demo")
    assert post(api_client, body(request(*failing_trace(TRACE))), key.key).status_code == 200
    ds = make_dataset(api_client, key.project_id, "integrity").json()["id"]
    return {"project": key.project_id, "dataset": ds}


def review(api_client, project, decision=None, issue_type="schema_violation"):
    r = create(api_client, project, issue_type=issue_type).json()
    if decision is not None:
        assert (
            patch(api_client, project, r["id"], {"reviewer_decision": decision}).status_code == 200
        )
    return r["id"]


def convert(api_client, d, review_id, **overrides):
    payload = case_body(origin_trace_id=TRACE, origin_review_id=review_id, **overrides)
    return add_case(api_client, d["project"], d["dataset"], payload)


def stored_review(db, review_id):
    return rows(db, "SELECT * FROM failure_reviews WHERE id = :i", i=review_id)[0]


# ---------------------------------------------------------------- conversion eligibility (D4)
def test_supported_review_converts_with_or_without_expected_output(api_client, demo, db):
    rid = review(api_client, demo["project"], "hypothesis_supported")
    r = convert(api_client, demo, rid, expected_output=None, rule=EXACT_TEXT)
    assert r.status_code == 201, r.text  # stored; never evaluated (T40)


def test_rejected_review_needs_an_independently_supplied_expected_output(api_client, demo, db):
    rid = review(api_client, demo["project"], "hypothesis_rejected")
    r = convert(api_client, demo, rid, expected_output=None, rule=EXACT_TEXT)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["expected_output"]
    assert case_rows(db) == []
    ok = convert(api_client, demo, rid)
    assert ok.status_code == 201, ok.text
    assert ok.json()["expected_output"] == {"summary": "SYNTHETIC: v2 adds dark mode"}


@pytest.mark.parametrize("decision", [None, "inconclusive"], ids=["pending", "inconclusive"])
def test_pending_and_inconclusive_reviews_are_not_eligible(api_client, demo, db, decision):
    rid = review(api_client, demo["project"], decision)
    r = convert(api_client, demo, rid)
    code = "review_not_decided" if decision is None else "review_not_eligible"
    assert r.status_code == 409 and r.json()["error"]["code"] == code
    assert case_rows(db) == []


# ---------------------------------------------------------------- decision freeze (D3)
def test_decision_is_frozen_once_a_case_cites_the_review(api_client, demo, db):
    project = demo["project"]
    rid = review(api_client, project, "hypothesis_supported")
    assert convert(api_client, demo, rid).status_code == 201
    before = stored_review(db, rid)
    for change in (
        {"reviewer_decision": "hypothesis_rejected"},
        {"reviewer_decision": "inconclusive"},
        {"reviewer_decision": "pending"},  # clearing it
        {"review_status": "closed", "reviewer_decision": "inconclusive"},
    ):
        r = patch(api_client, project, rid, change)
        assert r.status_code == 409 and r.json()["error"]["code"] == "review_decision_frozen"
        assert stored_review(db, rid) == before  # nothing written, updated_at unchanged
    same = patch(api_client, project, rid, {"reviewer_decision": "hypothesis_supported"})
    assert same.status_code == 200 and stored_review(db, rid) == before  # a no-op
    closed = patch(api_client, project, rid, {"review_status": "closed"})
    assert closed.status_code == 200
    assert closed.json()["reviewer_decision"] == "hypothesis_supported"
    reopened = patch(api_client, project, rid, {"review_status": "in_review"})
    assert reopened.status_code == 200  # status changes stay possible within §4.9


def test_decision_of_a_review_without_cases_may_still_change(api_client, demo, db):
    rid = review(api_client, demo["project"], "hypothesis_supported")
    r = patch(api_client, demo["project"], rid, {"reviewer_decision": "inconclusive"})
    assert r.status_code == 200 and r.json()["reviewer_decision"] == "inconclusive"


def test_review_text_has_no_write_path(api_client, demo, db):
    rid = review(api_client, demo["project"])
    before = stored_review(db, rid)
    for field in ("evidence", "hypothesis", "issue_type", "created_by", "trace_id"):
        r = patch(api_client, demo["project"], rid, {field: "SYNTHETIC changed"})
        assert r.status_code == 422 and r.json()["error"]["fields"] == [field]
    assert stored_review(db, rid) == before


# ---------------------------------------------------------------- concurrency through the API
def test_concurrent_decision_change_and_conversion_never_both_succeed(api_settings, demo, db):
    """For each of 10 fresh reviews, a PATCH to `inconclusive` and a conversion are released at
    the same instant over real HTTP. Exactly one wins, and the stored state matches it: either
    the case exists and the decision is still `hypothesis_supported`, or the decision is
    `inconclusive` and no case exists."""
    project, dataset = demo["project"], demo["dataset"]
    outcomes = set()
    with run_live_server(api_settings) as server, httpx.Client(timeout=30) as http:
        for i in range(10):
            created = http.post(
                f"{server.url}/api/v1/failure-reviews",
                params={"project": project},
                json={
                    "trace_id": TRACE,
                    "issue_type": "unknown" if i % 2 else "chunk_boundary",
                    "evidence": f"SYNTHETIC evidence {i}",
                    "hypothesis": f"SYNTHETIC hypothesis {i}",
                    "created_by": "race-test",
                },
                headers=ADMIN_AUTH,
            ).json()
            rid = created["id"]
            assert (
                http.patch(
                    f"{server.url}/api/v1/failure-reviews/{rid}",
                    params={"project": project},
                    json={"reviewer_decision": "hypothesis_supported"},
                    headers=ADMIN_AUTH,
                ).status_code
                == 200
            )
            barrier = threading.Barrier(2)

            def change(rid=rid, barrier=barrier) -> int:
                barrier.wait()
                return httpx.patch(
                    f"{server.url}/api/v1/failure-reviews/{rid}",
                    params={"project": project},
                    json={"review_status": "closed", "reviewer_decision": "inconclusive"},
                    headers=ADMIN_AUTH,
                    timeout=30,
                ).status_code

            def convert_now(rid=rid, barrier=barrier, i=i) -> int:
                barrier.wait()
                return httpx.post(
                    f"{server.url}/api/v1/datasets/{dataset}/cases",
                    params={"project": project},
                    json=case_body(
                        origin_trace_id=TRACE,
                        origin_review_id=rid,
                        sanitized_input=f"SYNTHETIC race input {i}",
                    ),
                    headers=ADMIN_AUTH,
                    timeout=30,
                ).status_code

            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                f_patch, f_case = pool.submit(change), pool.submit(convert_now)
                result = (f_patch.result(), f_case.result())
            stored = stored_review(db, rid)["reviewer_decision"]
            cases = rows(db, "SELECT id FROM test_cases WHERE origin_review_id = :i", i=rid)
            assert result in {(409, 201), (200, 409)}, result
            if result == (409, 201):
                assert stored == "hypothesis_supported" and len(cases) == 1
            else:
                assert stored == "inconclusive" and cases == []
            outcomes.add(result)
            # Close it (the decision stays as stored), so the next iteration's review of the
            # same trace and issue type is not a duplicate open review.
            closing = http.patch(
                f"{server.url}/api/v1/failure-reviews/{rid}",
                params={"project": project},
                json={"review_status": "closed"},
                headers=ADMIN_AUTH,
            )
            assert closing.status_code == 200
    assert outcomes  # at least one ordering was exercised (both usually are)


def test_database_index_alone_keeps_one_case_per_review(api_settings, demo, db, monkeypatch):
    """With the API's advisory lock and duplicate check disabled, eight simultaneous
    conversions still store one case: the partial unique index refuses the others, and they
    get the same friendly 409 as before."""
    monkeypatch.setattr(datasets_api.repo, "lock_review_conversion", lambda *a: None)
    monkeypatch.setattr(datasets_api.repo, "case_exists_for_review", lambda *a: None)
    with run_live_server(api_settings) as server:
        rid = None
        with httpx.Client(timeout=30) as http:
            rid = http.post(
                f"{server.url}/api/v1/failure-reviews",
                params={"project": demo["project"]},
                json={
                    "trace_id": TRACE,
                    "issue_type": "prompt_instruction",
                    "evidence": "SYNTHETIC evidence",
                    "hypothesis": "SYNTHETIC hypothesis",
                    "created_by": "race-test",
                },
                headers=ADMIN_AUTH,
            ).json()["id"]
            http.patch(
                f"{server.url}/api/v1/failure-reviews/{rid}",
                params={"project": demo["project"]},
                json={"reviewer_decision": "hypothesis_supported"},
                headers=ADMIN_AUTH,
            )
        payload = case_body(origin_trace_id=TRACE, origin_review_id=rid)
        barrier = threading.Barrier(8)

        def send(_: int) -> tuple[int, str]:
            barrier.wait()
            r = httpx.post(
                f"{server.url}/api/v1/datasets/{demo['dataset']}/cases",
                params={"project": demo["project"]},
                json=payload,
                headers=ADMIN_AUTH,
                timeout=30,
            )
            return r.status_code, r.json().get("error", {}).get("code", "")

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results = sorted(pool.map(send, range(8)))
    assert results == [(201, "")] + [(409, "duplicate_test_case")] * 7
    assert len(case_rows(db)) == 1


# ---------------------------------------------------------------- concurrency below the API
def _raw(db):
    url = make_url(db.url.render_as_string(hide_password=False))
    return psycopg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
        connect_timeout=5,
    )


def _seed_review(db, decision):
    """A project, trace, decided review and dataset written directly (no API)."""
    with db.begin() as c:
        c.execute(text("INSERT INTO projects (id, name) VALUES (1, 'raw')"))
        c.execute(
            text(
                "INSERT INTO traces (project_id, trace_id, start_ns, end_ns, started_at,"
                " ended_at, status, span_count) VALUES (1, :t, 1, 2, now(), now(), 'ok', 1)"
            ),
            {"t": TRACE},
        )
        rid = c.execute(
            text(
                "INSERT INTO failure_reviews (project_id, trace_id, issue_type, evidence,"
                " hypothesis, created_by, reviewer_decision) VALUES (1, :t, 'unknown',"
                " 'SYNTHETIC e', 'SYNTHETIC h', 'raw', :d) RETURNING id"
            ),
            {"t": TRACE, "d": decision},
        ).scalar_one()
        ds = c.execute(
            text("INSERT INTO test_datasets (project_id, name) VALUES (1, 'raw') RETURNING id")
        ).scalar_one()
    return rid, ds


_INSERT_CASE = (
    "INSERT INTO test_cases (dataset_id, origin_trace_id, origin_review_id, sanitized_input,"
    " expected_output, rule, redaction_applied, approved_by, approved_at) VALUES"
    " (%s, %s, %s, 'SYNTHETIC raw', '\"ACK\"'::jsonb,"
    ' \'{"version": 1, "type": "exact", "output_format": "text"}\'::jsonb, true, \'raw\', now())'
)


def _in_thread(fn):
    result: dict = {}

    def run():
        try:
            result["value"] = fn()
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            result["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    return t, result


def test_raw_sql_decision_change_waits_for_a_pending_case_insert_then_fails(db):
    rid, ds = _seed_review(db, "hypothesis_supported")
    with _raw(db) as inserter, _raw(db) as updater:
        cur = inserter.cursor()
        cur.execute(_INSERT_CASE, (ds, TRACE, rid))  # holds FOR SHARE on the review
        t, result = _in_thread(
            lambda: updater.execute(
                "UPDATE failure_reviews SET reviewer_decision = 'inconclusive' WHERE id = %s",
                (rid,),
            )
        )
        time.sleep(0.5)
        assert t.is_alive()  # blocked on the row lock, not decided yet
        inserter.commit()
        t.join(10)
        updater.rollback()
    assert isinstance(result.get("error"), psycopg.errors.IntegrityConstraintViolation)
    assert result["error"].diag.constraint_name == "failure_reviews_decision_frozen"
    assert stored_review(db, rid)["reviewer_decision"] == "hypothesis_supported"
    assert len(case_rows(db)) == 1


def test_raw_sql_case_insert_waits_for_a_pending_decision_change_then_fails(db):
    rid, ds = _seed_review(db, "hypothesis_supported")
    with _raw(db) as updater, _raw(db) as inserter:
        updater.execute(
            "UPDATE failure_reviews SET reviewer_decision = 'inconclusive' WHERE id = %s", (rid,)
        )
        t, result = _in_thread(lambda: inserter.execute(_INSERT_CASE, (ds, TRACE, rid)))
        time.sleep(0.5)
        assert t.is_alive()  # FOR SHARE waits for the uncommitted change
        updater.commit()
        t.join(10)
        inserter.rollback()
    assert isinstance(result.get("error"), psycopg.errors.IntegrityConstraintViolation)
    assert result["error"].diag.constraint_name == "test_cases_review_eligibility"
    assert stored_review(db, rid)["reviewer_decision"] == "inconclusive"
    assert case_rows(db) == []


# ---------------------------------------------------------------- exact numbers (rule v1)
def test_assertion_numbers_keep_their_exact_digits(api_client, demo, db):
    """`0.1000000000000000000001` would be `0.1` after a float round trip; `1.10` keeps its
    scale. The request is sent as raw JSON text, the way any client could send it."""
    raw = (
        '{"approved": true, "approved_by": "reviewer-a",'
        ' "sanitized_input": "SYNTHETIC: return the price as JSON",'
        ' "expected_output": {"price": 0.1000000000000000000001, "qty": 1.10, "n": 3},'
        ' "rule": {"version": 1, "type": "json_schema", "schema": {"type": "object",'
        ' "properties": {"price": {"type": "number", "minimum": 0.1}}}}}'
    )
    r = api_client.post(
        f"/api/v1/datasets/{demo['dataset']}/cases",
        params={"project": demo["project"]},
        content=raw,
        headers={**ADMIN_AUTH, "Content-Type": "application/json"},
    )
    assert r.status_code == 201, r.text
    assert '"price":0.1000000000000000000001' in r.text and '"qty":1.10' in r.text
    assert '"minimum":0.1' in r.text
    stored = rows(db, "SELECT expected_output::text AS e FROM test_cases")[0]["e"]
    assert "0.1000000000000000000001" in stored and "1.10" in stored
    listing = api_client.get(
        f"/api/v1/datasets/{demo['dataset']}/cases",
        params={"project": demo["project"]},
        headers=ADMIN_AUTH,
    )
    assert '"price":0.1000000000000000000001' in listing.text


@pytest.mark.parametrize(
    "number", ["1e999", "1" * 70, "NaN", "Infinity"], ids=["exponent", "digits", "nan", "inf"]
)
def test_numbers_beyond_the_exact_range_are_refused(api_client, demo, db, number):
    raw = (
        '{"approved": true, "approved_by": "reviewer-a", "sanitized_input": "SYNTHETIC x",'
        f' "expected_output": {{"n": {number}}},'
        ' "rule": {"version": 1, "type": "exact", "output_format": "json"}}'
    )
    r = api_client.post(
        f"/api/v1/datasets/{demo['dataset']}/cases",
        params={"project": demo["project"]},
        content=raw,
        headers={**ADMIN_AUTH, "Content-Type": "application/json"},
    )
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["body"]
    assert case_rows(db) == []
