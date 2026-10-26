"""Phase 4D: the backend half of the dashboard's experiment pages, against real PostgreSQL.

- the additive read endpoints `GET /api/v1/experiments` and `GET /api/v1/experiment-options`;
- bounded execution: concurrent runs, the per-run deadline, an unexpected evaluation error;
- money in the report is always a plain decimal string (a Phase 4C notation bug fixed here);
- the Phase 4C mutation follow-up (step 7): when the API's own eligibility or freeze check is
  bypassed, the migration-0004 triggers still refuse the write and the API answers with the
  same controlled `409`, never a `500`.

Every case, output, token count, duration and price here is SYNTHETIC; no provider is called.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.api import datasets as datasets_api
from app.api import experiments as experiments_api
from app.api import reviews as reviews_api
from app.services.experiments import golden
from tests.integration import test_experiment_api as experiment_tests
from tests.integration import test_phase4c_integrity as integrity_tests
from tests.integration.ingest_helpers import rows
from tests.integration.test_dataset_api import case_rows
from tests.integration.test_experiment_api import (
    MODEL,
    NOT_FOUND,
    body,
    counts,
    post_json,
    run_experiment,
)
from tests.integration.test_phase4c_integrity import (
    convert,
    review,
    stored_review,
)
from tests.integration.test_review_api import patch

pytestmark = pytest.mark.integration

# Fixtures shared with the Phase 4C modules (resolved against this module's database).
golden_setup = experiment_tests.golden_setup
demo = integrity_tests.demo

PLAIN_DECIMAL = re.compile(r"-?\d+\.\d{10}")
TINY_MODEL = "synthetic-tiny-price-model"
FUTURE_MODEL = "synthetic-future-price-model"


@pytest.fixture(scope="module")
def ingest_url(disposable_db):
    """This module's own database: the SYNTHETIC prices inserted here are append-only."""
    from alembic import command

    from tests.helpers import alembic_config

    url = disposable_db("llminsight_phase4d_test")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="module")
def ingest_engine(ingest_url):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    eng = create_engine(ingest_url, poolclass=NullPool, hide_parameters=True)
    yield eng
    eng.dispose()


def add_price(db, model, per_million, effective_from):
    with db.begin() as c:
        c.execute(
            text(
                "INSERT INTO model_prices (version, provider, model, effective_from,"
                " input_per_million, output_per_million, source_url, verified_at) VALUES"
                " ('synthetic-phase4d-v1', 'openai', :m, :at, :p, :p,"
                " 'https://example.invalid/synthetic-phase4d-price', :at)"
                " ON CONFLICT (provider, model, version) DO NOTHING"
            ),
            {"m": model, "at": effective_from, "p": per_million},
        )


def priced_subset(api_client, project, name):
    """The golden cases except G12 (whose v2 answer has no usage): every estimate is priced,
    so a run with `budget_usd` can pass the pre-check and be stored."""
    from tests.integration.test_dataset_api import make_dataset

    ds = make_dataset(api_client, project, name).json()["id"]
    for case, payload in zip(golden.CASES, golden.case_payloads(), strict=True):
        if case.key != "G12":
            r = post_json(api_client, f"/api/v1/datasets/{ds}/cases", project, payload)
            assert r.status_code == 201, r.text
    return ds


def get(client, path, project, **params):
    from tests.integration.conftest import ADMIN_AUTH

    return client.get(path, params={"project": project, **params}, headers=ADMIN_AUTH)


