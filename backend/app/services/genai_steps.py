"""Agent, tool, retrieval and MCP steps of a trace (Phase 10A, ADR-54). Pure, read-time only.

The kind of a step comes from the stored `gen_ai.operation.name` (OTel GenAI semantic
conventions, Development status: `semantic-conventions-genai`, agent spans and MCP):

| operation | kind |
|---|---|
| chat, text_completion, generate_content, embeddings | inference |
| invoke_agent, create_agent | agent |
| invoke_workflow | workflow |
| plan | plan |
| execute_tool | tool |
| retrieval | retrieval |
| (none, but `mcp.method.name` recorded) | mcp |

Names are only the recorded identifiers (`gen_ai.agent.name`, `gen_ai.tool.name`,
`gen_ai.data_source.id`, `mcp.method.name`). Nothing is invented, and no content exists to
read: tool arguments and results, agent descriptions and MCP session IDs are never stored
(attrmap v2 deny list).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from app.services.ingestion.attrmap import INFERENCE_OPERATIONS

KIND_BY_OPERATION: dict[str, str] = {
    **dict.fromkeys(INFERENCE_OPERATIONS, "inference"),
    "invoke_agent": "agent",
    "create_agent": "agent",
    "invoke_workflow": "workflow",
    "plan": "plan",
    "execute_tool": "tool",
    "retrieval": "retrieval",
}
NAME_KEY = {
    "agent": "gen_ai.agent.name",
    "tool": "gen_ai.tool.name",
    "retrieval": "gen_ai.data_source.id",
    "mcp": "mcp.method.name",
}
MAX_TOOLS = 50


def _text(attributes: Mapping[str, Any], key: str) -> str | None:
    value = attributes.get(key)
    return value if isinstance(value, str) and value else None


def step(attributes: Mapping[str, Any]) -> dict[str, str | None]:
    """`{kind, name, mcp_method}` of one span; every field is null when not recorded."""
    op = _text(attributes, "gen_ai.operation.name")
    mcp_method = _text(attributes, "mcp.method.name")
    kind = KIND_BY_OPERATION.get(op) if op is not None else None
    if kind is None and op is None and mcp_method is not None:
        kind = "mcp"
    name = _text(attributes, NAME_KEY[kind]) if kind in NAME_KEY else None
    return {"kind": kind, "name": name, "mcp_method": mcp_method}


def summary(spans: Iterable[Any]) -> dict[str, Any]:
    """Trace-level counts. `spans` need `.attributes` and `.status`. The per-tool list is
    bounded to MAX_TOOLS (most calls first, then by name, unnamed last among equals); the
    totals always cover every span given."""
    kinds: Counter[str] = Counter()
    calls: Counter[str | None] = Counter()
    errors: Counter[str | None] = Counter()
    tool_errors = mcp_calls = 0
    for s in spans:
        st = step(s.attributes or {})
        if st["mcp_method"] is not None:
            mcp_calls += 1
        if st["kind"] is None:
            continue
        kinds[st["kind"]] += 1
        if st["kind"] == "tool":
            calls[st["name"]] += 1
            if s.status == "ERROR":
                errors[st["name"]] += 1
                tool_errors += 1
    ordered = sorted(calls, key=lambda n: (-calls[n], n is None, n or ""))
    return {
        "agent_steps": kinds["agent"],
        "inference_calls": kinds["inference"],
        "tool_calls": kinds["tool"],
        "tool_errors": tool_errors,
        "retrievals": kinds["retrieval"],
        "mcp_calls": mcp_calls,
        "tools": [{"name": n, "calls": calls[n], "errors": errors[n]} for n in ordered[:MAX_TOOLS]],
        "tools_truncated": len(ordered) > MAX_TOOLS,
    }
