"""Phase 8A-1 on the real ingest path: OTLP/HTTP protobuf → /v1/traces → PostgreSQL → read API.

ADR-40: a catalogue row for exactly a free-plan provider and model never prices the call.
ADR-41: a Groq-shaped key never reaches the database, the read API or the logs.

All telemetry and prices are SYNTHETIC, and keys are assembled at runtime. No provider exists
here: these spans are made by the test. Prices use a fresh model per test because
`model_prices` is append-only and shared by the ingest database.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from sqlalchemy import insert

from app.models import ModelPrice
from tests.helpers import letters
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import body, inference_attrs, request, span

pytestmark = pytest.mark.integration

WINDOW = {"from": "2026-10-24T00:00:00Z", "to": "2026-10-25T00:00:00Z"}
FROM = datetime(2026, 1, 1, tzinfo=UTC)
SPAN_ID = "7" * 16


def seed_price(engine, provider: str, model: str, rate: Decimal) -> None:
    with engine.begin() as c:
        c.execute(
            insert(ModelPrice).values(
                version=f"synthetic-{letters()}",
                provider=provider,
                model=model,
                effective_from=FROM,
                input_per_million=rate,
                output_per_million=rate,
                source_url="https://example.invalid/synthetic",
                verified_at=FROM,
            )
        )


@pytest.mark.parametrize(
    ("provider", "suffix", "host"),
    [("groq", "", "api.groq.com"), ("openrouter", ":free", "openrouter.ai")],
)
@pytest.mark.parametrize("rate", [Decimal("0.15"), Decimal("0")], ids=["listed", "zero"])
def test_a_seeded_price_never_prices_a_free_plan_call(
    api_client, db, ingest_engine, make_key, provider, suffix, host, rate
):
    model = f"synthetic-{letters()}{suffix}"
    seed_price(ingest_engine, provider, model, rate)
    k = make_key()
    attrs = inference_attrs(
        gen_ai__provider__name=provider,
        gen_ai__request__model=model,
        gen_ai__response__model=model,
        gen_ai__response__id=f"synthetic-{uuid.uuid4().hex}",
        server__address=host,
    )
    llm = span(span_id=SPAN_ID, name=f"chat {model}", kind=Span.SPAN_KIND_CLIENT, attrs=attrs)
    assert post(api_client, body(request(llm)), k.key).status_code == 200

    (usage,) = rows(db, "SELECT * FROM usage_records WHERE span_id = :s", s=SPAN_ID)
    assert usage["cost_usd"] is None
    assert (usage["cost_status"], usage["cost_unknown_reason"]) == ("unknown", "price_missing")
    assert usage["price_version"] is None and usage["model_price_id"] is None

    compare = api_client.get(
        "/api/v1/models/compare",
        params={"project": k.project_id, "provider": provider, **WINDOW},
        headers=ADMIN_AUTH,
    )
    assert compare.status_code == 200
    (group,) = compare.json()["groups"]
    assert group["cost"]["known_cost_usd"] is None
    assert group["cost"]["unknown_cost_calls"] == 1
    assert group["cost"]["cost_complete"] is False


def test_a_groq_key_never_reaches_the_database_the_api_or_the_logs(
    api_client, db, make_key, caplog
):
    caplog.set_level(logging.DEBUG)
    key = "gsk" + "_" + "Q7xINTEGRATION" * 4
    k = make_key()
    attrs = inference_attrs(
        gen_ai__provider__name="groq",
        gen_ai__request__model=f"ft:gpt-oss:acme:{key}",
        gen_ai__response__model=key,
        gen_ai__response__id=key,
    )
    leaked = span(span_id=SPAN_ID, name=f"chat {key}", kind=Span.SPAN_KIND_CLIENT, attrs=attrs)
    response = post(api_client, body(request(leaked)), k.key)
    assert response.status_code == 200

    (stored,) = rows(db, "SELECT * FROM spans WHERE span_id = :s", s=SPAN_ID)
    assert stored["name"] == "redacted_span"
    assert stored["attributes_schema_version"] == "llminsight.attrmap.v1"
    assert stored["server_dropped_attributes_count"] == 4  # three attributes and the name

    listing = api_client.get(
        "/api/v1/traces", params={"project": k.project_id, **WINDOW}, headers=ADMIN_AUTH
    )
    assert listing.status_code == 200
    detail = api_client.get(
        f"/api/v1/traces/{stored['trace_id']}",
        params={"project": k.project_id},
        headers=ADMIN_AUTH,
    )
    assert detail.status_code == 200

    dump = "\n".join(
        r["j"]
        for table in ("traces", "spans", "usage_records")
        for r in rows(db, f"SELECT row_to_json(x)::text AS j FROM {table} AS x")  # noqa: S608
    )
    for text in (dump, listing.text, detail.text, caplog.text, response.text):
        assert key not in text
        assert key[-12:] not in text