# ---------------------------------------------------------------- GET /api/v1/experiments
def test_list_is_empty_then_newest_first_with_summaries(api_client, golden_setup, db):
    s = golden_setup
    empty = get(api_client, "/api/v1/experiments", s["demo"])
    assert empty.status_code == 200 and empty.json() == {
        "items": [],
        "truncated": False,
        "limit": 50,
    }
    assert empty.headers["cache-control"] == "no-store"
    first = run_experiment(api_client, s).json()["experiment"]["id"]
    subset = priced_subset(api_client, s["demo"], "priced-subset")
    with_budget = run_experiment(api_client, s, dataset_id=subset, budget_usd=Decimal("1.5"))
    assert with_budget.status_code == 201, with_budget.text
    second = with_budget.json()["experiment"]["id"]
    doc = get(api_client, "/api/v1/experiments", s["demo"]).json()
    assert [x["id"] for x in doc["items"]] == [second, first]
    item = doc["items"][0]
    stored = rows(db, "SELECT * FROM experiments WHERE id = :i", i=second)[0]
    assert item == {
        "id": second,
        "dataset_id": subset,
        "dataset_name": "priced-subset",
        "prompt_version_a": golden.PROMPT_VERSION_A,
        "prompt_version_b": golden.PROMPT_VERSION_B,
        "model": MODEL,
        "mode": "mock",
        "budget_usd": "1.5000000000",
        "started_at": stored["started_at"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "finished_at": stored["finished_at"].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "state": "finished",
        "results": 22,
    }
    assert doc["items"][1]["budget_usd"] is None
    limited = get(api_client, "/api/v1/experiments", s["demo"], limit=1).json()
    assert [x["id"] for x in limited["items"]] == [second] and limited["truncated"] is True


def test_list_is_project_scoped_and_validated(api_client, golden_setup, db):
    s = golden_setup
    assert run_experiment(api_client, s).status_code == 201
    other = get(api_client, "/api/v1/experiments", s["other"])
    assert other.status_code == 200 and other.json()["items"] == []  # never another project's
    unknown = get(api_client, "/api/v1/experiments", 999_999)
    assert unknown.status_code == 404 and unknown.json() == NOT_FOUND
    for bad in ("0", "201", "x"):
        r = get(api_client, "/api/v1/experiments", s["demo"], limit=bad)
        assert r.status_code == 422 and r.json()["error"] == {
            "code": "invalid_request",
            "message": "request parameters are invalid",
            "fields": ["limit"],
        }
    assert api_client.get("/api/v1/experiments", params={"project": s["demo"]}).status_code == 401


# ---------------------------------------------------------------- GET /api/v1/experiment-options
def test_options_offer_mock_only_scripted_versions_and_priced_models(api_client, golden_setup, db):
    add_price(db, FUTURE_MODEL, Decimal("1"), datetime.now(UTC) + timedelta(days=30))
    r = get(api_client, "/api/v1/experiment-options", golden_setup["demo"])
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    doc = r.json()
    assert doc["contract_version"] == "llminsight.experiment_options.v1"
    assert doc["modes"] == ["mock"]
    assert doc["real_mode"] == {"available": False, "code": "real_llm_not_allowed"}
    assert doc["provider"] == {
        "kind": "scripted_mock",
        "simulates": "openai",
        "provenance": "synthetic",
    }
    assert doc["prompt_versions"] == [
        {"prompt_version": golden.PROMPT_VERSION_A, "scripted_inputs": 12},
        {"prompt_version": golden.PROMPT_VERSION_B, "scripted_inputs": 12},
    ]
    models = {m["model"]: m["price_version"] for m in doc["models"]}
    assert models[MODEL] == "synthetic-golden-v1"
    assert FUTURE_MODEL not in models  # a price not yet in force does not count
    assert doc["models_truncated"] is False
    assert doc["limits"] == {
        "max_cases": 200,
        "max_concurrent_runs": 4,
        "run_time_limit_seconds": 15,
        "budget_usd_max": "9999999999.9999999999",
        "budget_usd_decimal_places": 10,
    }
    unknown = get(api_client, "/api/v1/experiment-options", 999_999)
    assert unknown.status_code == 404 and unknown.json() == NOT_FOUND
    no_auth = api_client.get("/api/v1/experiment-options", params={"project": golden_setup["demo"]})
    assert no_auth.status_code == 401


def test_options_name_the_reason_real_mode_is_unavailable(api_settings, golden_setup, db):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.integration.conftest import ADMIN_AUTH

    allowed = api_settings.model_copy(update={"allow_real_llm": True})
    with TestClient(create_app(allowed)) as client:
        doc = client.get(
            "/api/v1/experiment-options",
            params={"project": golden_setup["demo"]},
            headers=ADMIN_AUTH,
        ).json()
        # Even with the flag the build has no real executor: still not offered.
        assert doc["real_mode"] == {"available": False, "code": "real_mode_unavailable"}
        assert doc["modes"] == ["mock"]
        refused = post_json(
            client,
            "/api/v1/experiments",
            golden_setup["demo"],
            body(golden_setup, mode="real", budget_usd=Decimal("1")),
        )
        assert (
            refused.status_code == 409
            and refused.json()["error"]["code"] == "real_mode_unavailable"
        )
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


# ---------------------------------------------------------------- bounded execution
def test_runs_beyond_the_concurrency_bound_are_refused_not_queued(api_client, golden_setup, db):
    slots = api_client.app.state.experiment_slots
    held = [slots.try_acquire() for _ in range(slots.size)]
    assert held == [True] * 4
    try:
        r = run_experiment(api_client, golden_setup)
        assert r.status_code == 429 and r.json()["error"]["code"] == "experiments_busy"
        assert r.headers["retry-after"] == "5"
        assert counts(db) == {"experiments": 0, "experiment_results": 0}
    finally:
        for _ in held:
            slots.release()
    assert run_experiment(api_client, golden_setup).status_code == 201


def test_slots_are_released_after_every_outcome(api_client, golden_setup, db, monkeypatch):
    s = golden_setup
    assert run_experiment(api_client, s, budget_usd=Decimal("0.0000000001")).status_code == 409
    assert run_experiment(api_client, s, dataset_id=999_999).status_code == 404
    monkeypatch.setattr(experiments_api.runner.engine, "evaluate", _explode)
    assert run_experiment(api_client, s).status_code == 500
    monkeypatch.undo()
    for _ in range(6):
        assert run_experiment(api_client, s).status_code == 201
    slots = api_client.app.state.experiment_slots
    assert all(slots.try_acquire() for _ in range(slots.size))  # all free again
    for _ in range(slots.size):
        slots.release()


def test_a_run_past_its_deadline_is_stopped_and_stores_nothing(
    api_client, golden_setup, db, monkeypatch
):
    monkeypatch.setattr(experiments_api, "RUN_TIME_LIMIT_S", -1)
    r = run_experiment(api_client, golden_setup)
    assert r.status_code == 409 and r.json()["error"]["code"] == "experiment_time_limit"
    assert "nothing was stored" in r.json()["error"]["message"]
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


def _explode(*_args, **_kwargs):
    raise ValueError("SYNTHETIC-LEAK-MARKER output text that must never be logged")


def test_an_unexpected_evaluation_error_is_a_controlled_500(
    api_client, golden_setup, db, monkeypatch, caplog
):
    monkeypatch.setattr(experiments_api.runner.engine, "evaluate", _explode)
    with caplog.at_level(logging.ERROR, logger="app.api"):
        r = run_experiment(api_client, golden_setup)
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "internal error"}}
    assert "SYNTHETIC-LEAK-MARKER" not in r.text
    assert "experiment run failed: ValueError" in caplog.text
    assert "SYNTHETIC-LEAK-MARKER" not in caplog.text
    assert counts(db) == {"experiments": 0, "experiment_results": 0}


