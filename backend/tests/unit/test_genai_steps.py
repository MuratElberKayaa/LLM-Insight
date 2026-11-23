"""Phase 10A (ADR-54): agent, tool, retrieval and MCP steps derived from stored span metadata.

Pure functions over the allowlisted attributes; SYNTHETIC inputs only. The classification never
reads content (none is stored) and never invents a name that was not recorded.
"""

from types import SimpleNamespace

import pytest

from app.services import genai_steps as gs


def span(op=None, status="OK", **attrs):
    a = {k.replace("__", "."): v for k, v in attrs.items()}
    if op is not None:
        a["gen_ai.operation.name"] = op
    return SimpleNamespace(attributes=a, status=status)


@pytest.mark.parametrize(
    ("op", "attrs", "kind", "name"),
    [
        ("chat", {}, "inference", None),
        ("embeddings", {}, "inference", None),
        ("invoke_agent", {"gen_ai__agent__name": "triage"}, "agent", "triage"),
        ("create_agent", {"gen_ai__agent__name": "triage"}, "agent", "triage"),
        ("invoke_workflow", {}, "workflow", None),
        ("plan", {}, "plan", None),
        ("execute_tool", {"gen_ai__tool__name": "search_kb"}, "tool", "search_kb"),
        ("retrieval", {"gen_ai__data_source__id": "kb-main"}, "retrieval", "kb-main"),
        (None, {"mcp__method__name": "tools/list"}, "mcp", "tools/list"),
        # An MCP tool call is a tool step; the MCP method is kept as its own field.
        (
            "execute_tool",
            {"gen_ai__tool__name": "get_weather", "mcp__method__name": "tools/call"},
            "tool",
            "get_weather",
        ),
        (None, {}, None, None),
        ("unknown_op", {}, None, None),
    ],
)
def test_classification(op, attrs, kind, name):
    step = gs.step(span(op, **attrs).attributes)
    assert (step["kind"], step["name"]) == (kind, name)


def test_the_mcp_method_is_reported_on_any_step():
    s = gs.step(
        span("execute_tool", gen_ai__tool__name="t", mcp__method__name="tools/call").attributes
    )
    assert s["mcp_method"] == "tools/call"
    assert gs.step(span("chat").attributes)["mcp_method"] is None


def test_non_string_values_are_ignored():
    s = gs.step({"gen_ai.operation.name": 5, "gen_ai.tool.name": ["x"]})
    assert s == {"kind": None, "name": None, "mcp_method": None}


def test_summary_counts_steps_and_groups_tools():
    spans = [
        span("invoke_agent", gen_ai__agent__name="triage"),
        span("chat"),
        span("execute_tool", gen_ai__tool__name="search_kb"),
        span("execute_tool", "ERROR", gen_ai__tool__name="search_kb"),
        span("execute_tool", gen_ai__tool__name="lookup_order", mcp__method__name="tools/call"),
        span("execute_tool"),  # no tool name recorded
        span("retrieval", gen_ai__data_source__id="kb"),
        span(None, mcp__method__name="tools/list"),
        span(None),
    ]
    summary = gs.summary(spans)
    assert summary == {
        "agent_steps": 1,
        "inference_calls": 1,
        "tool_calls": 4,
        "tool_errors": 1,
        "retrievals": 1,
        "mcp_calls": 2,
        "tools": [
            {"name": "search_kb", "calls": 2, "errors": 1},
            {"name": "lookup_order", "calls": 1, "errors": 0},
            {"name": None, "calls": 1, "errors": 0},  # unnamed last among equals
        ],
        "tools_truncated": False,
    }


def test_summary_of_a_plain_trace_is_all_zero():
    assert gs.summary([span("chat"), span(None)]) == {
        "agent_steps": 0,
        "inference_calls": 1,
        "tool_calls": 0,
        "tool_errors": 0,
        "retrievals": 0,
        "mcp_calls": 0,
        "tools": [],
        "tools_truncated": False,
    }


def test_tool_list_is_bounded_and_ordered():
    spans = [
        span("execute_tool", gen_ai__tool__name=f"tool_{i:03d}") for i in range(gs.MAX_TOOLS + 5)
    ]
    spans += [span("execute_tool", gen_ai__tool__name="tool_999")] * 3
    s = gs.summary(spans)
    assert len(s["tools"]) == gs.MAX_TOOLS and s["tools_truncated"] is True
    assert s["tools"][0] == {"name": "tool_999", "calls": 3, "errors": 0}
    assert [t["name"] for t in s["tools"][1:4]] == ["tool_000", "tool_001", "tool_002"]
    assert s["tool_calls"] == gs.MAX_TOOLS + 8  # counts are never truncated


def test_only_error_status_counts_as_a_failed_tool():
    # The SDK leaves a successful span UNSET; only ERROR is a failure.
    spans = [
        span("execute_tool", "UNSET", gen_ai__tool__name="t"),
        span("execute_tool", "OK", gen_ai__tool__name="t"),
    ]
    s = gs.summary(spans)
    assert (s["tool_calls"], s["tool_errors"]) == (2, 0)
    assert s["tools"] == [{"name": "t", "calls": 2, "errors": 0}]


def test_an_unknown_operation_is_not_reclassified_as_mcp():
    # A recorded but unknown operation stays unclassified; only an MCP span without any
    # operation is an `mcp` step. The MCP method is still reported.
    s = gs.step(span("invoke_subagent", mcp__method__name="tools/list").attributes)
    assert s == {"kind": None, "name": None, "mcp_method": "tools/list"}
