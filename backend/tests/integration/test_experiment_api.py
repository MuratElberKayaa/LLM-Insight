"""Phase 4C Gate B: mock A/B experiments over the golden dataset through the real API and real
PostgreSQL (T36–T40). Every case, output, token count, duration and price here is SYNTHETIC;
no provider is called.
"""

import concurrent.futures
import ipaddress
import socket
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api import experiments as experiments_api
from app.core import exact_json
from app.services.experiments import golden
from tests.helpers import alembic_config
from tests.integration.conftest import ADMIN_AUTH, run_live_server
from tests.integration.ingest_helpers import rows
from tests.integration.test_dataset_api import make_dataset
from tests.integration.test_review_api import FAKE_KEY

pytestmark = pytest.mark.integration

MODEL = "synthetic-golden-model"
PRICE_IN, PRICE_OUT = Decimal("0.1500000000"), Decimal("0.6000000000")
A, B = golden.PROMPT_VERSION_A, golden.PROMPT_VERSION_B
NOT_FOUND = {"error": {"code": "not_found", "message": "resource not found"}}
TELEMETRY = ("traces", "spans", "usage_records", "model_prices")


@pytest.fixture(scope="module")
def ingest_url(disposable_db):
    """A database of this module's own: the SYNTHETIC (openai, MODEL) price inserted here is
    append-only and would otherwise remain visible to other modules' price assertions."""
    from alembic import command

    url = disposable_db("llminsight_experiment_test")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="module")
def ingest_engine(ingest_url):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    eng = create_engine(ingest_url, poolclass=NullPool, hide_parameters=True)
    yield eng
    eng.dispose()


def post_json(client, path, project, payload):
    return client.post(
        path,
        params={"project": project},
        content=exact_json.dumps(payload),
        headers={**ADMIN_AUTH, "Content-Type": "application/json"},
    )


def ensure_price(db):
    """A SYNTHETIC price for (openai, MODEL), inserted once per database (prices are
    append-only and survive the per-test truncation)."""
    with db.begin() as c:
        c.execute(
            text(
                "INSERT INTO model_prices (version, provider, model, effective_from,"
                " input_per_million, output_per_million, source_url, verified_at) VALUES"
                " ('synthetic-golden-v1', 'openai', :m, :at, :pin, :pout,"
                " 'https://example.invalid/synthetic-golden-price', :at)"
                " ON CONFLICT (provider, model, version) DO NOTHING"
            ),
            {
                "m": MODEL,
                "at": datetime(2026, 1, 1, tzinfo=UTC),
                "pin": PRICE_IN,
                "pout": PRICE_OUT,
            },
        )


@pytest.fixture
def golden_setup(api_client, make_key, db):
    """Project `demo` with the golden dataset seeded through the API; `other` with its own."""
    ensure_price(db)
    demo, other = make_key("demo").project_id, make_key("other").project_id
    ds = make_dataset(api_client, demo, golden.DATASET_NAME).json()["id"]
    ids = {}
    for case, payload in zip(golden.CASES, golden.case_payloads(), strict=True):
        r = post_json(api_client, f"/api/v1/datasets/{ds}/cases", demo, payload)
        assert r.status_code == 201, r.text
        assert r.json()["redaction"]["masked"] == {"sanitized_input": {}}
        ids[case.key] = r.json()["id"]
    other_ds = make_dataset(api_client, other, "other-golden").json()["id"]
    return {"demo": demo, "other": other, "dataset": ds, "other_dataset": other_ds, "ids": ids}


