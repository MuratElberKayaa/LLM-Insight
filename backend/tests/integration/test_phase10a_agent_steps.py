"""Phase 10A (ADR-54): agent, tool and MCP spans through the real ingest path and the read API.

Every span is SYNTHETIC and made by this test; no provider or MCP server is called. The trace is
the OTel GenAI agent shape: an `invoke_agent` span with `chat` and `execute_tool` children, one
tool reached through MCP. Content keys are sent on purpose and must be dropped (attrmap v2).

    invoke_agent support-triage          (INTERNAL)
    ├── chat gpt-5.4-mini                (CLIENT, inference)
    ├── execute_tool search_kb  ×5       (INTERNAL; one ERROR)  -> repeated_tool_calls
    ├── execute_tool lookup_order        (INTERNAL; mcp.method.name tools/call)
    └── chat gpt-5.4-mini                (CLIENT, inference)
"""

from datetime import UTC, datetime

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post, rows
from tests.otlp import MS, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
BASE = (
    int(
        (
            datetime(2026, 11, 20, 9, 0, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        ).total_seconds()
    )
    * SEC
)
TRACE = "10a" + "0" * 28 + "1"
WINDOW = {"from": "2026-11-20T00:00:00Z", "to": "2026-11-21T00:00:00Z"}
CONTENT = "AGENT-CONTENT-SENTINEL-do-not-store-10a"


def sid(i: int) -> str:
    return f"{i:016x}"


def agent_trace() -> list[Span]:
    spans = [
        span(
            trace_id=TRACE,
            span_id=sid(1),
            name="invoke_agent support-triage",
            kind=Span.SPAN_KIND_INTERNAL,
            start=BASE,
            end=BASE + 900 * MS,
            attrs={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.provider.name": "openai",
                "gen_ai.agent.name": "support-triage",
                "gen_ai.agent.id": "agent_7f3a",
                "gen_ai.agent.version": "1.2.0",
                "gen_ai.agent.description": CONTENT,  # denied
                "llminsight.provenance": "synthetic",
            },
        )
    ]
    t = BASE + 10 * MS
    for i, (kind, extra) in enumerate(
        [("chat", {})]
        + [("tool", {"gen_ai.tool.name": "search_kb"})] * 5
        + [
            (
                "tool",
                {
                    "gen_ai.tool.name": "lookup_order",
                    "mcp.method.name": "tools/call",
                    "mcp.protocol.version": "2025-06-18",
                },
            )
        ]
        + [("chat", {})],
        start=2,
    ):
        if kind == "chat":
            attrs = inference_attrs(
                gen_ai__request__model="gpt-5.4-mini",
                gen_ai__response__model="gpt-5.4-mini-2026-03-17",
                gen_ai__response__id=f"chatcmpl-SYNTHETIC-10a-{i}",
                llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{10_000 + i:012d}",
            )
            name, k = "chat gpt-5.4-mini", Span.SPAN_KIND_CLIENT
        else:
            attrs = {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.type": "function",
                "gen_ai.tool.call.id": f"call_SYNTHETIC_{i}",
                "gen_ai.tool.call.arguments": CONTENT,  # denied
                "gen_ai.tool.call.result": CONTENT,  # denied
                "mcp.session.id": "sess-SYNTHETIC-1",  # denied
                "llminsight.provenance": "synthetic",
                **extra,
            }
            name, k = f"execute_tool {extra['gen_ai.tool.name']}", Span.SPAN_KIND_INTERNAL
        spans.append(
            span(
                trace_id=TRACE,
                span_id=sid(i),
                parent=sid(1),
                name=name,
                kind=k,
                start=t,
                end=t + 50 * MS,
                status_code=2 if i == 3 else 0,  # one failed search_kb execution
                attrs=attrs,
            )
        )
        t += 60 * MS
    return spans


@pytest.fixture
def seeded(api_client, make_key):
    k = make_key("p10a")
    assert post(api_client, body(request(*agent_trace())), k.key).status_code == 200
    return k


def get(client, path, project, **params):
    r = client.get(path, params={"project": project, **params}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def test_agent_and_tool_metadata_is_stored_and_content_is_dropped(seeded, db):
    stored = {
        r["span_id"]: r
        for r in rows(
            db,
            "SELECT span_id, attributes, attributes_schema_version, server_dropped_attributes_count"
            " FROM spans WHERE project_id = :p",
            p=seeded.project_id,
        )
    }
    assert len(stored) == 9
    assert {r["attributes_schema_version"] for r in stored.values()} == {"llminsight.attrmap.v2"}
    agent = stored[sid(1)]
    assert agent["attributes"]["gen_ai.agent.name"] == "support-triage"
    assert agent["attributes"]["gen_ai.agent.version"] == "1.2.0"
    assert "gen_ai.agent.description" not in agent["attributes"]
    assert agent["server_dropped_attributes_count"] == 1
    mcp = stored[sid(8)]
    assert mcp["attributes"]["gen_ai.tool.name"] == "lookup_order"
    assert mcp["attributes"]["mcp.method.name"] == "tools/call"
    for key in ("gen_ai.tool.call.arguments", "gen_ai.tool.call.result", "mcp.session.id"):
        assert key not in mcp["attributes"]
    assert mcp["server_dropped_attributes_count"] == 3
    everything = repr(rows(db, "SELECT * FROM spans WHERE project_id = :p", p=seeded.project_id))
    assert CONTENT not in everything and "sess-SYNTHETIC" not in everything
    # Only the two chat spans are inference: tool and agent spans get no usage row.
    usage = rows(db, "SELECT span_id FROM usage_records WHERE project_id = :p", p=seeded.project_id)
    assert sorted(r["span_id"] for r in usage) == [sid(2), sid(9)]


def test_trace_detail_carries_steps_and_the_agent_summary(seeded, api_client):
    doc = get(api_client, f"/api/v1/traces/{TRACE}", seeded.project_id)
    steps = {s["span_id"]: s["genai_step"] for s in doc["spans"]}
    assert steps[sid(1)] == {"kind": "agent", "name": "support-triage", "mcp_method": None}
    assert steps[sid(2)] == {"kind": "inference", "name": None, "mcp_method": None}
    assert steps[sid(3)] == {"kind": "tool", "name": "search_kb", "mcp_method": None}
    assert steps[sid(8)] == {"kind": "tool", "name": "lookup_order", "mcp_method": "tools/call"}
    assert doc["agent_summary"] == {
        "agent_steps": 1,
        "inference_calls": 2,
        "tool_calls": 6,
        "tool_errors": 1,
        "retrievals": 0,
        "mcp_calls": 1,
        "tools": [
            {"name": "search_kb", "calls": 5, "errors": 1},
            {"name": "lookup_order", "calls": 1, "errors": 0},
        ],
        "tools_truncated": False,
    }


def test_diagnostics_flag_the_repeated_tool_as_a_hypothesis(seeded, api_client):
    doc = get(api_client, f"/api/v1/traces/{TRACE}/diagnostics", seeded.project_id)
    assert doc["ruleset_version"] == "llminsight.diagnostics.rules.v2"
    loops = [f for f in doc["findings"] if f["category"] == "repeated_tool_calls"]
    assert len(loops) == 1
    facts = {f["name"]: f["value"] for f in loops[0]["observed_facts"]}
    assert facts == {
        "repeated_tools": [{"tool_name": "search_kb", "executions": 5, "failed_executions": 1}],
        "threshold": 5,
    }
    assert loops[0]["id"] == "repeated_tool_calls:trace"
    assert loops[0]["evidence_total"] == 5 and loops[0]["hypothesis"]["verified"] is False
    text = repr(loops[0])
    assert "Possible explanation (not verified)" in text and CONTENT not in text
    # The failed tool execution is also reported by the existing non-inference error rule.
    errors = [f for f in doc["findings"] if f["category"] == "non_inference_span_error"]
    assert [f["subject"].get("span_id") for f in errors] == [sid(3)]
