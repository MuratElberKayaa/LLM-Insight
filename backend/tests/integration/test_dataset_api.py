"""Phase 4B: datasets and human-approved Trace-to-Testset (T34, T35; decisions D2, D4, D5, D9)
against real PostgreSQL. Traces come through the real `/v1/traces` ingest; every test input,
expected output and review text is SYNTHETIC.
"""

import concurrent.futures
import json

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api import datasets as datasets_api
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.integration.test_review_api import (
    FAKE_KEY,
    NOT_FOUND,
    OTHER_TRACE,
    TRACE,
    create,
    failing_trace,
    patch,
)
from tests.otlp import body, request

pytestmark = pytest.mark.integration

SCHEMA_RULE = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    },
}
TELEMETRY = ("projects", "project_api_keys", "traces", "spans", "usage_records", "model_prices")


@pytest.fixture
def setup(api_client, make_key):
    """Two projects with one failing trace each, a decided review on each trace and one
    dataset per project. Returns a dict of ids."""
    demo, other = make_key("demo"), make_key("other")
    for key, trace_id in ((demo.key, TRACE), (other.key, OTHER_TRACE)):
        assert post(api_client, body(request(*failing_trace(trace_id))), key).status_code == 200
    ids = {"demo": demo.project_id, "other": other.project_id}
    for name, project, trace_id in (
        ("demo", demo.project_id, TRACE),
        ("other", other.project_id, OTHER_TRACE),
    ):
        review = create(api_client, project, trace_id=trace_id).json()
        r = patch(api_client, project, review["id"], {"reviewer_decision": "hypothesis_supported"})
        assert r.status_code == 200
        ids[f"{name}_review"] = review["id"]
        ds = make_dataset(api_client, project, f"regression-{name}")
        assert ds.status_code == 201, ds.text
        ids[f"{name}_dataset"] = ds.json()["id"]
    return ids


def make_dataset(client, project, name):
    return client.post(
        "/api/v1/datasets", params={"project": project}, json={"name": name}, headers=ADMIN_AUTH
    )


def case_body(ids=None, **overrides):
    values = {
        "approved": True,
        "approved_by": "reviewer-a",
        "sanitized_input": "SYNTHETIC: summarize the release note 'v2 adds dark mode' as JSON",
        "expected_output": {"summary": "SYNTHETIC: v2 adds dark mode"},
        "rule": SCHEMA_RULE,
    }
    if ids is not None:
        values |= {"origin_trace_id": TRACE, "origin_review_id": ids["demo_review"]}
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not _ABSENT}


_ABSENT = object()


def add_case(client, project, dataset_id, payload):
    return client.post(
        f"/api/v1/datasets/{dataset_id}/cases",
        params={"project": project},
        json=payload,
        headers=ADMIN_AUTH,
    )


def case_rows(db):
    return rows(db, "SELECT * FROM test_cases ORDER BY id")


def telemetry_dump(db):
    with db.connect() as c:
        return {
            t: [tuple(r) for r in c.execute(text(f"SELECT * FROM {t} ORDER BY id"))]  # noqa: S608 - constant names
            for t in TELEMETRY
        }


# ---------------------------------------------------------------- datasets (D2)
def test_dataset_create_list_and_isolation(api_client, setup, db):
    demo, other = setup["demo"], setup["other"]
    second = make_dataset(api_client, demo, "regression-extra")
    assert second.status_code == 201
    out = second.json()
    assert set(out) == {"id", "name", "created_at", "case_count"} and out["case_count"] == 0
    dup = make_dataset(api_client, demo, "regression-extra")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_dataset"
    assert make_dataset(api_client, other, "regression-extra").status_code == 201  # per project
    listing = api_client.get("/api/v1/datasets", params={"project": demo}, headers=ADMIN_AUTH)
    assert listing.status_code == 200
    assert [d["name"] for d in listing.json()["items"]] == ["regression-demo", "regression-extra"]
    assert listing.json()["truncated"] is False
    limited = api_client.get(
        "/api/v1/datasets", params={"project": demo, "limit": 1}, headers=ADMIN_AUTH
    ).json()
    assert len(limited["items"]) == 1 and limited["truncated"] is True
    missing = api_client.get("/api/v1/datasets", params={"project": 999_999}, headers=ADMIN_AUTH)
    assert missing.status_code == 404 and missing.json() == NOT_FOUND
    assert make_dataset(api_client, 999_999, "x").status_code == 404


