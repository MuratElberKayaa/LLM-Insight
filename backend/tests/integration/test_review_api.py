"""Phase 4B: human Failure Review (CR-03, T33) and its read-side integration, against real
PostgreSQL. Every trace is written through the real `/v1/traces` ingest; all telemetry and all
review text are SYNTHETIC.
"""

import logging

import pytest
from sqlalchemy import text

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import MS, T0, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
OTHER_TRACE = "5bf92f3577b34da6a3ce929d0e0e4737"
ROOT, CHILD, VALIDATE = "00f067aa0ba90200", "00f067aa0ba90201", "00f067aa0ba90202"
SERVER, CLIENT, INTERNAL = 2, 3, 1
ERROR = 2
ISSUE_TYPES = (
    "retrieval_missing",
    "chunk_boundary",
    "prompt_instruction",
    "generation_mismatch",
    "schema_violation",
    "unknown",
)
NOT_FOUND = {"error": {"code": "not_found", "message": "resource not found"}}
FAKE_KEY = "sk-" + "SYNTHETIC" + "q9w8e7r6t5y4u3i2"


def failing_trace(trace_id: str = TRACE) -> list:
    """Root request span, a failed inference attempt and a failed validation span."""
    return [
        span(trace_id=trace_id, span_id=ROOT, name="POST /v1/summarize", kind=SERVER),
        span(
            trace_id=trace_id,
            span_id=CHILD,
            parent=ROOT,
            name="chat gpt-4o-mini",
            kind=CLIENT,
            start=T0 + MS,
            attrs=inference_attrs(
                gen_ai__response__id=None,
                gen_ai__usage__input_tokens=None,
                gen_ai__usage__output_tokens=None,
                llminsight__usage_source=None,
                error__type="429",
            ),
            status_code=ERROR,
        ),
        span(
            trace_id=trace_id,
            span_id=VALIDATE,
            parent=ROOT,
            name="validate_output",
            kind=INTERNAL,
            start=T0 + 2 * MS,
            attrs={
                "llminsight.validation.rule": "json_schema:summary_v1",
                "llminsight.validation.passed": False,
            },
            status_code=ERROR,
        ),
    ]


@pytest.fixture
def setup(api_client, make_key):
    """Project `demo` with TRACE, project `other` with OTHER_TRACE; returns the ids."""
    demo, other = make_key("demo"), make_key("other")
    for key, trace_id in ((demo.key, TRACE), (other.key, OTHER_TRACE)):
        r = post(api_client, body(request(*failing_trace(trace_id))), key)
        assert r.status_code == 200, r.content
    return demo.project_id, other.project_id


