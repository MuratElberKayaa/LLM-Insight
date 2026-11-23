"""Phase 10A (ADR-54): agent and tool spans from the SDK, checked at a local OTLP stub.

Every span is SYNTHETIC; no provider, agent framework or MCP server is called. The helpers take
no content argument at all, and values that break the attrmap v2 rules are withheld and counted.
"""

from __future__ import annotations

import inspect

import pytest

import llm_insight_sdk as lli
from llm_insight_sdk import instrumentation
from llm_insight_sdk.mock import MockLLMProvider, ObservedMockClient
from tests.conftest import messages


def exported(telemetry, stub):
    assert telemetry.force_flush(5000)
    return {s["name"]: s for s in stub.spans()}


def test_agent_tool_and_inference_form_the_genai_agent_tree(telemetry, stub):
    client = ObservedMockClient(telemetry, MockLLMProvider(time_scale=0.0), max_attempts=1)
    with telemetry.agent_span(
        agent_name="support-triage", agent_id="agent_7f3a", agent_version="1.2.0", provider="openai"
    ):
        client.chat(model="mock-chat-small", messages=messages())
        with telemetry.tool_span(
            tool_name="search_kb", tool_call_id="call_abc123", tool_type="function"
        ):
            pass
        with telemetry.tool_span(
            tool_name="lookup_order", mcp_method="tools/call", mcp_protocol_version="2025-06-18"
        ):
            pass
    spans = exported(telemetry, stub)
    agent = spans["invoke_agent support-triage"]
    assert agent["kind"] == "INTERNAL" and agent["status"] == "UNSET" and agent["events"] == 0
    assert agent["attributes"] == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "support-triage",
        "gen_ai.agent.id": "agent_7f3a",
        "gen_ai.agent.version": "1.2.0",
        "gen_ai.provider.name": "openai",
        "llminsight.provenance": "synthetic",
    }
    tool = spans["execute_tool search_kb"]
    assert tool["parent_span_id"] == agent["span_id"] and tool["kind"] == "INTERNAL"
    assert tool["attributes"] == {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "search_kb",
        "gen_ai.tool.call.id": "call_abc123",
        "gen_ai.tool.type": "function",
        "llminsight.provenance": "synthetic",
    }
    mcp = spans["execute_tool lookup_order"]
    assert mcp["attributes"]["mcp.method.name"] == "tools/call"
    assert mcp["attributes"]["mcp.protocol.version"] == "2025-06-18"
    chat = spans["chat mock-chat-small"]
    assert chat["parent_span_id"] == agent["span_id"] and chat["kind"] == "CLIENT"
    assert {s["trace_id"] for s in spans.values()} == {agent["trace_id"]}


def test_a_failing_tool_ends_with_error_status_and_no_message(telemetry, stub):
    class ToolBroke(RuntimeError):
        pass

    with pytest.raises(ToolBroke), telemetry.tool_span(tool_name="search_kb"):
        raise ToolBroke("CONTENT-SENTINEL tool said: customer 4111 1111 1111 1111")
    with telemetry.tool_span(tool_name="lookup_order") as step:
        step.mark_failed("not_found")
    spans = exported(telemetry, stub)
    broke = spans["execute_tool search_kb"]
    assert broke["status"] == "ERROR" and broke["status_message"] == "" and broke["events"] == 0
    assert broke["attributes"]["error.type"] == "_OTHER"
    assert "CONTENT-SENTINEL" not in repr(broke)
    failed = spans["execute_tool lookup_order"]
    assert failed["status"] == "ERROR" and failed["attributes"]["error.type"] == "not_found"


@pytest.mark.parametrize(
    ("kwargs", "withheld_key"),
    [
        ({"tool_name": "search kb"}, "gen_ai.tool.name"),  # whitespace: not an identifier
        (
            {"tool_name": "t", "tool_call_id": "sk-" + "a" * 30},
            "gen_ai.tool.call.id",
        ),  # credential shape
        ({"tool_name": "t", "mcp_method": "Tools/Call"}, "mcp.method.name"),  # must start lowercase
        ({"tool_name": "user@example.com"}, "gen_ai.tool.name"),  # personal data
    ],
)
def test_invalid_values_are_withheld_and_counted(telemetry, stub, kwargs, withheld_key):
    before = telemetry.stats().attributes_withheld
    with telemetry.tool_span(**kwargs):
        pass
    (span,) = list(exported(telemetry, stub).values())
    assert withheld_key not in span["attributes"]
    assert telemetry.stats().attributes_withheld == before + 1
    if withheld_key == "gen_ai.tool.name":
        assert span["name"] == "execute_tool"


def test_the_helpers_accept_no_content():
    for fn in (
        instrumentation.agent_span,
        instrumentation.tool_span,
        lli.Telemetry.agent_span,
        lli.Telemetry.tool_span,
    ):
        params = set(inspect.signature(fn).parameters)
        assert not params & {
            "arguments",
            "result",
            "description",
            "instructions",
            "messages",
            "session_id",
            "input",
            "output",
        }


def test_operation_and_provenance_are_checked():
    with (
        pytest.raises(ValueError),
        instrumentation.agent_span(
            None,
            on_withheld=lambda k: None,
            provenance="synthetic",
            agent_name="a",
            operation="chat",
        ),
    ):
        pass
    with (
        pytest.raises(ValueError),
        instrumentation.tool_span(
            None, on_withheld=lambda k: None, provenance="maybe", tool_name="t"
        ),
    ):  # type: ignore[arg-type]
        pass


def test_create_agent_is_supported(telemetry, stub):
    with telemetry.agent_span(agent_name="planner", operation="create_agent"):
        pass
    (span,) = list(exported(telemetry, stub).values())
    assert span["name"] == "create_agent planner"
    assert span["attributes"]["gen_ai.operation.name"] == "create_agent"