@pytest.mark.parametrize(
    "name", ["", " ", "x" * 101, "two\nlines", "owner jane.roe@example.invalid", "a\u202eb"]
)
def test_dataset_name_rules(api_client, setup, db, name):
    r = make_dataset(api_client, setup["demo"], name)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["name"]
    assert len(rows(db, "SELECT id FROM test_datasets")) == 2


# ---------------------------------------------------------------- T34
@pytest.mark.parametrize(
    "approved",
    [_ABSENT, False, None, "true", 1, "yes"],
    ids=["missing", "false", "null", "string", "one", "yes"],
)
def test_t34_no_approval_no_test_case(api_client, setup, db, approved):
    payload = case_body(setup, approved=approved)
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], payload)
    assert r.status_code == 422
    assert r.json()["error"]["fields"] == ["approved"]
    assert case_rows(db) == []


@pytest.mark.parametrize("missing", ["approved_by", "sanitized_input", "rule"])
def test_required_fields(api_client, setup, db, missing):
    r = add_case(
        api_client, setup["demo"], setup["demo_dataset"], case_body(setup, **{missing: _ABSENT})
    )
    assert r.status_code == 422 and missing in r.json()["error"]["fields"]
    assert case_rows(db) == []


# ---------------------------------------------------------------- T35
def test_t35_input_is_masked_before_insert(api_client, setup, db):
    raw = (
        "SYNTHETIC: email jane.roe@example.invalid, phone +90 532 123 45 67,"
        f" key {FAKE_KEY}, card 4111 1111 1111 1111"
    )
    r = add_case(
        api_client, setup["demo"], setup["demo_dataset"], case_body(setup, sanitized_input=raw)
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["redaction"] == {
        "applied": True,
        "masked": {"sanitized_input": {"credential": 1, "email": 1, "phone": 1, "number": 1}},
    }
    stored = case_rows(db)[0]
    assert stored["redaction_applied"] is True
    assert stored["sanitized_input"] == (
        "SYNTHETIC: email [REDACTED:email], phone [REDACTED:phone],"
        " key [REDACTED:credential], card [REDACTED:number]"
    )
    dump = json.dumps(rows(db, "SELECT * FROM test_cases"), default=str)
    for secret in ("jane.roe", "532 123", FAKE_KEY, "4111"):
        assert secret not in dump and secret not in r.text


def test_t35_clean_input_still_records_redaction_applied(api_client, setup, db):
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup))
    assert r.status_code == 201
    assert r.json()["redaction"]["masked"] == {"sanitized_input": {}}
    assert case_rows(db)[0]["redaction_applied"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_output", {"contact": "jane.roe@example.invalid"}),
        ("expected_output", {"phone": 905321234567}),
        ("expected_output", {"key": FAKE_KEY}),
        ("expected_output", {"jane.roe@example.invalid": "x"}),
        ("expected_output", {"t": "zero\u200bwidth"}),
        # literal contact data in an assertion (an escaped pattern holds none and is allowed)
        ("rule", {"type": "regex", "pattern": "^jane.roe@example.invalid$"}),
        ("approved_by", "jane.roe@example.invalid"),
    ],
)
def test_structured_fields_and_labels_with_sensitive_values_are_refused(
    api_client, setup, db, field, value
):
    r = add_case(
        api_client, setup["demo"], setup["demo_dataset"], case_body(setup, **{field: value})
    )
    assert r.status_code == 422 and r.json()["error"]["fields"] == [field]
    assert "jane.roe" not in r.text and FAKE_KEY not in r.text
    assert case_rows(db) == []


# ---------------------------------------------------------------- content rules
@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"sanitized_input": "  "}, "sanitized_input"),
        ({"sanitized_input": "x" * 8001}, "sanitized_input"),
        ({"sanitized_input": "a\u202eb"}, "sanitized_input"),
        ({"rule": {"type": "llm_judge"}}, "rule"),
        ({"rule": {"type": "exact", "weight": 1}}, "rule"),
        (
            {"rule": {"type": "json_schema", "schema": {"$ref": "https://example.invalid/x"}}},
            "rule",
        ),
        ({"rule": {"type": "regex", "pattern": "("}}, "rule"),
        ({"rule": {"type": "field_match", "fields": ["missing"]}}, "expected_output"),
        ({"rule": "exact"}, "rule"),
        ({"expected_output": {"a": "x" * 17000}}, "expected_output"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_case_content_validation(api_client, setup, db, override, field):
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup, **override))
    assert r.status_code == 422 and r.json()["error"]["fields"] == [field], r.text
    assert case_rows(db) == []