def review_body(**overrides):
    values = {
        "trace_id": TRACE,
        "issue_type": "schema_violation",
        "evidence": "SYNTHETIC: validate_output failed rule json_schema:summary_v1",
        "hypothesis": "SYNTHETIC: prompt v1 may lack an explicit JSON instruction",
        "created_by": "reviewer-a",
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def create(client, project, **overrides):
    return client.post(
        "/api/v1/failure-reviews",
        params={"project": project},
        json=review_body(**overrides),
        headers=ADMIN_AUTH,
    )


def patch(client, project, review_id, payload):
    return client.patch(
        f"/api/v1/failure-reviews/{review_id}",
        params={"project": project},
        json=payload,
        headers=ADMIN_AUTH,
    )


def review_rows(db):
    return rows(db, "SELECT * FROM failure_reviews ORDER BY id")


def detail(client, project, trace_id=TRACE):
    r = client.get(f"/api/v1/traces/{trace_id}", params={"project": project}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- access
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong-token"}], ids=["missing", "wrong"]
)
def test_writes_authenticate_before_any_validation(api_client, setup, db, headers):
    demo, _ = setup
    for method, url, payload in (
        ("POST", "/api/v1/failure-reviews", review_body()),
        ("POST", "/api/v1/failure-reviews", {"garbage": True}),
        ("PATCH", "/api/v1/failure-reviews/1", {"review_status": "closed"}),
    ):
        r = api_client.request(method, url, params={"project": demo}, json=payload, headers=headers)
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
    assert review_rows(db) == []


# ---------------------------------------------------------------- T33
def test_t33_every_issue_type_creates_an_open_pending_review(api_client, setup, db):
    demo, _ = setup
    for issue_type in ISSUE_TYPES:
        r = create(api_client, demo, issue_type=issue_type)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["issue_type"] == issue_type
        assert (out["review_status"], out["reviewer_decision"]) == ("open", "pending")
        assert out["trace_id"] == TRACE and out["span_id"] is None
        assert out["created_by"] == "reviewer-a"
        assert out["test_cases"] == []
        assert out["redaction"] == {"applied": True, "masked": {"evidence": {}, "hypothesis": {}}}
        assert r.headers["cache-control"] == "no-store"
    stored = review_rows(db)
    assert [r["issue_type"] for r in stored] == list(ISSUE_TYPES)
    assert {(r["review_status"], r["reviewer_decision"]) for r in stored} == {("open", "pending")}
    assert {r["project_id"] for r in stored} == {demo}


@pytest.mark.parametrize(
    "issue_type", ["root_cause_SYNTHETIC", "SCHEMA_VIOLATION", "", "retrieval-missing-SYNTHETIC"]
)
def test_t33_invalid_issue_type_is_422_without_echo(api_client, setup, db, issue_type):
    demo, _ = setup
    r = create(api_client, demo, issue_type=issue_type or " ")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_request"
    assert r.json()["error"]["fields"] == ["issue_type"]
    if issue_type:
        assert issue_type not in r.text
    assert review_rows(db) == []


def test_t33_duplicate_open_review_is_409_and_closing_allows_a_new_one(api_client, setup, db):
    demo, _ = setup
    first = create(api_client, demo).json()
    dup = create(api_client, demo, evidence="SYNTHETIC: a second, different text")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "duplicate_open_review"
    assert len(review_rows(db)) == 1
    # in_review is still "not closed"
    assert patch(api_client, demo, first["id"], {"review_status": "in_review"}).status_code == 200
    assert create(api_client, demo).status_code == 409
    closed = patch(
        api_client,
        demo,
        first["id"],
        {"review_status": "closed", "reviewer_decision": "hypothesis_supported"},
    )
    assert closed.status_code == 200
    again = create(api_client, demo)
    assert again.status_code == 201
    assert len(review_rows(db)) == 2


def test_a_different_issue_type_on_the_same_trace_is_allowed(api_client, setup):
    demo, _ = setup
    assert create(api_client, demo, issue_type="schema_violation").status_code == 201
    assert create(api_client, demo, issue_type="prompt_instruction").status_code == 201


# ---------------------------------------------------------------- references and isolation
def test_unknown_foreign_and_missing_references_are_one_identical_404(api_client, setup, db):
    demo, other = setup
    responses = [
        create(api_client, demo, trace_id="f" * 32),  # no such trace
        create(api_client, demo, trace_id=OTHER_TRACE),  # another project's trace
        create(api_client, 999_999, trace_id=TRACE),  # no such project
    ]
    for r in responses:
        assert r.status_code == 404
        assert r.json() == NOT_FOUND
    assert len({(r.content, r.headers.get("content-type")) for r in responses}) == 1
    assert review_rows(db) == []


def test_span_must_belong_to_the_trace(api_client, setup, db):
    demo, _ = setup
    bad = create(api_client, demo, span_id="abcdefabcdef0001")
    assert bad.status_code == 422 and bad.json()["error"]["fields"] == ["span_id"]
    assert review_rows(db) == []
    ok = create(api_client, demo, span_id=VALIDATE)
    assert ok.status_code == 201 and ok.json()["span_id"] == VALIDATE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", "4BF92F3577B34DA6A3CE929D0E0E4736"),
        ("trace_id", "0" * 32),
        ("trace_id", "abc"),
        ("span_id", "0" * 16),
        ("span_id", "xyz"),
    ],
)
def test_malformed_ids_are_422(api_client, setup, db, field, value):
    demo, _ = setup
    r = create(api_client, demo, **{field: value})
    assert r.status_code == 422 and r.json()["error"]["fields"] == [field]
    assert value not in r.text or value == "abc"
    assert review_rows(db) == []


