"""Phase 8A-6: the dashboard's SYNTHETIC comparison fixture (`scripts/dev/seed_trace_fixtures.py
--set phase8a6`) through the real ingest rules, offline:

    seeder → OTLP protobuf bytes → `normalize` → usage ledger → cost engine (verified catalogue)

No database, no network and no provider. The browser test (`frontend/tests/e2e/phase8a6.spec.ts`)
pins the values this fixture must produce in the dashboard. This test shows the backend's own
rules produce them, so a wrong expectation fails here, locally and in the CI `python` job, and
not only in the browser run. The scenario identities are checked against the Phase 8A-4
registry (`demo_app/comparison_scenarios.py`), read as source: the backend never imports the
demo app or the SDK (ADR-01)."""

import ast
import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.services import pricing, pricing_catalog
from app.services.ingestion import usage as usage_ledger
from app.services.ingestion.normalize import NormalizedSpan, normalize

ROOT = Path(__file__).resolve().parents[3]
SEEDER = ROOT / "scripts" / "dev" / "seed_trace_fixtures.py"
REGISTRY = ROOT / "demo_app" / "comparison_scenarios.py"
GROQ_FIXTURE = ROOT / "docs" / "fixtures" / "groq_client_span.v1.json"
ANCHOR = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # after the catalogue's effective_from
GROQ_MODEL = "openai/gpt-oss-20b"
OPENROUTER_MODEL = "synthetic/compat-only:free"
OPENAI_COST = Decimal("0.0015750000")  # docs/PRICING.md §4 worked example
XSS = "window.__llmiXss=1"
ADVERSARIAL = f'<img src=x onerror="{XSS}">cmp8a6.adversarial'
SERVICES = {
    "cmp8a6.openai",
    "cmp8a6.gemini",
    "cmp8a6.groq.ticket-triage",
    "cmp8a6.groq.json-aggregate",
    "cmp8a6.groq.release-summary",
    "cmp8a6.openrouter-synthetic",
    "cmp8a6.refused-root-only",
    ADVERSARIAL,
}
SCENARIOS = {  # service → (prompt name, prompt version, (input, output) tokens)
    "cmp8a6.groq.ticket-triage": ("cmp.ticket-triage", "cmp-v1.ticket-triage", (20, 32)),
    "cmp8a6.groq.json-aggregate": ("cmp.json-aggregate", "cmp-v1.json-aggregate", (24, 40)),
    "cmp8a6.groq.release-summary": ("cmp.release-summary", "cmp-v1.release-summary", (30, 64)),
}
DROPPED = {
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.id",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "llm.reasoning",
}


def _seeder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_trace_fixtures_8a6", SEEDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeder() -> ModuleType:
    return _seeder()