def test_case_without_expected_output_is_stored_for_later_not_evaluated(api_client, setup, db):
    """The T40 precondition: a case may have no expected output (it is never evaluated here)."""
    r = add_case(
        api_client,
        setup["demo"],
        setup["demo_dataset"],
        case_body(setup, expected_output=None, rule={"type": "exact"}),
    )
    assert r.status_code == 201 and r.json()["expected_output"] is None
    assert rows(db, "SELECT expected_output IS NULL AS n FROM test_cases") == [{"n": True}]


def test_standalone_synthetic_case_without_origin(api_client, setup, db):
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body())
    assert r.status_code == 201
    assert (r.json()["origin_trace_id"], r.json()["origin_review_id"]) == (None, None)


# ---------------------------------------------------------------- origin rules (D4) and isolation
def test_trace_to_testset_records_provenance(api_client, setup, db):
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup))
    assert r.status_code == 201
    out = r.json()
    assert out["origin_trace_id"] == TRACE and out["origin_review_id"] == setup["demo_review"]
    assert out["approved_by"] == "reviewer-a" and out["approved_at"].endswith("Z")
    assert out["dataset_id"] == setup["demo_dataset"]
    detail = api_client.get(
        f"/api/v1/traces/{TRACE}", params={"project": setup["demo"]}, headers=ADMIN_AUTH
    ).json()
    assert detail["reviews"][0]["test_cases"] == [
        {"id": out["id"], "dataset_id": setup["demo_dataset"], "dataset_name": "regression-demo"}
    ]


@pytest.mark.parametrize(
    "override",
    [{"origin_review_id": _ABSENT}, {"origin_trace_id": _ABSENT}],
    ids=["trace-without-review", "review-without-trace"],
)
def test_origin_needs_both_references(api_client, setup, db, override):
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup, **override))
    assert r.status_code == 422
    assert r.json()["error"]["fields"] == ["origin_review_id", "origin_trace_id"]
    assert case_rows(db) == []