def test_patch_of_another_projects_review_is_404_and_changes_nothing(api_client, setup, db):
    demo, other = setup
    review = create(api_client, demo).json()
    for project, review_id in ((other, review["id"]), (demo, 987654), (999_999, review["id"])):
        r = patch(api_client, project, review_id, {"review_status": "in_review"})
        assert r.status_code == 404 and r.json() == NOT_FOUND
    assert review_rows(db)[0]["review_status"] == "open"


# ---------------------------------------------------------------- redaction and input rules
def test_review_text_is_redacted_before_insert(api_client, setup, db, caplog):
    demo, _ = setup
    caplog.set_level(logging.DEBUG)
    evidence = f"SYNTHETIC: user wrote jane.roe@example.invalid and key {FAKE_KEY}"
    hypothesis = "SYNTHETIC: caller +90 532 123 45 67 retried"
    r = create(api_client, demo, evidence=evidence, hypothesis=hypothesis)
    assert r.status_code == 201
    out = r.json()
    assert out["redaction"]["masked"] == {
        "evidence": {"credential": 1, "email": 1},
        "hypothesis": {"phone": 1},
    }
    stored = review_rows(db)[0]
    for value in (stored["evidence"], stored["hypothesis"], r.text, caplog.text):
        assert "jane.roe@example.invalid" not in value
        assert FAKE_KEY not in value
        assert "532 123 45 67" not in value
    assert stored["evidence"] == (
        "SYNTHETIC: user wrote [REDACTED:email] and key [REDACTED:credential]"
    )
    assert out["evidence"] == stored["evidence"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence", "   "),
        ("evidence", "x" * 4001),
        ("hypothesis", "x" * 2001),
        ("evidence", "hidden\u202etext"),
        ("hypothesis", "zero\u200bwidth"),
        ("created_by", ""),
        ("created_by", "x" * 101),
        ("created_by", "jane.roe@example.invalid"),
        ("created_by", "two\nlines"),
    ],
)
def test_invalid_text_fields_are_422_and_store_nothing(api_client, setup, db, field, value):
    demo, _ = setup
    r = create(api_client, demo, **{field: value})
    assert r.status_code == 422 and r.json()["error"]["fields"] == [field]
    assert "jane.roe" not in r.text
    assert review_rows(db) == []


def test_text_that_grows_past_the_limit_when_masked_is_refused(api_client, setup, db):
    demo, _ = setup
    r = create(api_client, demo, hypothesis="a@b.co " * 285)  # 1995 chars, longer when masked
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["hypothesis"]
    assert review_rows(db) == []


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        (
            {"content": b"{}", "headers": {"Content-Type": "text/plain"}},
            415,
            "unsupported_media_type",
        ),
        (
            {"content": b"not json", "headers": {"Content-Type": "application/json"}},
            422,
            "invalid_request",
        ),
        (
            {"content": b'{"evidence": NaN}', "headers": {"Content-Type": "application/json"}},
            422,
            "invalid_request",
        ),
        (
            {
                "content": b'{"trace_id": "a", "trace_id": "b"}',
                "headers": {"Content-Type": "application/json"},
            },
            422,
            "invalid_request",
        ),
        (
            {"content": b"[]", "headers": {"Content-Type": "application/json"}},
            422,
            "invalid_request",
        ),
        (
            {
                "content": b"{" + b'"evidence": "' + b"x" * 70_000 + b'"}',
                "headers": {"Content-Type": "application/json"},
            },
            413,
            "payload_too_large",
        ),
    ],
    ids=["media-type", "not-json", "nan", "duplicate-key", "not-object", "too-large"],
)
def test_body_format_rules(api_client, setup, db, kwargs, status, code):
    demo, _ = setup
    headers = {**ADMIN_AUTH, **kwargs.pop("headers")}
    r = api_client.post(
        "/api/v1/failure-reviews", params={"project": demo}, headers=headers, **kwargs
    )
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert review_rows(db) == []