# ---------------------------------------------------------------- money notation (Phase 4C fix)
def test_money_below_one_millionth_is_a_plain_decimal_string(api_client, golden_setup, db):
    add_price(db, TINY_MODEL, Decimal("0.0010000000"), datetime(2026, 1, 1, tzinfo=UTC))
    subset = priced_subset(api_client, golden_setup["demo"], "tiny-price")
    r = run_experiment(
        api_client,
        golden_setup,
        dataset_id=subset,
        model=TINY_MODEL,
        budget_usd=Decimal("0.0000100000"),
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    costs = [x["cost_usd"] for x in doc["results"] if x["cost_usd"] is not None]
    assert costs and all(PLAIN_DECIMAL.fullmatch(c) for c in costs), costs
    assert any(Decimal(c) < Decimal("0.000001") for c in costs)  # the case str() got wrong
    for v in doc["variants"]:
        assert PLAIN_DECIMAL.fullmatch(v["cost"]["known_cost_usd"])
    assert doc["experiment"]["budget_usd"] == "0.0000100000"
    assert not re.search(r"\d[eE][+-]?\d", r.text)  # no scientific notation anywhere
    listed = get(api_client, "/api/v1/experiments", golden_setup["demo"]).json()["items"][0]
    assert listed["budget_usd"] == "0.0000100000"


# ---------------------------------------------------------------- Phase 4C mutation follow-up
def test_bypassed_eligibility_check_still_ends_in_a_controlled_409(
    api_client, demo, db, monkeypatch
):
    """The Phase 4C mutation that survived the API tests: an `inconclusive` review accepted by
    the API's eligibility check. The `test_cases_review_eligibility` trigger refuses the insert
    and the API maps it to the same `409 review_not_eligible`, storing nothing."""
    rid = review(api_client, demo["project"], "inconclusive")
    monkeypatch.setattr(
        datasets_api, "CONVERTIBLE_DECISIONS", (*datasets_api.CONVERTIBLE_DECISIONS, "inconclusive")
    )
    r = convert(api_client, demo, rid)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "review_not_eligible"
    assert case_rows(db) == []


def test_bypassed_freeze_check_still_ends_in_a_controlled_409(api_client, demo, db, monkeypatch):
    rid = review(api_client, demo["project"], "hypothesis_supported")
    assert convert(api_client, demo, rid).status_code == 201
    before = stored_review(db, rid)
    monkeypatch.setattr(reviews_api.repo, "review_has_cases", lambda *_a: False)
    r = patch(api_client, demo["project"], rid, {"reviewer_decision": "inconclusive"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "review_decision_frozen"
    assert stored_review(db, rid) == before
