"""Phase 9B: the list-price reference through the real ingest path and the read API
(ADR-52, PRICING.md §9).

Every span is SYNTHETIC and made by this test; no provider is called. The tests pin the
invariants of `reports/phase-9a.md` §3: the ledger is never written or repriced (I1, I2), the
`cost` block keeps the ledger values (I3), the reference sits only in its own block and never in
the overview (I4), and only provider-reported, consistent usage is priced (I5).

| Trace | Provider / model | Prompt version | Usage |
|---|---|---|---|
| 1 | openai / gpt-5.4-mini → …-2026-03-17 (ledger-priced) | v1 | 1200 (1000 cached) / 300 |
| 2 | gcp.gemini / gemini-3.1-flash-lite | v1 | 1200 in (200 cached) / 300 out (100 thinking) |
| 3, 4 | groq / openai/gpt-oss-20b | cmp | 222 / 96 (60 reasoning) and 244 / 363 (307 reasoning) |
| 5 | groq / openai/gpt-oss-20b | cmp | estimated usage → usage_missing |
| 6 | groq / openai/gpt-oss-20b | cmp | 50 cache-write tokens → not representable |
| 7 | openrouter / synthetic/compat-only:free | cmp | 20 / 32 |
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from sqlalchemy.orm import Session

from app.repositories import pricing as pricing_repo
from app.services import pricing_catalog
from app.services import reference_pricing_catalog as cat
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

AFTER_CATALOGUE = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
WINDOW = {"from": "2026-10-25T00:00:00Z", "to": "2026-10-26T00:00:00Z"}
SEC = 1_000_000_000
GROQ, GEMINI = "openai/gpt-oss-20b", "gemini-3.1-flash-lite"
OPENROUTER = "synthetic/compat-only:free"  # SYNTHETIC: no OpenRouter trace exists
OPENAI_REQUESTED, OPENAI_RESPONSE = "gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"
OPENAI_COST = Decimal("0.0015750000")  # docs/PRICING.md §4 worked example


def ns(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * SEC


BASE = ns(AFTER_CATALOGUE)


def client_span(i: int, provider: str, requested: str, response: str, version: str, **kw) -> Span:
    attrs = inference_attrs(
        gen_ai__provider__name=provider,
        gen_ai__request__model=requested,
        gen_ai__response__model=response,
        gen_ai__response__id=f"chatcmpl-SYNTHETIC-9b-{i}",
        gen_ai__prompt__name="demo.reference",
        gen_ai__prompt__version=version,
        llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{9_000_000 + i:012d}",
        **kw,
    )
    start = BASE + i * SEC
    return span(
        trace_id=f"9b0{i:029x}",
        span_id=f"{i:08x}{2:08x}",
        name=f"chat {requested}",
        kind=Span.SPAN_KIND_CLIENT,
        start=start,
        end=start + 40 * MS,
        attrs=attrs,
    )


def tokens(inp: int, out: int, **kw) -> dict:
    return {"gen_ai__usage__input_tokens": inp, "gen_ai__usage__output_tokens": out, **kw}


@pytest.fixture(scope="session")
def openai_catalogue(ingest_engine) -> None:
    with Session(ingest_engine) as s, s.begin():
        pricing_repo.seed(s, pricing_catalog.ENTRIES, dry_run=False)  # idempotent


@pytest.fixture
def seeded(api_client, make_key, openai_catalogue):
    k = make_key("p9b")
    spans = [
        client_span(
            1,
            "openai",
            OPENAI_REQUESTED,
            OPENAI_RESPONSE,
            "v1",
            **tokens(
                1200,
                300,
                gen_ai__usage__cache_read__input_tokens=1000,
                gen_ai__usage__reasoning__output_tokens=200,
            ),
            server__address="api.openai.com",
            openai__response__service_tier="default",
        ),
        client_span(
            2,
            "gcp.gemini",
            GEMINI,
            GEMINI,
            "v1",
            **tokens(
                1200,
                300,
                gen_ai__usage__cache_read__input_tokens=200,
                gen_ai__usage__reasoning__output_tokens=100,
            ),
        ),
        client_span(
            3,
            "groq",
            GROQ,
            GROQ,
            "cmp",
            **tokens(222, 96, gen_ai__usage__reasoning__output_tokens=60),
        ),
        client_span(
            4,
            "groq",
            GROQ,
            GROQ,
            "cmp",
            **tokens(244, 363, gen_ai__usage__reasoning__output_tokens=307),
        ),
        client_span(
            5, "groq", GROQ, GROQ, "cmp", **tokens(30, 30), llminsight__usage_source="estimated"
        ),
        client_span(
            6,
            "groq",
            GROQ,
            GROQ,
            "cmp",
            **tokens(100, 10, gen_ai__usage__cache_write__input_tokens=50),
        ),
        client_span(7, "openrouter", OPENROUTER, OPENROUTER, "cmp", **tokens(20, 32)),
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    return k


def get(client, path, project, **params):
    r = client.get(path, params={"project": project, **WINDOW, **params}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def by_provider(groups) -> dict[str, dict]:
    out = {g["provider"]: g for g in groups}
    assert len(out) == len(groups)
    return out


def ledger(db, project_id: int) -> list[dict]:
    return [
        dict(r)
        for r in rows(
            db,
            "SELECT id, provider, requested_model, response_model, usage_source, input_tokens,"
            " output_tokens, cached_input_tokens, cache_write_input_tokens,"
            " reasoning_output_tokens, cost_usd, cost_status, cost_unknown_reason, price_version,"
            " model_price_id FROM usage_records WHERE project_id = :p ORDER BY id",
            p=project_id,
        )
    ]


# ---------------------------------------------------------------- I1, I2: the ledger is untouched
def test_reading_the_reference_never_writes_or_reprices_the_ledger(seeded, db, api_client):
    before = ledger(db, seeded.project_id)
    prices_before = rows(db, "SELECT count(*) AS n FROM model_prices")[0]["n"]
    get(api_client, "/api/v1/models/compare", seeded.project_id)
    get(api_client, "/api/v1/overview", seeded.project_id)
    assert ledger(db, seeded.project_id) == before
    assert rows(db, "SELECT count(*) AS n FROM model_prices")[0]["n"] == prices_before
    outcome = {(r["provider"], r["cost_status"], r["cost_unknown_reason"]) for r in before}
    assert outcome == {
        ("openai", "computed", None),
        ("gcp.gemini", "unknown", "price_missing"),
        ("groq", "unknown", "price_missing"),
        ("groq", "unknown", "usage_missing"),
        ("openrouter", "unknown", "price_missing"),
    }
    assert all(r["cost_usd"] is None for r in before if r["provider"] != "openai")


# ---------------------------------------------------------------- I3, I4: separate blocks
def test_cost_blocks_keep_the_ledger_values(seeded, api_client):
    groups = by_provider(get(api_client, "/api/v1/models/compare", seeded.project_id)["groups"])
    openai = groups["openai"]["cost"]
    assert Decimal(openai["known_cost_usd"]) == OPENAI_COST and openai["cost_complete"] is True
    for provider in ("gcp.gemini", "groq", "openrouter"):
        cost = groups[provider]["cost"]
        assert cost["known_cost_usd"] is None and cost["computed_cost_calls"] == 0
        assert cost["cost_complete"] is False
    assert groups["groq"]["cost"]["unknown_cost_by_reason"] == {
        "price_missing": 3,
        "usage_missing": 1,
        "usage_inconsistent": 0,
    }


def test_the_overview_never_carries_a_reference(seeded, api_client):
    r = api_client.get(
        "/api/v1/overview", params={"project": seeded.project_id, **WINDOW}, headers=ADMIN_AUTH
    )
    assert r.status_code == 200 and "reference" not in r.text


# ---------------------------------------------------------------- the reference values
def test_gemini_reference_prices_cached_once_and_thinking_inside_output(seeded, api_client):
    g = by_provider(get(api_client, "/api/v1/models/compare", seeded.project_id)["groups"])
    rlp = g["gcp.gemini"]["reference_list_price"]
    # (1000 · 0.25 + 200 · 0.025 + 300 · 1.50) / 1e6
    assert rlp["reference_usd"] == "0.0007050000" and rlp["available"] is True
    assert rlp["tier"] == "paid_standard" and rlp["catalogue_version"] == cat.VERSION
    assert rlp["label"] == "reference_not_billed" and rlp["currency"] == "USD"
    assert (rlp["calls_priced"], rlp["calls_not_priced"]) == (1, 0)


def test_groq_reference_equals_the_sum_of_its_priced_rows_and_counts_the_rest(seeded, api_client):
    g = by_provider(get(api_client, "/api/v1/models/compare", seeded.project_id)["groups"])
    rlp = g["groq"]["reference_list_price"]
    # Rows 3 and 4 only: (222 · 0.075 + 96 · 0.30) + (244 · 0.075 + 363 · 0.30) = 172.65 / 1e6
    assert rlp["reference_usd"] == "0.0001726500"
    assert (rlp["calls_priced"], rlp["calls_not_priced"]) == (2, 2)
    assert rlp["not_priced_by_reason"] == {
        "usage_missing": 1,
        "usage_inconsistent": 0,
        "cache_write_not_representable": 1,
        "not_in_reference_catalogue": 0,
        "price_not_listed": 0,
    }
    assert rlp["calls_priced"] + rlp["calls_not_priced"] == g["groq"]["attempts"]


@pytest.mark.parametrize("provider", ["openai", "openrouter"])
def test_providers_outside_the_catalogue_have_no_reference(seeded, api_client, provider):
    g = by_provider(get(api_client, "/api/v1/models/compare", seeded.project_id)["groups"])
    rlp = g[provider]["reference_list_price"]
    assert rlp["available"] is False and rlp["reference_usd"] is None
    assert rlp["unavailable_reason"] == "not_in_reference_catalogue"
    assert rlp["not_priced_by_reason"]["not_in_reference_catalogue"] == 1


def test_filters_apply_to_the_reference_as_to_every_other_block(seeded, api_client):
    groq = get(api_client, "/api/v1/models/compare", seeded.project_id, provider="groq")["groups"]
    assert [g["provider"] for g in groq] == ["groq"]
    assert groq[0]["reference_list_price"]["reference_usd"] == "0.0001726500"
    empty = get(api_client, "/api/v1/models/compare", seeded.project_id, provider="anthropic")
    assert empty["groups"] == []