@pytest.mark.parametrize(
    "extra",
    [
        {"review_status": "closed"},
        {"reviewer_decision": "hypothesis_supported"},
        {"diagnostic_category": "inference_call_failed"},
        {"issue_type": 5},
        {"evidence": ["SYNTHETIC"]},
    ],
)
def test_create_refuses_unknown_or_mistyped_fields(api_client, setup, db, extra):
    demo, _ = setup
    r = api_client.post(
        "/api/v1/failure-reviews",
        params={"project": demo},
        json={**review_body(), **extra},
        headers=ADMIN_AUTH,
    )
    assert r.status_code == 422 and r.json()["error"]["fields"] == sorted(extra)
    assert review_rows(db) == []


# ---------------------------------------------------------------- PATCH (decision D3)
def test_patch_status_and_decision(api_client, setup, db):
    demo, _ = setup
    review = create(api_client, demo).json()
    r = patch(api_client, demo, review["id"], {"reviewer_decision": "inconclusive"})
    assert r.status_code == 200
    out = r.json()
    assert (out["review_status"], out["reviewer_decision"]) == ("open", "inconclusive")
    assert "test_cases" not in out and "redaction" not in out
    assert out["updated_at"] >= review["updated_at"]
    r = patch(api_client, demo, review["id"], {"review_status": "closed"})
    assert r.status_code == 200 and r.json()["review_status"] == "closed"
    stored = review_rows(db)[0]
    assert (stored["review_status"], stored["reviewer_decision"]) == ("closed", "inconclusive")
    assert stored["evidence"] == review["evidence"]  # text is immutable


def test_patch_cannot_close_without_a_decision(api_client, setup, db):
    demo, _ = setup
    review = create(api_client, demo).json()
    r = patch(api_client, demo, review["id"], {"review_status": "closed"})
    assert r.status_code == 422
    assert r.json()["error"]["fields"] == ["review_status", "reviewer_decision"]
    closed = patch(
        api_client,
        demo,
        review["id"],
        {"review_status": "closed", "reviewer_decision": "inconclusive"},
    )
    assert closed.status_code == 200
    back = patch(api_client, demo, review["id"], {"reviewer_decision": "pending"})
    assert back.status_code == 422
    assert review_rows(db)[0]["reviewer_decision"] == "inconclusive"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"review_status": "approved"},
        {"reviewer_decision": "root_cause_proven"},
        {"evidence": "SYNTHETIC edit"},
        {"review_status": None, "reviewer_decision": None},
        {"review_status": 1},
    ],
)
def test_patch_validation(api_client, setup, db, payload):
    demo, _ = setup
    review = create(api_client, demo).json()
    r = patch(api_client, demo, review["id"], payload)
    assert r.status_code == 422
    stored = review_rows(db)[0]
    assert (stored["review_status"], stored["reviewer_decision"]) == ("open", "pending")
    assert stored["updated_at"] == stored["created_at"]


def test_patch_without_a_change_does_not_write(api_client, setup, db):
    demo, _ = setup
    review = create(api_client, demo).json()
    r = patch(
        api_client, demo, review["id"], {"review_status": "open", "reviewer_decision": "pending"}
    )
    assert r.status_code == 200
    assert r.json()["updated_at"] == review["updated_at"]


