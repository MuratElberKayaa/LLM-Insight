"""All fixtures synthetic; real provenance is asserted only to exercise the import contract.
No provider SDK or network call. Disposable database, real OTLP and project-scoped API.
"""

import copy

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.imported_experiments import RULES
from tests import otlp
from tests.helpers import alembic_config
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import counts, post

pytestmark = pytest.mark.integration
PATH = "/api/v1/imported-experiments"


@pytest.fixture
def evidence(api_client, make_key):
    key = make_key("live-import")
    other = make_key("other")
    cases = []
    for i in range(1, 7):
        for j, variant in enumerate(("v1", "v2")):
            trace = f"{i * 2 + j:032x}"
            attrs = otlp.inference_attrs(
                gen_ai__provider__name="groq",
                gen_ai__request__model="openai/gpt-oss-20b",
                gen_ai__response__model="openai/gpt-oss-20b",
                llminsight__provenance="real",
                gen_ai__response__id="SYNTHETIC-" + trace,
                gen_ai__prompt__version="live-ticket-" + variant,
                llminsight__call_id=f"00000000-0000-4000-8000-{i * 2 + j:012d}",
            )
            request = otlp.request(otlp.span(trace_id=trace, kind=3, attrs=attrs))
            assert post(api_client, otlp.body(request), key.key).status_code == 200
            rules = dict.fromkeys(RULES, True)
            rules["expected_priority"] = i <= (3 if variant == "v1" else 5)
            cases.append(
                {"case_id": f"t{i:02d}", "variant": variant, "trace_id": trace, "rules": rules}
            )
    return (
        key.project_id,
        other.project_id,
        {
            "run_id": "a" * 32,
            "model": "openai/gpt-oss-20b",
            "suite": "ticket-ab-v1",
            "rule_version": "ticket-category-priority-v1",
            "cases": cases,
        },
    )


def send(client, project, body):
    return client.post(PATH, params={"project": project}, headers=ADMIN_AUTH, json=body)


def test_import_idempotency_aggregates_and_no_new_telemetry(api_client, evidence, db):
    project, _, body = evidence
    before = counts(db)
    first = send(api_client, project, body)
    assert first.status_code == 200, first.text
    report = first.json()
    assert [g["passed"] for g in report["groups"]] == [3, 5]
    assert [g["input_tokens"] for g in report["groups"]] == [600, 600]
    assert all(g["client_p50_ms"] == 10 for g in report["groups"])
    assert report["cost_status"] == "unknown" and report["winner"] is None
    body["cases"].reverse()  # canonical ordering does not create a second import
    assert send(api_client, project, body).json() == report
    assert counts(db) == before
    listed = api_client.get(PATH, params={"project": project}, headers=ADMIN_AUTH).json()
    assert len(listed["items"]) == 1 and "cases" not in listed["items"][0]
    got = api_client.get(f"{PATH}/{report['id']}", params={"project": project}, headers=ADMIN_AUTH)
    assert got.json() == report and got.headers["cache-control"] == "no-store"
    with pytest.raises(IntegrityError), db.begin() as conn:
        conn.execute(text("UPDATE imported_experiments SET model = 'changed'"))


def test_scope_and_auth(api_client, evidence):
    project, other, body = evidence
    assert api_client.post(PATH, params={"project": project}, json=body).status_code == 401
    assert send(api_client, other, body).status_code == 404
    report = send(api_client, project, body).json()
    assert (
        api_client.get(
            f"{PATH}/{report['id']}", params={"project": other}, headers=ADMIN_AUTH
        ).status_code
        == 404
    )
    assert api_client.get(PATH, params={"project": other}, headers=ADMIN_AUTH).json()["items"] == []


@pytest.mark.parametrize(
    "corruption", ["pair", "trace", "rule", "text", "type", "model", "version"]
)
def test_invalid_import_is_atomic(api_client, evidence, corruption):
    project, _, body = evidence
    body = copy.deepcopy(body)
    expected = 422
    if corruption == "pair":
        body["cases"][0]["case_id"] = "t02"
    if corruption == "trace":
        body["cases"][0]["trace_id"] = body["cases"][1]["trace_id"]
    if corruption == "rule":
        body["cases"][0]["rules"].pop("no_markdown")
    if corruption == "text":
        body["cases"][0]["response"] = "must not store content"
    if corruption == "type":
        body["cases"][0]["rules"]["no_markdown"] = "true"
    if corruption == "model":
        body["model"] = "gemini-3.1-flash-lite"
        expected = 409
    if corruption == "version":
        body["cases"][0]["trace_id"], body["cases"][1]["trace_id"] = (
            body["cases"][1]["trace_id"],
            body["cases"][0]["trace_id"],
        )
        expected = 409
    assert send(api_client, project, body).status_code == expected
    assert (
        api_client.get(PATH, params={"project": project}, headers=ADMIN_AUTH).json()["items"] == []
    )


def test_changed_rule_cannot_overwrite_evidence(api_client, evidence):
    project, _, body = evidence
    report = send(api_client, project, body).json()
    body["cases"][0]["rules"]["expected_priority"] = False
    response = send(api_client, project, body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "import_conflict"
    assert (
        api_client.get(
            f"{PATH}/{report['id']}", params={"project": project}, headers=ADMIN_AUTH
        ).json()
        == report
    )


def test_downgrade_preserves_imported_evidence(api_client, evidence, ingest_url):
    project, _, body = evidence
    report = send(api_client, project, body).json()
    with pytest.raises(RuntimeError, match="refusing to discard"):
        command.downgrade(alembic_config(ingest_url), "0005")
    response = api_client.get(
        f"{PATH}/{report['id']}", params={"project": project}, headers=ADMIN_AUTH
    )
    assert response.json() == report
