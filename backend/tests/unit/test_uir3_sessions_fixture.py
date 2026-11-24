"""UI-R3: the SYNTHETIC session set the sessions e2e test sends (`scripts/dev/seed_trace_fixtures.py
--set ui-r3-sessions`) through the real ingest rules with a session pseudonymizer, offline.

The browser test (`frontend/tests/e2e/ui-r3.spec.ts`) expects three sessions of 3, 2 and 1
traces, one failed attempt, and no raw conversation id anywhere on the page. This test shows the
backend's own rules give exactly that input: every span gets a keyed pseudonym and the raw
`gen_ai.conversation.id` is not kept. No database, no network, no provider."""

import importlib.util
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from app.services.ingestion import sessions
from app.services.ingestion.normalize import NormalizedSpan, normalize

ROOT = Path(__file__).resolve().parents[3]
SEEDER = ROOT / "scripts" / "dev" / "seed_trace_fixtures.py"
ANCHOR = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
PEPPER = b"unit-test-pepper-not-a-secret-000000"


@pytest.fixture(scope="module")
def spans() -> list[NormalizedSpan]:
    spec = importlib.util.spec_from_file_location("seed_trace_fixtures_uir3", SEEDER)
    assert spec is not None and spec.loader is not None
    seeder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seeder)
    b = seeder.Builder("uir3-unit", int(ANCHOR.timestamp()) * 1_000_000_000)
    seeder.ui_r3_sessions_set(b)
    req = ExportTraceServiceRequest.FromString(b.request().SerializeToString())
    out, violations = normalize(req, sessions.pseudonymizer(PEPPER, 7))
    assert violations == []
    return out


def test_three_sessions_of_three_two_and_one_traces(spans):
    per_trace = {s.trace_id: s.attributes[sessions.HASH_KEY] for s in spans}
    assert len(per_trace) == 6
    assert sorted(Counter(per_trace.values()).values()) == [1, 2, 3]
    assert all(v.startswith(sessions.PREFIX) for v in per_trace.values())


def test_the_raw_conversation_id_is_not_kept(spans):
    for s in spans:
        assert sessions.SOURCE_KEY not in s.attributes
        assert not any("uir3-conv" in str(v) for v in s.attributes.values())


def test_one_failed_attempt_and_synthetic_inference(spans):
    inference = [s for s in spans if s.is_inference]
    assert len(inference) == 6
    assert sum(s.status == "ERROR" for s in inference) == 1
    assert {s.provenance for s in inference} == {"synthetic"}
