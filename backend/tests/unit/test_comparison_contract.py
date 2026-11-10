"""Phase 8A-4: the comparison runner's span, on the backend's real ingest and pricing rules.

`demo_app.live_compare` emits the Groq adapter's CLIENT span (the attribute keys of
`docs/fixtures/groq_client_span.v1.json`, asserted by the SDK runner test) with the scenario's
prompt identity and `real` provenance. These tests show that the backend keeps that identity
under the unchanged `llminsight.attrmap.v1`, never prices the call, and that the three scenarios
form three distinct `/models/compare` groups, apart from the Gemini demo's `v1`. Offline: no
database, no network.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.ingestion import attrmap
from app.services.ingestion import usage as usage_ledger
from tests.unit.test_groq_contract import ATTRIBUTES, MODEL, normalized
from tests.unit.test_pricing import _synthetic

# The three scenarios of demo_app/comparison_scenarios.py (pinned there and in the SDK tests).
SCENARIOS = {
    "cmp-ticket-triage": ("cmp.ticket-triage", "cmp-v1.ticket-triage"),
    "cmp-json-aggregate": ("cmp.json-aggregate", "cmp-v1.json-aggregate"),
    "cmp-release-summary": ("cmp.release-summary", "cmp-v1.release-summary"),
}
GEMINI_DEMO_PROMPT_VERSION = "v1"


def comparison_attributes(prompt_name: str, prompt_version: str) -> dict:
    return {
        **ATTRIBUTES,
        "gen_ai.prompt.name": prompt_name,
        "gen_ai.prompt.version": prompt_version,
        "gen_ai.request.max_tokens": 1024,
        "llminsight.provenance": "real",
    }


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_prompt_identity_and_provenance_are_kept_unchanged(scenario):
    name, version = SCENARIOS[scenario]
    stored = normalized(comparison_attributes(name, version))
    assert attrmap.SCHEMA_VERSION == "llminsight.attrmap.v1"
    assert stored.server_dropped_attributes_count == 0
    assert stored.attributes["gen_ai.prompt.name"] == name
    assert stored.attributes["gen_ai.prompt.version"] == version
    assert stored.provenance == "real"


@pytest.mark.parametrize("rate", [Decimal("0.15"), Decimal("0")], ids=["listed", "zero"])
def test_a_comparison_call_is_never_priced(rate):
    stored = normalized(comparison_attributes(*SCENARIOS["cmp-ticket-triage"]))
    row = usage_ledger.usage_row(1, stored)
    price = _synthetic(
        provider="groq",
        model=MODEL,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        input_per_million=rate,
        output_per_million=rate,
    )
    usage_ledger.price_rows([row], {(row["trace_id"], row["span_id"]): stored}, [price])
    assert row["cost_usd"] is None
    assert (row["cost_status"], row["cost_unknown_reason"]) == ("unknown", "price_missing")
    assert row["provenance"] == "real" and row["usage_source"] == "provider_reported"


def test_each_scenario_is_its_own_compare_group_and_none_merges_with_the_gemini_demo():
    """`/models/compare` groups by provider, requested model, response model and the span's
    `gen_ai.prompt.version` (`repositories/metrics.py`)."""
    keys = set()
    for name, version in SCENARIOS.values():
        stored = normalized(comparison_attributes(name, version))
        row = usage_ledger.usage_row(1, stored)
        keys.add(
            (
                row["provider"],
                row["requested_model"],
                row["response_model"],
                stored.attributes["gen_ai.prompt.version"],
            )
        )
    assert len(keys) == 3
    assert all(key[0] == "groq" and key[1] == key[2] == MODEL for key in keys)
    assert GEMINI_DEMO_PROMPT_VERSION not in {key[3] for key in keys}