def body(s, **overrides):
    values = {
        "dataset_id": s["dataset"],
        "prompt_version_a": A,
        "prompt_version_b": B,
        "model": MODEL,
        "mode": "mock",
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def run_experiment(client, s, **overrides):
    return post_json(client, "/api/v1/experiments", s["demo"], body(s, **overrides))


def expected_cost(answer) -> Decimal:
    exact = (answer.input_tokens * PRICE_IN + answer.output_tokens * PRICE_OUT) / Decimal(10**6)
    return exact.quantize(Decimal("1E-10"), rounding=ROUND_HALF_EVEN)


def counts(db):
    return {
        t: rows(db, f"SELECT count(*) AS n FROM {t}")[0]["n"]  # noqa: S608 - constant names
        for t in ("experiments", "experiment_results")
    }


# ---------------------------------------------------------------- T36–T40 end to end
def test_golden_experiment_end_to_end(api_client, golden_setup, db):
    s = golden_setup
    r = run_experiment(api_client, s)
    assert r.status_code == 201, r.text
    doc = r.json()
    by_key = {v: k for k, v in s["ids"].items()}

    # T38: both versions ran on the identical case ids.
    assert doc["cases"] == {"count": 12, "ids_identical_across_versions": True}
    stored = rows(
        db,
        "SELECT test_case_id, prompt_version, passed, evaluation_detail, duration_ms,"
        " input_tokens, output_tokens, cost_usd::text AS cost, cost_status,"
        " cost_unknown_reason, provenance FROM experiment_results ORDER BY id",
    )
    assert len(stored) == 24
    ids = {v: sorted(x["test_case_id"] for x in stored if x["prompt_version"] == v) for v in (A, B)}
    assert ids[A] == ids[B] == sorted(s["ids"].values())

    # T39: every result is labelled, and mock means synthetic.
    assert {x["provenance"] for x in stored} == {"synthetic"}
    assert doc["provenance"] == {
        "label": "synthetic",
        "synthetic": 24,
        "real": 0,
        "simulated_provider": True,
    }
    assert {x["provenance"] for x in doc["results"]} == {"synthetic"}

    # Every documented golden outcome, as stored.
    for x in stored:
        case = golden.CASES[int(by_key[x["test_case_id"]][1:]) - 1]
        want_passed, want_code = case.expected[x["prompt_version"]]
        assert x["passed"] is want_passed, (case.key, x["prompt_version"])
        if want_passed is True:
            assert x["evaluation_detail"] is None
        else:
            assert x["evaluation_detail"].startswith(f"{want_code}: ")

    def result(key, version):
        return next(
            x
            for x in doc["results"]
            if x["test_case_id"] == s["ids"][key] and x["prompt_version"] == version
        )

    # T36: valid output passes the JSON Schema rule (G01) and the field rule (G05).
    for key in ("G01", "G05"):
        assert result(key, A)["passed"] is True and result(key, B)["passed"] is True
    # T37: a schema violation fails with evaluation_detail.
    g02 = result("G02", A)
    assert g02["passed"] is False and g02["detail_code"] == "schema_violation"
    assert (
        g02["evaluation_detail"].startswith("schema_violation: ")
        and "required" in (g02["evaluation_detail"])
    )
    # T40: no expected output -> passed NULL, outside the pass-rate denominator.
    for version in (A, B):
        g11 = result("G11", version)
        assert g11["passed"] is None and g11["outcome"] == "not_evaluated"
        assert g11["detail_code"] == "expected_output_missing"
    va, vb = doc["variants"]
    assert (va["passed"], va["failed"], va["not_evaluated"]) == (7, 4, 1)
    assert (vb["passed"], vb["failed"], vb["not_evaluated"]) == (4, 6, 2)
    assert va["pass_rate"] == {"value": 0.636364, "numerator": 7, "denominator": 11}
    assert vb["pass_rate"] == {"value": 0.4, "numerator": 4, "denominator": 10}
    assert vb["not_evaluated_by_code"] == {"expected_output_missing": 1, "provider_timeout": 1}

    # Exact Decimal cost from the recorded synthetic tokens; the timeout's cost stays unknown.
    want_a = sum(expected_cost(c.answers[A]) for c in golden.CASES)
    want_b = sum(expected_cost(c.answers[B]) for c in golden.CASES if c.answers[B].output)
    assert va["cost"]["known_cost_usd"] == str(want_a) and va["cost"]["complete"] is True
    assert vb["cost"]["known_cost_usd"] == str(want_b) and vb["cost"]["complete"] is False
    assert vb["cost"]["unknown_by_reason"] == {"usage_missing": 1}
    g12b = result("G12", B)
    assert (g12b["cost_usd"], g12b["cost_status"], g12b["cost_unknown_reason"]) == (
        None,
        "unknown",
        "usage_missing",
    )
    assert result("G01", A)["cost_usd"] == str(expected_cost(golden.CASES[0].answers[A]))
    assert rows(db, "SELECT count(*) AS n FROM experiment_results WHERE cost_usd = 0") == [{"n": 0}]

    # No better version is named on incomplete evidence; partial cost totals are not compared.
    assert doc["comparison"]["quality"] == {
        "status": "partial",
        "paired_cases": 10,
        "excluded_by_design": 1,
        "unpaired_cases": 1,
        "a_passed_on_paired": 7,
        "b_passed_on_paired": 4,
        "higher_pass_rate": None,
        "reason": doc["comparison"]["quality"]["reason"],
    }
    assert doc["comparison"]["cost"]["status"] == "incomplete"
    assert doc["comparison"]["cost"]["difference_usd_b_minus_a"] is None
    assert va["duration_ms"]["n"] == 12 and vb["duration_ms"]["n"] == 11

    exp = rows(db, "SELECT * FROM experiments")[0]
    assert exp["mode"] == "mock" and exp["budget_usd"] is None
    assert exp["finished_at"] >= exp["started_at"]
    assert doc["experiment"]["state"] == "finished"

    # GET returns the same document.
    again = api_client.get(
        f"/api/v1/experiments/{doc['experiment']['id']}",
        params={"project": s["demo"]},
        headers=ADMIN_AUTH,
    )
    assert again.status_code == 200 and again.json() == doc
    assert again.headers["cache-control"] == "no-store"


def test_outputs_are_never_persisted_or_returned(api_client, golden_setup, db):
    r = run_experiment(api_client, golden_setup)
    dump = str(rows(db, "SELECT * FROM experiment_results")) + str(
        rows(db, "SELECT * FROM experiments")
    )
    for fragment in (
        "Printer offline (floor 3)",
        "REFUND APPROVED",
        "Sure! The title",
        "double charge",
    ):
        assert fragment not in r.text and fragment not in dump


def test_experiments_are_reproducible(api_client, golden_setup, db):
    first = run_experiment(api_client, golden_setup).json()
    second = run_experiment(api_client, golden_setup).json()
    assert first["experiment"]["id"] != second["experiment"]["id"]
    assert first["results"] == second["results"]
    assert first["variants"] == second["variants"]
    assert first["comparison"] == second["comparison"]


def test_concurrent_experiments_are_independent_and_complete(api_settings, golden_setup, db):
    payload = exact_json.dumps(body(golden_setup))
    with run_live_server(api_settings) as server:

        def send(_: int):
            r = httpx.post(
                f"{server.url}/api/v1/experiments",
                params={"project": golden_setup["demo"]},
                content=payload,
                headers={**ADMIN_AUTH, "Content-Type": "application/json"},
                timeout=60,
            )
            return r.status_code, r.json()

        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            outcomes = list(pool.map(send, range(4)))
    assert [status for status, _ in outcomes] == [201] * 4
    assert len({doc["experiment"]["id"] for _, doc in outcomes}) == 4
    assert len({exact_json.dumps(doc["results"]) for _, doc in outcomes}) == 1
    per_experiment = rows(
        db, "SELECT experiment_id, count(*) AS n FROM experiment_results GROUP BY 1"
    )
    assert sorted(x["n"] for x in per_experiment) == [24] * 4


# ---------------------------------------------------------------- refusals (nothing written)
def test_real_mode_is_refused_and_writes_nothing(api_client, api_settings, golden_setup, db):
    s = golden_setup
    no_budget = run_experiment(api_client, s, mode="real")
    assert no_budget.status_code == 422 and no_budget.json()["error"]["fields"] == ["budget_usd"]
    refused = run_experiment(api_client, s, mode="real", budget_usd=Decimal("1"))
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "real_llm_not_allowed"
    from fastapi.testclient import TestClient

    from app.main import create_app

    allowed = api_settings.model_copy(update={"allow_real_llm": True})
    with TestClient(create_app(allowed)) as client:
        r = run_experiment(client, s, mode="real", budget_usd=Decimal("1"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "real_mode_unavailable"
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


def test_budget_is_enforced_before_execution(api_client, golden_setup, db):
    s = golden_setup
    unverifiable = run_experiment(api_client, s, budget_usd=Decimal("100"))
    assert unverifiable.status_code == 409
    assert unverifiable.json()["error"]["code"] == "budget_unverifiable"  # G12 v2: no usage
    assert counts(db) == {"experiments": 0, "experiment_results": 0}

    priced = make_dataset(api_client, s["demo"], "golden-priced").json()["id"]
    for case, payload in zip(golden.CASES, golden.case_payloads(), strict=True):
        if case.key != "G12":
            post_json(api_client, f"/api/v1/datasets/{priced}/cases", s["demo"], payload)
    total = sum(expected_cost(c.answers[v]) for c in golden.CASES if c.key != "G12" for v in (A, B))
    over = run_experiment(api_client, s, dataset_id=priced, budget_usd=total - Decimal("1E-10"))
    assert over.status_code == 409 and over.json()["error"]["code"] == "budget_exceeded"
    assert counts(db) == {"experiments": 0, "experiment_results": 0}
    ok = run_experiment(api_client, s, dataset_id=priced, budget_usd=total)
    assert ok.status_code == 201, ok.text
    doc = ok.json()
    assert doc["experiment"]["budget_usd"] == str(total.quantize(Decimal("1E-10")))
    spent = sum(Decimal(v["cost"]["known_cost_usd"]) for v in doc["variants"])
    assert spent == total and doc["comparison"]["cost"]["status"] == "complete"


@pytest.mark.parametrize(
    ("override", "fields"),
    [
        ({"prompt_version_b": A}, ["prompt_version_a", "prompt_version_b"]),
        ({"prompt_version_a": "has space"}, ["prompt_version_a"]),
        ({"prompt_version_a": "x" * 65}, ["prompt_version_a"]),
        ({"model": "owner jane.roe@example.invalid"}, ["model"]),
        ({"model": FAKE_KEY}, ["model"]),
        ({"mode": "live"}, ["mode"]),
        ({"budget_usd": Decimal("0")}, ["budget_usd"]),
        ({"budget_usd": Decimal("-1")}, ["budget_usd"]),
        ({"budget_usd": Decimal("0.00000000001")}, ["budget_usd"]),  # 11 decimal places
        ({"budget_usd": "5"}, ["budget_usd"]),
        ({"budget_usd": True}, ["budget_usd"]),
        ({"dataset_id": 0}, ["dataset_id"]),
        ({"surprise": 1}, ["surprise"]),
    ],
)
def test_request_validation(api_client, golden_setup, db, override, fields):
    r = run_experiment(api_client, golden_setup, **override)
    assert r.status_code == 422 and r.json()["error"]["fields"] == fields, r.text
    assert "jane.roe" not in r.text and FAKE_KEY not in r.text
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


def test_project_isolation_and_unknown_records(api_client, golden_setup, db):
    s = golden_setup
    mine = run_experiment(api_client, s).json()["experiment"]["id"]
    attempts = [
        run_experiment(api_client, s, dataset_id=s["other_dataset"]),
        run_experiment(api_client, s, dataset_id=987_654),
        post_json(api_client, "/api/v1/experiments", 999_999, body(s)),
        api_client.get(
            f"/api/v1/experiments/{mine}", params={"project": s["other"]}, headers=ADMIN_AUTH
        ),
        api_client.get(
            "/api/v1/experiments/987654", params={"project": s["demo"]}, headers=ADMIN_AUTH
        ),
    ]
    for r in attempts:
        assert r.status_code == 404 and r.json() == NOT_FOUND
    assert len({r.content for r in attempts}) == 1
    assert counts(db)["experiments"] == 1


def test_auth_comes_first(api_client, golden_setup, db):
    r = api_client.post("/api/v1/experiments", params={"project": golden_setup["demo"]}, json={})
    assert r.status_code == 401
    r = api_client.get("/api/v1/experiments/1", params={"project": golden_setup["demo"]})
    assert r.status_code == 401


def test_empty_and_oversized_datasets_are_refused(api_client, golden_setup, db, monkeypatch):
    s = golden_setup
    empty = make_dataset(api_client, s["demo"], "empty").json()["id"]
    r = run_experiment(api_client, s, dataset_id=empty)
    assert r.status_code == 409 and r.json()["error"]["code"] == "dataset_empty"
    monkeypatch.setattr(experiments_api, "MAX_EXPERIMENT_CASES", 5)
    r = run_experiment(api_client, s)
    assert r.status_code == 409 and r.json()["error"]["code"] == "dataset_too_large"
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


def test_a_legacy_unversioned_rule_is_reported_not_evaluated(api_client, golden_setup, db):
    """A Phase 4B rule written before contract v1 (here directly in SQL) is never
    reinterpreted: both versions report it `rule_unsupported`, outside the denominator."""
    s = golden_setup
    with db.begin() as c:
        legacy = c.execute(
            text(
                "INSERT INTO test_cases (dataset_id, sanitized_input, expected_output, rule,"
                " redaction_applied, approved_by, approved_at) VALUES (:d, :i, '\"ACK\"'::jsonb,"
                " '{\"type\": \"exact\"}'::jsonb, true, 'legacy', now()) RETURNING id"
            ),
            {"d": s["dataset"], "i": golden.CASES[6].sanitized_input + " (legacy copy)"},
        ).scalar_one()
    doc = run_experiment(api_client, s).json()
    legacy_results = [x for x in doc["results"] if x["test_case_id"] == legacy]
    assert [x["detail_code"] for x in legacy_results] == ["mock_no_scripted_output"] * 2
    # with a scripted input the rule itself is checked first:
    with db.begin() as c:
        c.execute(
            text("UPDATE test_cases SET sanitized_input = :i WHERE id = :id"),
            {"i": golden.CASES[6].sanitized_input, "id": legacy},
        )
        c.execute(text("DELETE FROM test_cases WHERE id = :id"), {"id": s["ids"]["G07"]})
    doc = run_experiment(api_client, s).json()
    legacy_results = [x for x in doc["results"] if x["test_case_id"] == legacy]
    assert [(x["passed"], x["detail_code"]) for x in legacy_results] == [
        (None, "rule_unsupported")
    ] * 2


def test_storage_failure_rolls_the_whole_run_back(api_client, golden_setup, db, monkeypatch):
    def fail(session, rows_):
        raise OperationalError("INSERT", {}, Exception("SYNTHETIC connection loss"))

    monkeypatch.setattr(experiments_api.repo, "insert_results", fail)
    r = run_experiment(api_client, golden_setup)
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


# ---------------------------------------------------------------- isolation from telemetry
def test_experiments_leave_telemetry_usage_and_prices_untouched(api_client, golden_setup, db):
    def dump():
        with db.connect() as c:
            return {
                t: [tuple(r) for r in c.execute(text(f"SELECT * FROM {t} ORDER BY id"))]  # noqa: S608 - constant names
                for t in TELEMETRY
            }

    before = dump()
    assert run_experiment(api_client, golden_setup).status_code == 201
    assert dump() == before  # no usage row is fabricated from synthetic tokens


def test_no_external_network_is_used(api_client, golden_setup, db, monkeypatch):
    """Only loopback connections (the local PostgreSQL) are allowed during a run."""
    attempts: list[str] = []
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in ("127.0.0.1", "localhost", "::1"):
            attempts.append(str(host))
            raise OSError("non-loopback name resolution blocked")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            attempts.append(str(host))
            raise OSError("non-loopback connection blocked")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    r = run_experiment(api_client, golden_setup)
    assert r.status_code == 201
    assert attempts == []