def test_reopening_is_refused_when_another_open_review_exists(api_client, setup, db):
    demo, _ = setup
    first = create(api_client, demo).json()
    patch(
        api_client,
        demo,
        first["id"],
        {"review_status": "closed", "reviewer_decision": "inconclusive"},
    )
    create(api_client, demo)
    r = patch(api_client, demo, first["id"], {"review_status": "open"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_open_review"
    assert review_rows(db)[0]["review_status"] == "closed"


# ---------------------------------------------------------------- read side
def test_detail_returns_linked_reviews_and_list_tells_reviewed_traces_apart(api_client, setup):
    demo, other = setup
    assert detail(api_client, demo)["reviews"] == []
    assert detail(api_client, demo)["reviews_truncated"] is False
    a = create(api_client, demo, issue_type="schema_violation").json()
    b = create(api_client, demo, issue_type="unknown").json()
    patch(
        api_client,
        demo,
        b["id"],
        {"review_status": "closed", "reviewer_decision": "hypothesis_rejected"},
    )
    reviews = detail(api_client, demo)["reviews"]
    assert [r["id"] for r in reviews] == [a["id"], b["id"]]
    assert reviews[1]["review_status"] == "closed"
    assert set(reviews[0]) == {
        "id",
        "trace_id",
        "span_id",
        "issue_type",
        "evidence",
        "hypothesis",
        "review_status",
        "reviewer_decision",
        "created_by",
        "created_at",
        "updated_at",
        "test_cases",
    }
    assert detail(api_client, other, OTHER_TRACE)["reviews"] == []  # isolation

    listing = api_client.get("/api/v1/traces", params={"project": demo}, headers=ADMIN_AUTH).json()
    assert [i["reviews"] for i in listing["items"]] == [{"total": 2, "not_closed": 1}]
    other_list = api_client.get(
        "/api/v1/traces", params={"project": other}, headers=ADMIN_AUTH
    ).json()
    assert [i["reviews"] for i in other_list["items"]] == [{"total": 0, "not_closed": 0}]


def test_review_text_is_returned_as_stored_without_interpretation(api_client, setup):
    """Hostile markup is stored and returned as plain JSON text (the UI renders it as text)."""
    demo, _ = setup
    hostile = "<script>alert('SYNTHETIC')</script><img src=x onerror=alert(1)>"
    r = create(api_client, demo, evidence=hostile, created_by="<b>reviewer</b>")
    assert r.status_code == 201
    review = detail(api_client, demo)["reviews"][0]
    assert review["evidence"] == hostile and review["created_by"] == "<b>reviewer</b>"


# ---------------------------------------------------------------- no automatic reviews
def test_diagnostics_and_failing_ingest_never_create_reviews(api_client, setup, db):
    """A failing trace (failed inference, failed validation) plus its diagnostics, detail and
    list reads leave `failure_reviews` empty: only an explicit POST creates a review."""
    demo, _ = setup
    diag = api_client.get(
        f"/api/v1/traces/{TRACE}/diagnostics", params={"project": demo}, headers=ADMIN_AUTH
    ).json()
    categories = {f["category"] for f in diag["findings"]}
    assert {"inference_call_failed", "application_validation_failed"} <= categories
    detail(api_client, demo)
    api_client.get("/api/v1/traces", params={"project": demo}, headers=ADMIN_AUTH)
    assert review_rows(db) == []
    assert not categories & set(ISSUE_TYPES)  # separate vocabularies, nothing to map


def test_read_endpoints_never_write(api_client, setup, db):
    demo, _ = setup
    review = create(api_client, demo).json()
    with db.connect() as c:
        before = c.execute(text("SELECT xmin::text, * FROM failure_reviews")).all()
    for _ in range(3):
        detail(api_client, demo)
        api_client.get("/api/v1/traces", params={"project": demo}, headers=ADMIN_AUTH)
        api_client.get(
            f"/api/v1/traces/{TRACE}/diagnostics", params={"project": demo}, headers=ADMIN_AUTH
        )
    with db.connect() as c:
        after = c.execute(text("SELECT xmin::text, * FROM failure_reviews")).all()
    assert before == after and len(after) == 1 and after[0].id == review["id"]
