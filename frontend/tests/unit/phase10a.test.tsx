/**
 * Phase 10A (ADR-54): agent, tool, retrieval and MCP steps in the trace detail. The decoder checks
 * the backend's summary against the returned spans; the waterfall labels steps by their recorded
 * identifiers; the "Agent steps" section appears only when such steps exist. SYNTHETIC fixtures.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- wire-form mutations in decoder tests, as in phase4d.test.tsx */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { AgentSteps } from "../../components/traces/trace-detail-sections";
import { stepLabel } from "../../components/traces/waterfall";
import { traceDetailDecoder } from "../../lib/api/trace-schema";
import { decode } from "../../lib/validate";
import { makeDetail, makeSpan, stepOf, toWire } from "../fixtures/traces";

/** The wire form (lossless integers as the backend sends them), deep-copied for mutation. */
const json = (d: ReturnType<typeof makeDetail>) => structuredClone(toWire(d)) as Record<string, any>;

/** The OTel GenAI agent shape: agent → chat, search_kb ×2 (one failed), an MCP tool, a retrieval. */
function agentDetail() {
  const spans = [
    makeSpan({ id: 1, start: 0n, end: 900n, kind: "INTERNAL", name: "invoke_agent support-triage", attributes: { "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "support-triage" } }),
    makeSpan({ id: 2, parent: 1, start: 10n, end: 100n, name: "chat gpt-5.4-mini", attributes: { "gen_ai.operation.name": "chat" } }),
    makeSpan({ id: 3, parent: 1, start: 110n, end: 160n, kind: "INTERNAL", name: "execute_tool search_kb", attributes: { "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search_kb" } }),
    makeSpan({ id: 4, parent: 1, start: 170n, end: 220n, kind: "INTERNAL", status: "ERROR", name: "execute_tool search_kb", attributes: { "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search_kb" } }),
    makeSpan({ id: 5, parent: 1, start: 230n, end: 280n, kind: "INTERNAL", name: "execute_tool lookup_order", attributes: { "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup_order", "mcp.method.name": "tools/call" } }),
    makeSpan({ id: 6, parent: 1, start: 290n, end: 330n, kind: "INTERNAL", name: "retrieval kb", attributes: { "gen_ai.operation.name": "retrieval", "gen_ai.data_source.id": "kb-main" } }),
  ];
  return makeDetail(spans);
}

describe("Phase 10A decoder", () => {
  it("accepts an agent trace whose summary matches its spans", () => {
    const r = decode(traceDetailDecoder, json(agentDetail()));
    assert.equal(r.ok, true, r.ok ? "" : r.path);
    if (r.ok) {
      assert.deepEqual(r.value.agent_summary.tools, [
        { name: "search_kb", calls: 2, errors: 1 },
        { name: "lookup_order", calls: 1, errors: 0 },
      ]);
      assert.equal(r.value.agent_summary.mcp_calls, 1);
    }
  });

  const broken: [string, (d: Record<string, any>) => void][] = [
    ["a tool count that differs from the spans", (d) => (d.agent_summary.tool_calls = 4)],
    ["per-tool calls that do not add up", (d) => (d.agent_summary.tools[0].calls = 1)],
    ["more tool errors than tool calls", (d) => (d.agent_summary.tools[1].errors = 2)],
    ["an MCP count that differs from the spans", (d) => (d.agent_summary.mcp_calls = 0)],
    ["a step name with whitespace (content, not an identifier)", (d) => (d.spans[2].genai_step.name = "search the kb for the customer")],
    ["a name on an unclassified step", (d) => (d.spans[0].genai_step = { kind: null, name: "x", mcp_method: null })],
    ["a name on an inference step", (d) => (d.spans[1].genai_step.name = "gpt")],
    ["an unknown step kind", (d) => (d.spans[0].genai_step.kind = "subagent")],
    ["a missing step", (d) => delete d.spans[0].genai_step],
    ["a missing summary", (d) => delete d.agent_summary],
  ];
  for (const [name, mutate] of broken) {
    it(`refuses ${name}`, () => {
      const doc = json(agentDetail()) as Record<string, any>;
      mutate(doc);
      assert.equal(decode(traceDetailDecoder, doc).ok, false);
    });
  }
});

describe("Phase 10A presentation", () => {
  it("labels agent, tool, MCP and retrieval steps and leaves inference to its LLM tag", () => {
    const d = agentDetail();
    assert.deepEqual(
      d.spans.map((s) => stepLabel(s.genai_step)),
      ["Agent · support-triage", null, "Tool · search_kb", "Tool · search_kb", "Tool · lookup_order · via MCP tools/call", "Retrieval · kb-main"],
    );
    assert.equal(stepLabel(stepOf({ "mcp.method.name": "tools/list" })), "MCP · tools/list");
    assert.equal(stepLabel(stepOf({ "gen_ai.operation.name": "execute_tool" })), "Tool");
  });

  it("shows the agent steps section with counts and the per-tool table", () => {
    const m = renderToStaticMarkup(<AgentSteps summary={agentDetail().agent_summary} spansTruncated={false} />);
    assert.match(m, /Agent steps/);
    assert.match(m, /1 agent step\(s\) · 1 inference attempt\(s\) · 3 tool execution\(s\), 1 failed · 1 retrieval\(s\) · 1 MCP call\(s\)/);
    assert.match(m, /Tool arguments and results are never stored/);
    assert.match(m, /data-testid="table-tools"/);
    assert.match(m, /search_kb[^]*?>2<[^]*?>1</);
  });

  it("says when the counts cover only the returned spans", () => {
    const m = renderToStaticMarkup(<AgentSteps summary={agentDetail().agent_summary} spansTruncated />);
    assert.match(m, /counted over the returned spans only/);
  });

  it("renders nothing for a trace without agent, tool, retrieval or MCP steps", () => {
    const plain = makeDetail([makeSpan({ id: 1, start: 0n, end: 10n, attributes: { "gen_ai.operation.name": "chat" } })]);
    assert.equal(renderToStaticMarkup(<AgentSteps summary={plain.agent_summary} spansTruncated={false} />), "");
  });

  it("escapes recorded identifiers", () => {
    const d = agentDetail();
    d.agent_summary.tools[0]!.name = "<img>";
    assert.doesNotMatch(renderToStaticMarkup(<AgentSteps summary={d.agent_summary} spansTruncated={false} />), /<img>/);
  });
});
