"""Phase 8A-2: the span the SDK's Groq adapter emits, on the backend's real ingest rules.

`docs/fixtures/groq_client_span.v1.json` is the shared contract. The SDK test asserts it emits
exactly these attributes; these tests assert that the backend keeps every one of them under
`llminsight.attrmap.v1`, with no schema change, and never prices the call. SYNTHETIC only.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from app.services.ingestion import attrmap
from app.services.ingestion import usage as usage_ledger
from app.services.ingestion.normalize import normalize
from tests.helpers import REPO_ROOT
from tests.otlp import T0, request, span
from tests.unit.test_pricing import _synthetic

FIXTURE = json.loads(
    (REPO_ROOT / "docs" / "fixtures" / "groq_client_span.v1.json").read_text(encoding="utf-8")
)
ATTRIBUTES = FIXTURE["attributes"]
MODEL = ATTRIBUTES["gen_ai.request.model"]


def normalized(attrs=None, name=None, status_code=0):
    spans, _ = normalize(
        request(
            span(
                name=name or FIXTURE["span_name"],
                kind=Span.SPAN_KIND_CLIENT,
                attrs=ATTRIBUTES if attrs is None else attrs,
                status_code=status_code,
            )
        )
    )
    return spans[0]


def test_the_fixture_is_labelled_synthetic():
    assert FIXTURE["synthetic"] is True
    assert ATTRIBUTES["llminsight.provenance"] == "synthetic"
    assert FIXTURE["span_kind"] == "CLIENT"


def test_every_groq_attribute_is_kept_under_the_unchanged_mapping():
    stored = normalized()
    assert stored.server_dropped_attributes_count == 0
    assert stored.name == FIXTURE["span_name"]
    assert stored.provenance == "synthetic"
    assert attrmap.SCHEMA_VERSION == "llminsight.attrmap.v1"
    for key, value in ATTRIBUTES.items():
        assert stored.attributes[key] == value, key


def test_the_usage_row_carries_provider_models_tokens_and_identity():
    row = usage_ledger.usage_row(1, normalized())
    assert (row["provider"], row["requested_model"], row["response_model"]) == (
        "groq",
        MODEL,
        MODEL,
    )
    assert (row["input_tokens"], row["output_tokens"]) == (20, 32)
    assert row["cached_input_tokens"] is None and row["reasoning_output_tokens"] is None
    assert row["usage_source"] == "provider_reported"
    assert row["attempt_id"] == 1 and row["operation_name"] == "chat"
    assert row["canonical_call_key"] == "resp:groq:chatcmpl-SYNTHETIC-groq-0001"
    assert row["call_key_source"] == "provider_response_id"


@pytest.mark.parametrize("rate", [Decimal("0.15"), Decimal("0")], ids=["listed", "zero"])
def test_a_matching_catalogue_row_never_prices_the_groq_span(rate):
    stored = normalized()
    row = usage_ledger.usage_row(1, stored)
    price = _synthetic(
        provider="groq",
        model=MODEL,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        input_per_million=rate,
        output_per_million=rate,
    )
    assert usage_ledger.price_model_keys([row]) == {("groq", MODEL)}
    usage_ledger.price_rows([row], {(row["trace_id"], row["span_id"]): stored}, [price])
    assert row["cost_usd"] is None
    assert (row["cost_status"], row["cost_unknown_reason"]) == ("unknown", "price_missing")
    assert row["price_version"] is None and row["model_price_id"] is None


@pytest.mark.parametrize(
    "error_type",
    [
        "400",
        "401",
        "403",
        "404",
        "429",
        "500",
        "timeout",
        "cancelled",
        "KeyboardInterrupt",
        "httpx.ConnectError",
        "httpx.RemoteProtocolError",
        "groq.response.malformed",
        "groq.response.oversized",
        "groq.response.error",
        "groq.response.empty",
        "groq.response.failed",
        "groq.response.unexpected_finish",
        "groq.response.model_mismatch",
    ],
)
def test_every_error_type_the_adapter_emits_is_accepted(error_type):
    attrs = {
        k: v for k, v in ATTRIBUTES.items() if not k.startswith(("gen_ai.usage", "gen_ai.response"))
    }
    attrs["llminsight.usage_source"] = "unknown"
    attrs["error.type"] = error_type
    stored = normalized(attrs, status_code=2)
    assert stored.server_dropped_attributes_count == 0
    assert stored.attributes["error.type"] == error_type
    row = usage_ledger.usage_row(1, stored)
    assert row["input_tokens"] is None and row["output_tokens"] is None
    assert row["cost_usd"] is None and row["usage_source"] == "unknown"


@pytest.mark.parametrize("finish", ["stop", "length", "content_filter", "error", "tool_calls"])
def test_every_finish_reason_is_kept_verbatim(finish):
    attrs = {**ATTRIBUTES, "gen_ai.response.finish_reasons": [finish]}
    stored = normalized(attrs)
    assert stored.server_dropped_attributes_count == 0
    assert stored.attributes["gen_ai.response.finish_reasons"] == [finish]


def test_the_fixture_start_time_is_outside_no_window():
    """`T0` is the same instant the other ingest tests use, so a usage row is well formed."""
    assert usage_ledger.usage_row(1, normalized())["started_at"] == usage_ledger.ns_to_datetime(T0)