def test_pending_review_cannot_be_converted(api_client, setup, db):
    review = create(api_client, setup["demo"], issue_type="unknown").json()
    r = add_case(
        api_client,
        setup["demo"],
        setup["demo_dataset"],
        case_body(setup, origin_review_id=review["id"]),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "review_not_decided"
    assert case_rows(db) == []


def test_review_of_a_different_trace_is_refused(api_client, setup, make_key, db):
    third = "6bf92f3577b34da6a3ce929d0e0e4738"
    key = make_key("demo")
    assert post(api_client, body(request(*failing_trace(third))), key.key).status_code == 200
    r = add_case(
        api_client, setup["demo"], setup["demo_dataset"], case_body(setup, origin_trace_id=third)
    )
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["origin_review_id"]
    assert case_rows(db) == []


def test_cross_project_references_are_indistinguishable_404s(api_client, setup, db):
    demo = setup["demo"]
    attempts = [
        # another project's dataset
        add_case(api_client, demo, setup["other_dataset"], case_body(setup)),
        # another project's trace and review into my dataset
        add_case(
            api_client,
            demo,
            setup["demo_dataset"],
            case_body(setup, origin_trace_id=OTHER_TRACE, origin_review_id=setup["other_review"]),
        ),
        # my trace with another project's review
        add_case(
            api_client,
            demo,
            setup["demo_dataset"],
            case_body(setup, origin_review_id=setup["other_review"]),
        ),
        # unknown dataset, unknown review, unknown project
        add_case(api_client, demo, 987_654, case_body(setup)),
        add_case(
            api_client, demo, setup["demo_dataset"], case_body(setup, origin_review_id=987_654)
        ),
        add_case(api_client, 999_999, setup["demo_dataset"], case_body(setup)),
        # reading another project's cases
        api_client.get(
            f"/api/v1/datasets/{setup['other_dataset']}/cases",
            params={"project": demo},
            headers=ADMIN_AUTH,
        ),
    ]
    for r in attempts:
        assert r.status_code == 404 and r.json() == NOT_FOUND
    assert len({r.content for r in attempts}) == 1
    assert case_rows(db) == []


# ---------------------------------------------------------------- duplicates (D5)
def test_second_conversion_of_a_review_into_a_dataset_is_409(api_client, setup, db):
    first = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup))
    assert first.status_code == 201
    again = add_case(
        api_client,
        setup["demo"],
        setup["demo_dataset"],
        case_body(setup, sanitized_input="SYNTHETIC: a different input"),
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "duplicate_test_case"
    other_ds = make_dataset(api_client, setup["demo"], "regression-second").json()["id"]
    assert add_case(api_client, setup["demo"], other_ds, case_body(setup)).status_code == 201
    assert len(case_rows(db)) == 2


def test_concurrent_conversions_store_exactly_one_case(api_settings, setup, db):
    """Eight simultaneous conversions of one review into one dataset over real HTTP: the
    advisory lock serializes them, so one 201 and seven 409s, and one row."""
    from tests.integration.conftest import run_live_server

    payload = case_body(setup)
    with run_live_server(api_settings) as server:

        def send(_: int) -> int:
            return httpx.post(
                f"{server.url}/api/v1/datasets/{setup['demo_dataset']}/cases",
                params={"project": setup["demo"]},
                json=payload,
                headers=ADMIN_AUTH,
                timeout=30,
            ).status_code

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            statuses = sorted(pool.map(send, range(8)))
    assert statuses == [201] + [409] * 7
    assert len(case_rows(db)) == 1


# ---------------------------------------------------------------- atomicity
def test_storage_failure_after_insert_rolls_back(api_client, setup, db, monkeypatch):
    original = datasets_api.repo.insert_case

    def insert_then_fail(session, values):
        original(session, values)
        assert session.execute(text("SELECT count(*) FROM test_cases")).scalar_one() == 1
        raise OperationalError("SELECT 1", {}, Exception("SYNTHETIC connection loss"))

    monkeypatch.setattr(datasets_api.repo, "insert_case", insert_then_fail)
    r = add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup))
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert r.headers["retry-after"] == "5"
    assert case_rows(db) == []


# ---------------------------------------------------------------- reading cases (4C readiness)
def test_case_listing_is_stable_paginated_and_deterministic(api_client, setup, db):
    demo, ds = setup["demo"], setup["demo_dataset"]
    ids = [
        add_case(
            api_client,
            demo,
            ds,
            case_body(sanitized_input=f"SYNTHETIC case {i}", rule={"type": "exact"}),
        ).json()["id"]
        for i in range(5)
    ]
    url = f"/api/v1/datasets/{ds}/cases"
    first = api_client.get(url, params={"project": demo, "limit": 2}, headers=ADMIN_AUTH)
    assert first.status_code == 200
    page1 = first.json()
    assert [c["id"] for c in page1["items"]] == ids[:2]
    assert page1["dataset"]["case_count"] == 5 and page1["next_after"] == ids[1]
    page2 = api_client.get(
        url, params={"project": demo, "limit": 2, "after": page1["next_after"]}, headers=ADMIN_AUTH
    ).json()
    assert [c["id"] for c in page2["items"]] == ids[2:4]
    full = [
        api_client.get(url, params={"project": demo}, headers=ADMIN_AUTH).content for _ in range(3)
    ]
    assert len(set(full)) == 1  # byte-identical repeated reads
    item = json.loads(full[0])["items"][0]
    assert set(item) == {
        "id",
        "dataset_id",
        "origin_trace_id",
        "origin_review_id",
        "sanitized_input",
        "expected_output",
        "rule",
        "redaction_applied",
        "approved_by",
        "approved_at",
        "created_at",
    }
    assert item["rule"] == {"type": "exact"}


# ---------------------------------------------------------------- immutability
def test_reviews_and_cases_leave_telemetry_usage_and_prices_untouched(api_client, setup, db):
    before = telemetry_dump(db)
    review = create(api_client, setup["demo"], issue_type="generation_mismatch").json()
    patch(
        api_client,
        setup["demo"],
        review["id"],
        {"review_status": "closed", "reviewer_decision": "inconclusive"},
    )
    add_case(api_client, setup["demo"], setup["demo_dataset"], case_body(setup))
    make_dataset(api_client, setup["demo"], "regression-immutability")
    assert telemetry_dump(db) == before
