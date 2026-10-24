"""Cost through the real ingest path: OTLP/HTTP protobuf → /v1/traces → PostgreSQL → read API
(T17–T23, docs/PRICING.md). All spans are SYNTHETIC telemetry. `openai` rows use the
verified catalogue; `synthetic.*` providers use SYNTHETIC test prices, unique per test
because `model_prices` is append-only and shared by the ingest database."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from app.models import ModelPrice
from app.repositories import pricing as pricing_repo
from app.services import pricing_catalog
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

TRACE = "5b8aa5a2d2c872e8321cf37308d69df2"
ROOT, LLM, LLM2, LLM3 = (
    "1000000000000001",
    "2000000000000002",
    "3000000000000003",
    "4000000000000004",
)
STANDARD = {"openai.response.service_tier": "default", "server.address": "api.openai.com"}


def ns(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 1_000_000_000


AFTER_CATALOGUE = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def verified_catalogue(ingest_engine) -> dict[str, int]:
    """The verified catalogue in the ingest DB (seed is idempotent); model -> price id."""
    with Session(ingest_engine) as s, s.begin():
        pricing_repo.seed(s, pricing_catalog.ENTRIES, dry_run=False)
    found = rows(ingest_engine, "SELECT id, model FROM model_prices WHERE provider = 'openai'")
    return {r["model"]: r["id"] for r in found}


@pytest.fixture
def synthetic_provider(ingest_engine):
    """A fresh SYNTHETIC provider namespace and a helper to add its prices."""
    provider = f"synthetic.t{uuid.uuid4().hex[:12]}"

    def add(version: str, effective_from: datetime, **prices: Decimal) -> int:
        values = {
            "version": version,
            "provider": provider,
            "model": "synthetic-model",
            "effective_from": effective_from,
            "input_per_million": Decimal("1"),
            "output_per_million": Decimal("2"),
            "source_url": "https://example.invalid/synthetic",
            "verified_at": effective_from,
        } | prices
        with ingest_engine.begin() as c:
            return c.execute(
                insert(ModelPrice).values(**values).returning(ModelPrice.id)
            ).scalar_one()

    return provider, add


def llm(span_id=LLM, *, at=AFTER_CATALOGUE, parent=ROOT, **attrs) -> Span:
    return span(
        trace_id=TRACE,
        span_id=span_id,
        parent=parent,
        name="chat gpt-5.4-mini",
        kind=Span.SPAN_KIND_CLIENT,
        start=ns(at),
        end=ns(at) + 40 * MS,
        attrs=inference_attrs(**attrs),
    )


def root(at=AFTER_CATALOGUE, **attrs) -> Span:
    return span(
        trace_id=TRACE,
        span_id=ROOT,
        name="POST /v1/summarize",
        kind=Span.SPAN_KIND_SERVER,
        start=ns(at) - MS,
        end=ns(at) + 50 * MS,
        attrs=attrs,
    )


def gpt54(**overrides):
    """The T19 worked example: 1200 in (1000 cached), 300 out (200 reasoning)."""
    return {
        "gen_ai__request__model": "gpt-5.4-mini",
        "gen_ai__response__model": "gpt-5.4-mini-2026-03-17",
        "gen_ai__response__id": f"chatcmpl-SYNTHETIC-{uuid.uuid4().hex}",
        "gen_ai__usage__input_tokens": 1200,
        "gen_ai__usage__cache_read__input_tokens": 1000,
        "gen_ai__usage__output_tokens": 300,
        "gen_ai__usage__reasoning__output_tokens": 200,
        "openai__response__service_tier": "default",
        "server__address": "api.openai.com",
        **overrides,
    }


def usage(db, span_id=LLM) -> dict:
    (row,) = rows(db, "SELECT * FROM usage_records WHERE span_id = :s", s=span_id)
    return row


def ingest(client, key, *spans):
    response = post(client, body(request(*spans)), key)
    assert response.status_code == 200, response.content
    return response


# ---------------------------------------------------------------- T19 / T20 / T21 / T22
def test_verified_price_gives_the_hand_computed_cost(api_client, db, make_key, verified_catalogue):
    k = make_key()
    ingest(api_client, k.key, root(), llm(**gpt54()))
    u = usage(db)
    assert u["cost_usd"] == Decimal("0.0015750000")  # docs/PRICING.md §4 worked example
    assert (u["cost_status"], u["cost_unknown_reason"]) == ("computed", None)
    assert u["price_version"] == "openai-2026-10-24"
    assert u["model_price_id"] == verified_catalogue["gpt-5.4-mini-2026-03-17"]
    assert (u["input_tokens"], u["cached_input_tokens"]) == (1200, 1000)  # stored as reported
    assert (u["output_tokens"], u["reasoning_output_tokens"]) == (300, 200)
    assert u["usage_source"] == "provider_reported" and u["provenance"] == "synthetic"


def test_root_span_never_carries_cost(api_client, db, make_key, verified_catalogue):
    """A parent span with usage-like attributes produces no usage row: no double count."""
    k = make_key()
    ingest(
        api_client,
        k.key,
        root(**{"gen_ai.usage.input_tokens": 1200, "gen_ai.usage.output_tokens": 300}),
        llm(**gpt54()),
    )
    assert rows(db, "SELECT span_id FROM usage_records") == [{"span_id": LLM}]
    (summary,) = rows(
        db, "SELECT sum(cost_usd) AS total FROM usage_records WHERE cost_status = 'computed'"
    )
    assert summary["total"] == Decimal("0.0015750000")


def test_read_api_shows_usage_and_cost(api_client, db, make_key, verified_catalogue):
    k = make_key()
    ingest(
        api_client,
        k.key,
        root(),
        llm(**gpt54()),
        llm(LLM2, **gpt54(gen_ai__response__model="gpt-5.4")),  # not catalogued
    )
    detail = api_client.get(
        f"/api/v1/traces/{TRACE}", params={"project": k.project_id}, headers=ADMIN_AUTH
    ).json()
    by_span = {u["span_id"]: u for u in detail["usage"]}
    assert by_span[LLM]["cost_usd"] == "0.0015750000"
    assert by_span[LLM]["cost_status"] == "computed"
    assert by_span[LLM]["price_version"] == "openai-2026-10-24"
    assert by_span[LLM]["cached_input_tokens"] == 1000
    assert by_span[LLM]["reasoning_output_tokens"] == 200
    assert by_span[LLM2]["cost_usd"] is None  # unknown is null, never "0"
    assert (by_span[LLM2]["cost_status"], by_span[LLM2]["cost_unknown_reason"]) == (
        "unknown",
        "price_missing",
    )
    window = {
        "from": "2026-10-24T00:00:00Z",
        "to": "2026-10-26T00:00:00Z",
        "project": k.project_id,
    }
    (item,) = api_client.get("/api/v1/traces", params=window, headers=ADMIN_AUTH).json()["items"]
    assert item["usage"]["known_cost_usd"] == "0.0015750000"
    assert item["usage"]["unknown_cost_calls"] == 1
    assert item["usage"]["inference_calls"] == 2
    assert item["usage"]["input_tokens"] == 2400  # known tokens summed; subsets not added


# ---------------------------------------------------------------- T23: idempotent re-export
def test_reexport_leaves_the_cost_row_untouched(api_client, db, make_key, verified_catalogue):
    k = make_key()
    payload = (root(), llm(**gpt54(gen_ai__response__id="chatcmpl-SYNTHETIC-reexport")))
    ingest(api_client, k.key, *payload)
    first = usage(db)
    for _ in range(2):
        ingest(api_client, k.key, *payload)
    assert rows(db, "SELECT count(*) AS n FROM usage_records") == [{"n": 1}]
    assert usage(db) == first  # same id, cost, price row and created_at


def test_double_instrumentation_keeps_the_first_cost(api_client, db, make_key, verified_catalogue):
    """T23-S1: a second span with the same response ID is a collision, not a second cost."""
    k = make_key()
    same = "chatcmpl-SYNTHETIC-double"
    ingest(api_client, k.key, root(), llm(**gpt54(gen_ai__response__id=same)))
    ingest(
        api_client,
        k.key,
        llm(LLM2, **gpt54(gen_ai__response__id=same, gen_ai__usage__output_tokens=999)),
    )
    (row,) = rows(db, "SELECT span_id, cost_usd FROM usage_records")
    assert row == {"span_id": LLM, "cost_usd": Decimal("0.0015750000")}


# ---------------------------------------------------------------- retries (T14 + cost)
def test_each_physical_attempt_is_priced_on_its_own(api_client, db, make_key, verified_catalogue):
    k = make_key()
    call = "7a1c2e3f-0000-4000-8000-00000000c0de"
    failed = llm(
        LLM,
        llminsight__call_id=call,
        llminsight__attempt=1,
        gen_ai__response__id=None,
        gen_ai__response__model=None,
        gen_ai__usage__input_tokens=None,
        gen_ai__usage__output_tokens=None,
        llminsight__usage_source="unknown",
        error__type="429",
        gen_ai__request__model="gpt-5.4-mini",
    )
    ok = llm(
        LLM2,
        at=AFTER_CATALOGUE + timedelta(seconds=1),
        **gpt54(llminsight__call_id=call, llminsight__attempt=2),
    )
    ingest(api_client, k.key, root(), failed, ok)
    first, second = usage(db, LLM), usage(db, LLM2)
    assert (first["attempt_id"], first["cost_status"], first["cost_unknown_reason"]) == (
        1,
        "unknown",
        "usage_missing",
    )
    assert first["cost_usd"] is None and first["model_price_id"] is None
    assert (second["attempt_id"], second["cost_usd"]) == (2, Decimal("0.0015750000"))
    assert first["call_id"] == second["call_id"] == call


# ---------------------------------------------------------------- price_missing paths
@pytest.mark.parametrize(
    "overrides",
    [
        {"gen_ai__response__model": "gpt-5.4"},  # long-context model: not catalogued
        {"gen_ai__response__model": "gpt-5.4-mini-2099-01-01"},  # unknown snapshot
        {"gen_ai__provider__name": "OpenAI"},
        {"openai__response__service_tier": "flex"},
        {"openai__response__service_tier": "priority"},
        {"openai__response__service_tier": None},
        {"server__address": "eu.api.openai.com"},
        {"server__address": None},
        {"gen_ai__usage__cache_write__input_tokens": 10},  # no cache-write price listed
    ],
)
def test_price_missing_stays_null(api_client, db, make_key, verified_catalogue, overrides):
    k = make_key()
    ingest(api_client, k.key, root(), llm(**gpt54(**overrides)))
    u = usage(db)
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )
    assert u["price_version"] is None and u["model_price_id"] is None


def test_event_before_the_verified_catalogue_is_price_missing(
    api_client, db, make_key, verified_catalogue
):
    k = make_key()
    before = pricing_catalog.EFFECTIVE_FROM - timedelta(seconds=1)
    ingest(api_client, k.key, root(at=before), llm(at=before, **gpt54()))
    assert usage(db)["cost_unknown_reason"] == "price_missing"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"llminsight__usage_source": "estimated"}, "usage_missing"),  # T17
        ({"gen_ai__usage__output_tokens": None}, "usage_missing"),  # T18
        ({"gen_ai__usage__cache_read__input_tokens": 1201}, "usage_inconsistent"),
        ({"gen_ai__usage__reasoning__output_tokens": 301}, "usage_inconsistent"),
    ],
)
def test_usage_problems_are_never_priced(
    api_client, db, make_key, verified_catalogue, overrides, reason
):
    k = make_key()
    ingest(api_client, k.key, root(), llm(**gpt54(**overrides)))
    u = usage(db)
    assert (u["cost_usd"], u["cost_status"], u["cost_unknown_reason"]) == (None, "unknown", reason)


# ---------------------------------------------------------------- versions and event time
def test_event_time_selects_the_price_in_force(api_client, db, make_key, synthetic_provider):
    provider, add = synthetic_provider
    v1 = add("synthetic-v1", datetime(2026, 1, 1, tzinfo=UTC))
    v2 = add("synthetic-v2", datetime(2026, 6, 1, tzinfo=UTC), input_per_million=Decimal("3"))
    k = make_key()

    def at(span_id, when):
        return llm(
            span_id,
            at=when,
            gen_ai__provider__name=provider,
            gen_ai__response__model="synthetic-model",
            gen_ai__response__id=f"syn-{span_id}",
            gen_ai__usage__input_tokens=1000,
            gen_ai__usage__output_tokens=0,
        )

    ingest(
        api_client,
        k.key,
        at(LLM, datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)),
        at(LLM2, datetime(2026, 3, 1, tzinfo=UTC)),
        at(LLM3, datetime(2026, 6, 1, tzinfo=UTC)),  # exactly at v2's start
    )
    got = {
        r["span_id"]: (r["cost_usd"], r["price_version"], r["model_price_id"])
        for r in rows(db, "SELECT * FROM usage_records")
    }
    assert got == {
        LLM: (None, None, None),
        LLM2: (Decimal("0.0010000000"), "synthetic-v1", v1),  # 1000 * 1 / 1e6
        LLM3: (Decimal("0.0030000000"), "synthetic-v2", v2),  # 1000 * 3 / 1e6
    }


def test_a_new_price_version_never_rewrites_stored_costs(
    api_client, db, make_key, synthetic_provider
):
    """T22: costs are fixed at first write; a later catalogue version prices only new spans."""
    provider, add = synthetic_provider
    v1 = add("synthetic-v1", datetime(2026, 1, 1, tzinfo=UTC))
    k = make_key()
    attrs = dict(
        gen_ai__provider__name=provider,
        gen_ai__response__model="synthetic-model",
        gen_ai__usage__input_tokens=1000,
        gen_ai__usage__output_tokens=0,
    )
    when = datetime(2026, 7, 1, tzinfo=UTC)
    old = llm(LLM, at=when, gen_ai__response__id="syn-old", **attrs)
    ingest(api_client, k.key, old)
    before = usage(db, LLM)
    assert (before["price_version"], before["model_price_id"]) == ("synthetic-v1", v1)

    v2 = add("synthetic-v2", datetime(2026, 6, 1, tzinfo=UTC), input_per_million=Decimal("3"))
    ingest(api_client, k.key, old)  # re-export after the catalogue change
    ingest(api_client, k.key, llm(LLM2, at=when, gen_ai__response__id="syn-new", **attrs))

    assert usage(db, LLM) == before  # history unchanged: still v1, same cost
    new = usage(db, LLM2)
    assert (new["cost_usd"], new["price_version"], new["model_price_id"]) == (
        Decimal("0.0030000000"),
        "synthetic-v2",
        v2,
    )


def test_unknown_cost_rows_are_not_repriced_when_a_price_appears(
    api_client, db, make_key, synthetic_provider
):
    provider, add = synthetic_provider
    k = make_key()
    attrs = dict(
        gen_ai__provider__name=provider,
        gen_ai__response__model="synthetic-model",
        gen_ai__usage__input_tokens=10,
        gen_ai__usage__output_tokens=10,
    )
    when = datetime(2026, 7, 1, tzinfo=UTC)
    first = llm(LLM, at=when, gen_ai__response__id="syn-unknown", **attrs)
    ingest(api_client, k.key, first)
    before = usage(db, LLM)
    assert before["cost_unknown_reason"] == "price_missing"

    add("synthetic-v1", datetime(2026, 1, 1, tzinfo=UTC))
    ingest(api_client, k.key, first)  # re-export: still unknown, never silently repriced
    assert usage(db, LLM) == before
    ingest(api_client, k.key, llm(LLM2, at=when, gen_ai__response__id="syn-later", **attrs))
    assert usage(db, LLM2)["cost_usd"] == Decimal("0.0000300000")  # 10 * 1 + 10 * 2


def test_costs_are_stored_with_exact_decimals_in_the_database(
    api_client, db, make_key, synthetic_provider
):
    provider, add = synthetic_provider
    add(
        "synthetic-v1",
        datetime(2026, 1, 1, tzinfo=UTC),
        input_per_million=Decimal("0.00035"),
        output_per_million=Decimal("0"),
    )
    k = make_key()
    ingest(
        api_client,
        k.key,
        llm(
            at=datetime(2026, 7, 1, tzinfo=UTC),
            gen_ai__provider__name=provider,
            gen_ai__response__model="synthetic-model",
            gen_ai__usage__input_tokens=1,
            gen_ai__usage__output_tokens=0,
        ),
    )
    (row,) = rows(db, "SELECT cost_usd::text AS cost FROM usage_records")
    assert row["cost"] == "0.0000000004"  # 3.5e-10 rounded half-even, stored as NUMERIC text


def test_price_lookup_uses_one_query_per_batch(api_client, db, make_key, verified_catalogue):
    """Many inference spans in one batch are priced from one model_prices query."""
    k = make_key()
    spans = [root()] + [
        llm(f"{i:016x}", **gpt54()) for i in range(0x5000000000000001, 0x5000000000000033)
    ]
    ingest(api_client, k.key, *spans)
    (n,) = rows(db, "SELECT count(*) AS n FROM usage_records WHERE cost_status = 'computed'")
    assert n["n"] == 50
    with db.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM model_prices WHERE provider = 'openai'")
            ).scalar_one()
            == 6
        )
