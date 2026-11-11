"""Phase 8A-5: comparison telemetry beside OpenAI and Gemini, through the real ingest path
(OTLP/HTTP protobuf → /v1/traces → PostgreSQL) and the authenticated read API.

Every span is SYNTHETIC and made by this test; no provider is called. The Groq spans have the
attribute set of `docs/fixtures/groq_client_span.v1.json` with the three comparison scenarios'
prompt identity (`demo_app/comparison_scenarios.py`), each under a `demo.live-compare` root.
**The OpenRouter rows are a synthetic representation only: no OpenRouter adapter exists**, and the
model name says so. Prices for exactly `groq` and `openrouter` are seeded, to show they are never
applied (ADR-40). OpenAI uses the verified catalogue, as in `test_ingest_cost.py`.

| Project | Trace | Provider / model | Prompt version |
|---|---|---|---|
| A | 1 | openai / gpt-5.4-mini → gpt-5.4-mini-2026-03-17 (priced) | v1 |
| A | 2 | gcp.gemini / gemini-3.1-flash-lite | v1 |
| A | 3, 4, 5 | groq / openai/gpt-oss-20b | `cmp-v1.<scenario>`, one per scenario |
| A | 6 | openrouter / synthetic/compat-only:free | cmp-v1.ticket-triage |
| B | 7 | groq / openai/gpt-oss-20b | cmp-v1.ticket-triage |
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import ModelPrice
from app.repositories import pricing as pricing_repo
from app.services import pricing_catalog
from tests.helpers import letters
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

AFTER_CATALOGUE = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)  # the verified OpenAI catalogue
WINDOW = {"from": "2026-10-25T00:00:00Z", "to": "2026-10-26T00:00:00Z"}
SEC = 1_000_000_000
GROQ_MODEL = "openai/gpt-oss-20b"
GEMINI_MODEL = "gemini-3.1-flash-lite"
OPENROUTER_MODEL = "synthetic/compat-only:free"  # SYNTHETIC: no OpenRouter adapter exists
OPENAI_REQUESTED, OPENAI_RESPONSE = "gpt-5.4-mini", "gpt-5.4-mini-2026-03-17"
OPENAI_COST = Decimal("0.0015750000")  # docs/PRICING.md §4 worked example
SCENARIOS = {
    "cmp-ticket-triage": ("cmp.ticket-triage", "cmp-v1.ticket-triage"),
    "cmp-json-aggregate": ("cmp.json-aggregate", "cmp-v1.json-aggregate"),
    "cmp-release-summary": ("cmp.release-summary", "cmp-v1.release-summary"),
}
CMP_VERSIONS = sorted(v for _, v in SCENARIOS.values())
# Keys of docs/fixtures/groq_client_span.v1.json: what the Groq adapter emits for a completion.
GROQ_KEYS = {
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.response.id",
    "gen_ai.response.finish_reasons",
    "gen_ai.request.max_tokens",
    "gen_ai.prompt.name",
    "gen_ai.prompt.version",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "llminsight.call_id",
    "llminsight.attempt",
    "llminsight.provenance",
    "llminsight.usage_source",
    "server.address",
    "server.port",
}
CONTENT_SENTINEL = "COMPARE-CONTENT-SENTINEL-do-not-store-5a1e"
REASONING_SENTINEL = "COMPARE-REASONING-SENTINEL-do-not-store-77c0"


def ns(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * SEC


BASE = ns(AFTER_CATALOGUE)


def tid(i: int) -> str:
    return f"8a5{i:029x}"


def sid(i: int, j: int) -> str:
    return f"{i:08x}{j:08x}"


def client_attrs(i: int, provider: str, model: str, prompt: tuple[str, str], **kw) -> dict:
    host = {"groq": "api.groq.com", "openrouter": "openrouter.ai"}.get(provider)
    attrs = inference_attrs(
        gen_ai__provider__name=provider,
        gen_ai__request__model=model,
        gen_ai__response__model=model,
        gen_ai__response__id=f"chatcmpl-SYNTHETIC-8a5-{uuid.uuid4().hex}",
        gen_ai__response__finish_reasons=["stop"],
        gen_ai__request__max_tokens=1024,
        gen_ai__prompt__name=prompt[0],
        gen_ai__prompt__version=prompt[1],
        gen_ai__usage__input_tokens=20,
        gen_ai__usage__output_tokens=32,
        llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{i:012d}",
        server__address=host,
        server__port=443 if host else None,
    )
    for key, value in kw.items():
        name = key.replace("__", ".")
        if value is None:
            attrs.pop(name, None)
        else:
            attrs[name] = value
    return attrs


def comparison_trace(i: int, provider: str, model: str, scenario: str, **kw) -> list[Span]:
    """A root `demo.live-compare` and one CLIENT span, as the comparison runner exports them."""
    start = BASE + i * SEC
    status = kw.pop("status_code", 0)
    root = span(
        trace_id=tid(i),
        span_id=sid(i, 1),
        name="demo.live-compare",
        start=start,
        end=start + 50 * MS,
    )
    client = span(
        trace_id=tid(i),
        span_id=sid(i, 2),
        parent=sid(i, 1),
        name=f"chat {model}",
        kind=Span.SPAN_KIND_CLIENT,
        start=start + MS,
        end=start + 40 * MS,
        status_code=status,
        attrs=client_attrs(i, provider, model, SCENARIOS[scenario], **kw),
    )
    return [root, client]


def single(i: int, provider: str, requested: str, response: str, version: str, **kw) -> list[Span]:
    start = BASE + i * SEC
    attrs = client_attrs(i, provider, requested, ("demo.other", version), **kw)
    attrs["gen_ai.response.model"] = response
    return [
        span(
            trace_id=tid(i),
            span_id=sid(i, 2),
            name=f"chat {requested}",
            kind=Span.SPAN_KIND_CLIENT,
            start=start,
            end=start + 40 * MS,
            attrs=attrs,
        )
    ]


def seed_unused_price(engine, provider: str, model: str) -> None:
    """A catalogue row for exactly this provider and model; ADR-40 says it never applies."""
    offset = timedelta(seconds=uuid.uuid4().int % 10_000_000)
    effective = datetime(2026, 1, 1, tzinfo=UTC) + offset
    with engine.begin() as c:
        c.execute(
            insert(ModelPrice).values(
                version=f"synthetic-{letters()}",
                provider=provider,
                model=model,
                effective_from=effective,
                input_per_million=Decimal("0.15"),
                output_per_million=Decimal("0.60"),
                source_url="https://example.invalid/synthetic",
                verified_at=effective,
            )
        )


@pytest.fixture(scope="session")
def openai_catalogue(ingest_engine) -> None:
    with Session(ingest_engine) as s, s.begin():
        pricing_repo.seed(s, pricing_catalog.ENTRIES, dry_run=False)  # idempotent


@pytest.fixture
def seeded(api_client, make_key, ingest_engine, openai_catalogue):
    seed_unused_price(ingest_engine, "groq", GROQ_MODEL)
    seed_unused_price(ingest_engine, "openrouter", OPENROUTER_MODEL)
    a, b = make_key("p8a5-a"), make_key("p8a5-b")
    spans = [
        *single(
            1,
            "openai",
            OPENAI_REQUESTED,
            OPENAI_RESPONSE,
            "v1",
            gen_ai__usage__input_tokens=1200,
            gen_ai__usage__output_tokens=300,
            gen_ai__usage__cache_read__input_tokens=1000,
            gen_ai__usage__reasoning__output_tokens=200,
            server__address="api.openai.com",
            openai__response__service_tier="default",  # pricing applies to the standard tier only
        ),
        *single(2, "gcp.gemini", GEMINI_MODEL, GEMINI_MODEL, "v1"),
        *comparison_trace(3, "groq", GROQ_MODEL, "cmp-ticket-triage"),
        *comparison_trace(4, "groq", GROQ_MODEL, "cmp-json-aggregate"),
        *comparison_trace(5, "groq", GROQ_MODEL, "cmp-release-summary"),
        *comparison_trace(6, "openrouter", OPENROUTER_MODEL, "cmp-ticket-triage"),
    ]
    assert post(api_client, body(request(*spans)), a.key).status_code == 200
    other = comparison_trace(7, "groq", GROQ_MODEL, "cmp-ticket-triage")
    assert post(api_client, body(request(*other)), b.key).status_code == 200
    return a, b


def get(client, path, project, **params):
    r = client.get(path, params={"project": project, **WINDOW, **params}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def group_keys(groups) -> list[tuple]:
    return sorted(
        (g["provider"], g["requested_model"], g["response_model"], g["prompt_version"])
        for g in groups
    )


# ---------------------------------------------------------------- A, B, D, L: stored unchanged
def test_each_comparison_span_is_stored_with_its_identity_and_unknown_cost(seeded, db):
    a, _ = seeded
    for i, scenario in (
        (3, "cmp-ticket-triage"),
        (4, "cmp-json-aggregate"),
        (5, "cmp-release-summary"),
    ):
        prompt_name, prompt_version = SCENARIOS[scenario]
        spans = {
            r["kind"]: r
            for r in rows(
                db,
                "SELECT * FROM spans WHERE project_id = :p AND trace_id = :t",
                p=a.project_id,
                t=tid(i),
            )
        }
        assert set(spans) == {"INTERNAL", "CLIENT"}
        root, client = spans["INTERNAL"], spans["CLIENT"]
        assert root["name"] == "demo.live-compare" and root["parent_span_id"] is None
        assert (
            client["parent_span_id"] == root["span_id"] and client["name"] == f"chat {GROQ_MODEL}"
        )
        assert (
            client["server_dropped_attributes_count"]
            == root["server_dropped_attributes_count"]
            == 0
        )
        assert client["attributes_schema_version"] == "llminsight.attrmap.v1"
        assert set(client["attributes"]) == GROQ_KEYS
        assert client["attributes"]["gen_ai.prompt.name"] == prompt_name
        assert client["attributes"]["gen_ai.prompt.version"] == prompt_version
        assert client["attributes"]["gen_ai.provider.name"] == "groq"
        assert client["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
        assert not any(k.startswith("gen_ai.") for k in root["attributes"])

        (trace,) = rows(
            db,
            "SELECT * FROM traces WHERE project_id = :p AND trace_id = :t",
            p=a.project_id,
            t=tid(i),
        )
        assert (trace["prompt_version"], trace["status"], trace["span_count"]) == (
            prompt_version,
            "ok",
            2,
        )

        (usage,) = rows(
            db,
            "SELECT * FROM usage_records WHERE project_id = :p AND trace_id = :t",
            p=a.project_id,
            t=tid(i),
        )
        assert usage["span_id"] == client["span_id"]  # the root never produces usage
        assert (usage["provider"], usage["requested_model"], usage["response_model"]) == (
            "groq",
            GROQ_MODEL,
            GROQ_MODEL,
        )
        assert (usage["input_tokens"], usage["output_tokens"]) == (20, 32)
        assert usage["cost_usd"] is None and usage["model_price_id"] is None
        assert (usage["cost_status"], usage["cost_unknown_reason"]) == ("unknown", "price_missing")

    (openrouter,) = rows(
        db,
        "SELECT * FROM usage_records WHERE project_id = :p AND trace_id = :t",
        p=a.project_id,
        t=tid(6),
    )
    assert (openrouter["cost_status"], openrouter["cost_unknown_reason"]) == (
        "unknown",
        "price_missing",
    )
    assert openrouter["cost_usd"] is None


# ---------------------------------------------------------------- G, J: Models / Compare
def test_compare_keeps_one_row_per_provider_model_and_scenario(seeded, api_client):
    a, _ = seeded
    groups = get(api_client, "/api/v1/models/compare", a.project_id)["groups"]
    assert group_keys(groups) == sorted(
        [
            ("openai", OPENAI_REQUESTED, OPENAI_RESPONSE, "v1"),
            ("gcp.gemini", GEMINI_MODEL, GEMINI_MODEL, "v1"),
            *(("groq", GROQ_MODEL, GROQ_MODEL, v) for v in CMP_VERSIONS),
            ("openrouter", OPENROUTER_MODEL, OPENROUTER_MODEL, "cmp-v1.ticket-triage"),
        ]
    )
    by_key = {(g["provider"], g["prompt_version"]): g for g in groups}
    assert all(g["attempts"] == 1 for g in groups)
    openai = by_key[("openai", "v1")]["cost"]
    assert Decimal(openai["known_cost_usd"]) == OPENAI_COST and openai["cost_complete"] is True
    for key, g in by_key.items():
        if key[0] == "openai":
            continue
        assert g["cost"]["known_cost_usd"] is None and g["cost"]["cost_complete"] is False
        assert g["cost"]["unknown_cost_by_reason"]["price_missing"] == 1

    groq = get(api_client, "/api/v1/models/compare", a.project_id, provider="groq")["groups"]
    assert sorted(g["prompt_version"] for g in groq) == CMP_VERSIONS
    one = get(
        api_client, "/api/v1/models/compare", a.project_id, prompt_version="cmp-v1.ticket-triage"
    )
    assert sorted(g["provider"] for g in one["groups"]) == ["groq", "openrouter"]
    assert one["filters"]["prompt_version"] == "cmp-v1.ticket-triage"


# ---------------------------------------------------------------- E: Overview
def test_overview_counts_tokens_and_cost_completeness(seeded, api_client):
    a, _ = seeded
    o = get(api_client, "/api/v1/overview", a.project_id)
    inference = o["inference"]
    assert inference["attempts"]["total"] == 6 and o["traces"]["count"]["total"] == 6
    tokens = inference["tokens"]["total"]
    assert (tokens["input_tokens"], tokens["output_tokens"]) == (1200 + 5 * 20, 300 + 5 * 32)
    assert tokens["subsets"]["cached_input_tokens"] == {"sum": 1000, "calls_reporting": 1}
    assert tokens["subsets"]["reasoning_output_tokens"] == {"sum": 200, "calls_reporting": 1}
    cost = inference["cost"]["total"]
    assert Decimal(cost["known_cost_usd"]) == OPENAI_COST
    assert (cost["computed_cost_calls"], cost["unknown_cost_calls"]) == (1, 5)
    assert cost["unknown_cost_by_reason"]["price_missing"] == 5
    assert cost["cost_complete"] is False  # never complete while any call's cost is unknown

    groq = get(api_client, "/api/v1/overview", a.project_id, provider="groq")
    g = groq["inference"]
    assert g["attempts"]["total"] == 3 and groq["traces"]["count"]["total"] == 3
    assert (g["tokens"]["total"]["input_tokens"], g["tokens"]["total"]["output_tokens"]) == (60, 96)
    assert g["cost"]["total"]["known_cost_usd"] is None
    assert g["cost"]["total"]["cost_complete"] is False
    assert all(p["provider"] != "groq" for p in g["price_versions"]["items"])

    ticket = get(
        api_client, "/api/v1/overview", a.project_id, prompt_version="cmp-v1.ticket-triage"
    )
    assert ticket["inference"]["attempts"]["total"] == 2  # groq and the synthetic openrouter row

    openai = get(api_client, "/api/v1/overview", a.project_id, provider="openai")
    assert openai["inference"]["cost"]["total"]["cost_complete"] is True


# ---------------------------------------------------------------- F: Traces list and detail
def test_trace_list_and_detail_keep_provider_model_status_and_relationships(seeded, api_client):
    a, _ = seeded
    listing = get(api_client, "/api/v1/traces", a.project_id, provider="groq")
    assert [i["trace_id"] for i in listing["items"]] == [tid(5), tid(4), tid(3)]
    by_id = {i["trace_id"]: i for i in listing["items"]}
    assert by_id[tid(3)]["prompt_version"] == "cmp-v1.ticket-triage"
    assert by_id[tid(4)]["prompt_version"] == "cmp-v1.json-aggregate"
    for item in listing["items"]:
        assert item["status"] == "ok" and item["span_count"] == 2
        assert item["usage"]["models"]["items"] == [
            {
                "provider": "groq",
                "requested_model": GROQ_MODEL,
                "response_model": GROQ_MODEL,
                "calls": 1,
            }
        ]
        assert item["usage"]["known_cost_usd"] is None and item["usage"]["unknown_cost_calls"] == 1
    assert [
        i["trace_id"]
        for i in get(api_client, "/api/v1/traces", a.project_id, provider="openrouter")["items"]
    ] == [tid(6)]
    assert [
        i["trace_id"]
        for i in get(api_client, "/api/v1/traces", a.project_id, provider="gcp.gemini")["items"]
    ] == [tid(2)]
    model = get(api_client, "/api/v1/traces", a.project_id, provider="groq", model=GROQ_MODEL)
    assert len(model["items"]) == 3

    detail = api_client.get(
        f"/api/v1/traces/{tid(4)}", params={"project": a.project_id}, headers=ADMIN_AUTH
    ).json()
    assert detail["trace"]["prompt_version"] == "cmp-v1.json-aggregate"
    spans = {s["span_id"]: s for s in detail["spans"]}
    root, client = spans[sid(4, 1)], spans[sid(4, 2)]
    assert root["role"] == "root" and root["children"] == [sid(4, 2)]
    assert client["parent_span_id"] == sid(4, 1) and client["kind"] == "CLIENT"
    assert client["status"] == "UNSET" and client["error_type"] is None
    assert client["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    assert client["attributes"]["gen_ai.prompt.version"] == "cmp-v1.json-aggregate"
    assert client["server_dropped_attributes_count"] == 0
    (usage,) = detail["usage"]
    assert (usage["provider"], usage["response_model"]) == ("groq", GROQ_MODEL)
    assert (usage["cost_usd"], usage["cost_status"], usage["cost_unknown_reason"]) == (
        None,
        "unknown",
        "price_missing",
    )


# ---------------------------------------------------------------- H: project isolation
def test_no_view_of_one_project_shows_another_projects_trace(seeded, api_client):
    a, b = seeded
    assert tid(7) not in {
        i["trace_id"] for i in get(api_client, "/api/v1/traces", a.project_id)["items"]
    }
    assert [i["trace_id"] for i in get(api_client, "/api/v1/traces", b.project_id)["items"]] == [
        tid(7)
    ]
    foreign = api_client.get(
        f"/api/v1/traces/{tid(7)}", params={"project": a.project_id}, headers=ADMIN_AUTH
    )
    missing = api_client.get(
        f"/api/v1/traces/{uuid.uuid4().hex}", params={"project": a.project_id}, headers=ADMIN_AUTH
    )
    assert foreign.status_code == missing.status_code == 404 and foreign.text == missing.text
    overview_b = get(api_client, "/api/v1/overview", b.project_id)
    assert overview_b["inference"]["attempts"]["total"] == 1
    assert group_keys(get(api_client, "/api/v1/models/compare", b.project_id)["groups"]) == [
        ("groq", GROQ_MODEL, GROQ_MODEL, "cmp-v1.ticket-triage")
    ]
    ticket_a = get(
        api_client,
        "/api/v1/models/compare",
        a.project_id,
        provider="groq",
        prompt_version="cmp-v1.ticket-triage",
    )
    (group,) = ticket_a["groups"]
    assert group["attempts"] == 1  # project B's call with the same identity is not counted


# ---------------------------------------------------------------- C: the same export again
def test_a_resent_comparison_export_adds_nothing(api_client, db, make_key):
    k = make_key("p8a5-resend")
    payload = body(request(*comparison_trace(8, "groq", GROQ_MODEL, "cmp-json-aggregate")))
    for _ in range(3):
        assert post(api_client, payload, k.key).status_code == 200
    counts = rows(
        db,
        "SELECT (SELECT count(*) FROM traces WHERE project_id = :p) AS t,"
        " (SELECT count(*) FROM spans WHERE project_id = :p) AS s,"
        " (SELECT count(*) FROM usage_records WHERE project_id = :p) AS u",
        p=k.project_id,
    )
    assert counts == [{"t": 1, "s": 2, "u": 1}]
    o = get(api_client, "/api/v1/overview", k.project_id)
    assert o["inference"]["attempts"]["total"] == 1
    assert o["inference"]["tokens"]["total"]["input_tokens"] == 20


# ---------------------------------------------------------------- I: incomplete data fails closed
def test_incomplete_or_invalid_provider_data_manufactures_nothing(api_client, db, make_key):
    k = make_key("p8a5-incomplete")
    spans = [
        # 9: no usage reported at all
        *comparison_trace(
            9,
            "groq",
            GROQ_MODEL,
            "cmp-ticket-triage",
            gen_ai__usage__input_tokens=None,
            gen_ai__usage__output_tokens=None,
            llminsight__usage_source="unknown",
        ),
        # 10: a negative count is invalid: dropped, counted, and the usage is unknown
        *comparison_trace(
            10, "groq", GROQ_MODEL, "cmp-json-aggregate", gen_ai__usage__input_tokens=-5
        ),
        # 11: a provider error with no response model and no usage
        *comparison_trace(
            11,
            "groq",
            GROQ_MODEL,
            "cmp-release-summary",
            status_code=2,
            error__type="429",
            gen_ai__response__model=None,
            gen_ai__response__id=None,
            gen_ai__response__finish_reasons=None,
            gen_ai__usage__input_tokens=None,
            gen_ai__usage__output_tokens=None,
            llminsight__usage_source="unknown",
        ),
    ]
    assert post(api_client, body(request(*spans)), k.key).status_code == 200

    def usage(i: int) -> dict:
        (row,) = rows(
            db,
            "SELECT * FROM usage_records WHERE project_id = :p AND trace_id = :t",
            p=k.project_id,
            t=tid(i),
        )
        return row

    def client_span(i: int) -> dict:
        (row,) = rows(
            db,
            "SELECT * FROM spans WHERE project_id = :p AND trace_id = :t AND kind = 'CLIENT'",
            p=k.project_id,
            t=tid(i),
        )
        return row

    for i in (9, 10, 11):
        u = usage(i)
        assert u["input_tokens"] is None and u["usage_source"] == "unknown"
        assert u["cost_usd"] is None
        assert (u["cost_status"], u["cost_unknown_reason"]) == ("unknown", "usage_missing")
    assert usage(10)["output_tokens"] == 32  # a valid count is kept, never totalled with a gap
    assert client_span(9)["server_dropped_attributes_count"] == 0
    assert client_span(10)["server_dropped_attributes_count"] == 1
    assert "gen_ai.usage.input_tokens" not in client_span(10)["attributes"]
    failed = client_span(11)
    assert (failed["status"], failed["error_type"]) == ("ERROR", "429")
    assert usage(11)["response_model"] is None

    o = get(api_client, "/api/v1/overview", k.project_id)
    tokens = o["inference"]["tokens"]["total"]
    assert tokens["input_tokens"] is None  # no call reported a valid input count: unknown, not 0
    assert tokens["calls_with_unknown_input_tokens"] == 3
    assert o["inference"]["errors"]["total"]["failed"] == 1
    cost = o["inference"]["cost"]["total"]
    assert cost["known_cost_usd"] is None and cost["computed_cost_calls"] == 0
    assert cost["unknown_cost_by_reason"]["usage_missing"] == 3 and cost["cost_complete"] is False
    groups = get(api_client, "/api/v1/models/compare", k.project_id)["groups"]
    failed_group = [g for g in groups if g["response_model"] is None]
    assert len(failed_group) == 1 and failed_group[0]["errors"]["failed"] == 1  # its own honest row
    statuses = {
        i["trace_id"]: i["status"] for i in get(api_client, "/api/v1/traces", k.project_id)["items"]
    }
    assert statuses[tid(11)] == "error"


# ---------------------------------------------------------------- K: no content, no credential
def test_content_reasoning_and_credentials_never_reach_storage_or_the_api(api_client, db, make_key):
    k = make_key("p8a5-content")
    groq_key = "gsk" + "_" + "Q7xCOMPARE" * 4
    spans = comparison_trace(
        12,
        "groq",
        GROQ_MODEL,
        "cmp-ticket-triage",
        gen_ai__input__messages=CONTENT_SENTINEL,
        gen_ai__output__messages=CONTENT_SENTINEL,
        llm__reasoning=REASONING_SENTINEL,
        gen_ai__response__id=groq_key,
    )
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    (client,) = rows(
        db,
        "SELECT * FROM spans WHERE project_id = :p AND kind = 'CLIENT'",
        p=k.project_id,
    )
    assert client["server_dropped_attributes_count"] == 4  # three content keys and the key value
    assert set(client["attributes"]) == GROQ_KEYS - {"gen_ai.response.id"}
    responses = [
        api_client.get(path, params=params, headers=ADMIN_AUTH).text
        for path, params in (
            ("/api/v1/traces", {"project": k.project_id, **WINDOW}),
            (f"/api/v1/traces/{tid(12)}", {"project": k.project_id}),
            ("/api/v1/overview", {"project": k.project_id, **WINDOW}),
            ("/api/v1/models/compare", {"project": k.project_id, **WINDOW}),
        )
    ]
    dump = "\n".join(
        r["j"]
        for table in ("traces", "spans", "usage_records")
        for r in rows(db, f"SELECT row_to_json(x)::text AS j FROM {table} AS x")  # noqa: S608
    )
    for text in (dump, *responses):
        for secret in (CONTENT_SENTINEL, REASONING_SENTINEL, groq_key, groq_key[-12:], k.key):
            assert secret not in text