@pytest.fixture(scope="module")
def wire(seeder) -> bytes:
    anchor_ns = int((ANCHOR - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 1_000_000_000
    b = seeder.Builder("phase8a6-unit", anchor_ns)
    seeder.phase8a6_set(b)
    return b.request().SerializeToString()


@pytest.fixture(scope="module")
def spans(wire) -> list[NormalizedSpan]:
    out, violations = normalize(ExportTraceServiceRequest.FromString(wire))
    assert violations == []
    return out


def by_service(spans: list[NormalizedSpan]) -> dict[str, list[NormalizedSpan]]:
    out: dict[str, list[NormalizedSpan]] = {}
    for s in spans:
        out.setdefault(s.resource_attributes["service.name"], []).append(s)
    return out


def priced_rows(spans: list[NormalizedSpan], prices: list[pricing.Price]) -> list[dict]:
    rows = [r for s in spans if (r := usage_ledger.usage_row(1, s)) is not None]
    usage_ledger.price_rows(rows, {(s.trace_id, s.span_id): s for s in spans}, prices)
    return rows


def catalogue() -> list[pricing.Price]:
    return [
        pricing.Price(
            id=i,
            version=e.version,
            provider=e.provider,
            model=e.model,
            effective_from=e.effective_from,
            input_per_million=e.input_per_million,
            output_per_million=e.output_per_million,
            cached_input_per_million=e.cached_input_per_million,
            cache_write_input_per_million=e.cache_write_input_per_million,
        )
        for i, e in enumerate(pricing_catalog.ENTRIES, 1)
    ]


def test_the_set_is_eight_synthetic_traces_that_ingest_without_violation(spans):
    traces = by_service(spans)
    assert set(traces) == SERVICES
    assert len({s.trace_id for s in spans}) == 8 and len(spans) == 15
    assert all(len({s.trace_id for s in group}) == 1 for group in traces.values())
    inference = [s for s in spans if s.is_inference]
    assert len(inference) == 7
    assert {s.provenance for s in inference} == {"synthetic"}  # nothing claims or becomes real
    assert {s.provenance for s in spans if not s.is_inference} == {"unknown"}
    assert all(s.status == "UNSET" for s in spans)


def test_the_scenario_identities_are_the_phase_8a4_registry(seeder):
    registry = {
        (kw["prompt_name"], kw["prompt_version"])
        for node in ast.walk(ast.parse(REGISTRY.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and {"prompt_name", "prompt_version"}
        <= (kw := {k.arg: getattr(k.value, "value", None) for k in node.keywords}).keys()
    }
    pinned = {(name, version) for _, name, version, _ in seeder.COMPARISON_SCENARIOS}
    assert pinned == registry == {(n, v) for n, v, _ in SCENARIOS.values()}
    assert seeder.GROQ_MODEL == GROQ_MODEL and seeder.OPENROUTER_MODEL == OPENROUTER_MODEL


def test_each_groq_scenario_is_the_adapter_span_under_a_live_compare_root(spans):
    adapter_keys = set(json.loads(GROQ_FIXTURE.read_text(encoding="utf-8"))["attributes"])
    traces = by_service(spans)
    for service, (name, version, (tokens_in, tokens_out)) in SCENARIOS.items():
        (root,) = [s for s in traces[service] if s.parent_span_id is None]
        (client,) = [s for s in traces[service] if s.kind == "CLIENT"]
        assert (root.name, root.kind, root.attributes) == ("demo.live-compare", "INTERNAL", {})
        assert client.parent_span_id == root.span_id and client.name == f"chat {GROQ_MODEL}"
        assert set(client.attributes) == adapter_keys  # the exact adapter attribute set
        assert client.server_dropped_attributes_count == root.server_dropped_attributes_count == 0
        a = client.attributes
        assert (a["gen_ai.prompt.name"], a["gen_ai.prompt.version"]) == (name, version)
        assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (
            tokens_in,
            tokens_out,
        )
        assert (a["gen_ai.provider.name"], a["gen_ai.request.max_tokens"]) == ("groq", 1024)
        assert a["gen_ai.response.finish_reasons"] == ["stop"]
        assert (a["server.address"], a["server.port"]) == ("api.groq.com", 443)


def test_usage_and_cost_follow_the_ledger_and_pricing_rules(spans):
    rows = {r["span_id"]: r for r in priced_rows(spans, catalogue())}
    traces = by_service(spans)

    def row(service: str) -> dict:
        (r,) = [rows[s.span_id] for s in traces[service] if s.span_id in rows]
        return r

    openai = row("cmp8a6.openai")
    assert (openai["cost_status"], openai["cost_usd"]) == ("computed", OPENAI_COST)
    assert openai["price_version"] == pricing_catalog.VERSION
    for service in ("cmp8a6.gemini", *SCENARIOS, "cmp8a6.openrouter-synthetic"):
        r = row(service)
        assert (r["cost_status"], r["cost_unknown_reason"], r["cost_usd"]) == (
            "unknown",
            "price_missing",
            None,
        ), service
    adversarial = row(ADVERSARIAL)
    assert (adversarial["provider"], adversarial["requested_model"]) == ("unknown", None)
    assert (adversarial["input_tokens"], adversarial["usage_source"]) == (None, "unknown")
    assert adversarial["cost_unknown_reason"] == "usage_missing"
    assert [s for s in traces["cmp8a6.refused-root-only"] if s.span_id in rows] == []

    assert len(rows) == 7
    known_in = [r["input_tokens"] for r in rows.values() if r["input_tokens"] is not None]
    known_out = [r["output_tokens"] for r in rows.values() if r["output_tokens"] is not None]
    assert (sum(known_in), sum(known_out), 7 - len(known_in)) == (1312, 474, 1)
    reasons = sorted(r["cost_unknown_reason"] for r in rows.values() if r["cost_usd"] is None)
    assert reasons == ["price_missing"] * 5 + ["usage_missing"]


def test_a_catalogue_row_for_groq_or_openrouter_never_prices_the_set(spans):
    """ADR-40, even if a matching price existed: Groq and OpenRouter stay `price_missing`."""
    stored = [
        pricing.Price(
            id=100 + i,
            version="synthetic-8a6",
            provider=provider,
            model=model,
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            input_per_million=Decimal("0.15"),
            output_per_million=Decimal("0.60"),
            cached_input_per_million=None,
            cache_write_input_per_million=None,
        )
        for i, (provider, model) in enumerate(
            (("groq", GROQ_MODEL), ("openrouter", OPENROUTER_MODEL))
        )
    ]
    for r in priced_rows(spans, catalogue() + stored):
        if r["provider"] in ("groq", "openrouter"):
            assert (r["cost_usd"], r["cost_unknown_reason"]) == (None, "price_missing")


def test_compare_keys_keep_seven_groups_and_three_groq_scenarios(spans):
    rows = priced_rows(spans, catalogue())
    pv = {s.span_id: s.attributes.get("gen_ai.prompt.version") for s in spans}
    keys = {
        (r["provider"], r["requested_model"], r["response_model"], pv[r["span_id"]]) for r in rows
    }
    assert len(keys) == 7
    assert {k for k in keys if k[0] == "groq"} == {
        ("groq", GROQ_MODEL, GROQ_MODEL, version) for _, version, _ in SCENARIOS.values()
    }
    assert ("openrouter", OPENROUTER_MODEL, OPENROUTER_MODEL, "cmp-v1.ticket-triage") in keys
    assert ("openai", "gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "v1") in keys
    assert ("unknown", None, None, None) in keys


def test_the_adversarial_span_drops_six_values_and_keeps_its_labels_as_text(seeder, wire, spans):
    (root, client) = sorted(by_service(spans)[ADVERSARIAL], key=lambda s: s.parent_span_id or "")
    assert root.name == f"<script>{XSS}</script>"
    assert client.name == f"chat <img src=x onerror={XSS}>"
    assert client.server_dropped_attributes_count == len(DROPPED)
    assert DROPPED.isdisjoint(client.attributes)
    assert client.is_inference and client.provenance == "synthetic"
    # The values were really sent (so the drop is tested), and none survives normalization.
    key = "gsk" + "_" + "Q7xE2EDASH" * 4
    never = (seeder.CONTENT_SENTINEL, seeder.REASONING_SENTINEL, key)
    assert all(v.encode() in wire for v in never)
    stored = json.dumps(
        [[s.name, s.attributes, s.resource_attributes] for s in spans], ensure_ascii=False
    )
    assert not any(v in stored for v in never)


def test_the_root_only_trace_has_no_inference_and_no_usage(spans):
    (root,) = by_service(spans)["cmp8a6.refused-root-only"]
    assert (root.name, root.kind, root.parent_span_id, root.attributes) == (
        "demo.live-compare",
        "INTERNAL",
        None,
        {},
    )
    assert not root.is_inference and root.provenance == "unknown"
    assert usage_ledger.usage_row(1, root) is None
