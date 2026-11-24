"""UI-R2: the SYNTHETIC agent trace the explorer e2e test sends (`scripts/dev/seed_trace_fixtures.py
--set ui-r2-agent`) through the real ingest rules and the Phase 10A step classification, offline.

The browser test (`frontend/tests/e2e/ui-r2.spec.ts`) pins what the explorer shows for this trace:
every step family, `lookup_order` exactly at the possible-loop threshold, the recorded time to
first chunk. This test shows the backend's own rules produce those inputs, so a wrong expectation
fails here first. No database, no network, no provider; no content attribute is sent."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.services import genai_steps
from app.services.diagnostics.engine import REPEATED_TOOL_CALLS
from app.services.ingestion.normalize import NormalizedSpan, normalize

ROOT = Path(__file__).resolve().parents[3]
SEEDER = ROOT / "scripts" / "dev" / "seed_trace_fixtures.py"
ANCHOR = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CONTENT_KEYS = (
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.prompt",
    "gen_ai.completion",
)


@pytest.fixture(scope="module")
def spans() -> list[NormalizedSpan]:
    spec = importlib.util.spec_from_file_location("seed_trace_fixtures_uir2", SEEDER)
    assert spec is not None and spec.loader is not None
    seeder: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seeder)
    b = seeder.Builder("uir2-unit", int(ANCHOR.timestamp()) * 1_000_000_000)
    seeder.ui_r2_agent_set(b)
    out, violations = normalize(
        ExportTraceServiceRequest.FromString(b.request().SerializeToString())
    )
    assert violations == []
    return out


def test_one_trace_with_every_step_family(spans):
    assert len({s.trace_id for s in spans}) == 1
    assert {s.resource_attributes["service.name"] for s in spans} == {"uir2.agent"}
    kinds = [genai_steps.step(s.attributes)["kind"] for s in spans]
    assert kinds.count("agent") == 1
    assert kinds.count("inference") == 1
    assert kinds.count("retrieval") == 1
    assert kinds.count("mcp") == 1
    assert kinds.count("tool") == 7


def test_lookup_order_sits_exactly_at_the_possible_loop_threshold(spans):
    summary = genai_steps.summary(spans)
    tools = {t["name"]: t for t in summary["tools"]}
    assert tools["lookup_order"] == {
        "name": "lookup_order",
        "calls": REPEATED_TOOL_CALLS,
        "errors": 1,
    }
    assert tools["search_kb"]["calls"] == 2
    assert summary["tool_errors"] == 1


def test_the_streaming_attempt_records_its_first_chunk_time_and_nothing_else_streams(spans):
    llm = [s for s in spans if s.is_inference]
    assert len(llm) == 1
    assert llm[0].attributes["gen_ai.request.stream"] is True
    assert llm[0].attributes["gen_ai.response.time_to_first_chunk"] == pytest.approx(0.25)
    assert llm[0].provenance == "synthetic"


def test_no_content_is_sent(spans):
    for s in spans:
        assert not any(k in s.attributes for k in CONTENT_KEYS), s.name
