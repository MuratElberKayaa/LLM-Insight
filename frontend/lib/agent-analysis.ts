/**
 * UI-R2: agent-step reading of one trace, from what the backend already returned (Phase 10A
 * `genai_step` per span and `agent_summary`) and the Phase 4A diagnostics. Nothing here is
 * inferred from content: tool arguments, results and prompts are never stored.
 */
import type { Diagnostics } from "./api/diagnostics";
import type { AgentSummary, GenAiStepKind } from "./api/trace-types";
import type { SpanView } from "./span-tree";

/**
 * The diagnostics rule's threshold (backend `REPEATED_TOOL_CALLS`, ADR-54): executions of one
 * named tool in one trace. At or above it the rule reports a *possible* loop, a hypothesis.
 */
export const REPEATED_TOOL_CALLS = 5;
/** The diagnostics category of that rule; its hypothesis code is `possible_agent_loop`. */
export const LOOP_CATEGORY = "repeated_tool_calls";

/** The five step families the explorer tells apart; everything else is an application span. */
export type StepFamily = "agent" | "llm" | "tool" | "retrieval" | "mcp" | "app";

export const STEP_FAMILY_LABEL: Record<StepFamily, string> = {
  agent: "Agent",
  llm: "LLM",
  tool: "Tool",
  retrieval: "Retrieval",
  mcp: "MCP",
  app: "Application",
};

/** One letter per family: a shape cue beside the color, so the kind is never color alone. */
export const STEP_FAMILY_GLYPH: Record<StepFamily, string> = { agent: "A", llm: "L", tool: "T", retrieval: "R", mcp: "M", app: "·" };

const FAMILY_OF_KIND: Record<GenAiStepKind, StepFamily> = {
  agent: "agent",
  workflow: "agent",
  plan: "agent",
  inference: "llm",
  tool: "tool",
  retrieval: "retrieval",
  mcp: "mcp",
};

/** A span's family: a usage row makes it an LLM attempt; otherwise the backend's step kind. */
export function stepFamily(row: Pick<SpanView, "inference" | "step">): StepFamily {
  if (row.inference !== null) return "llm";
  return row.step.kind === null ? "app" : FAMILY_OF_KIND[row.step.kind];
}

export interface ToolUse {
  name: string | null;
  calls: number;
  errors: number;
  /** Executions after the first (0 for a tool run once). */
  repeats: number;
  /** A named tool at or above REPEATED_TOOL_CALLS: the diagnostics rule's possible loop. */
  possibleLoop: boolean;
}

export interface AgentAnalysis {
  /** Spans per family over the returned spans. */
  families: Record<StepFamily, number>;
  tools: ToolUse[];
  /** Named tools at or above the threshold. */
  loopTools: string[];
  /** The diagnostics finding id of the rule, when the diagnostics read succeeded and it fired. */
  loopFindingId: string | null;
  /** True when any agent, tool, retrieval or MCP step was recorded. */
  hasAgentSteps: boolean;
}

export function analyseAgent(rows: SpanView[], summary: AgentSummary, diagnostics: Diagnostics | null): AgentAnalysis {
  const families: Record<StepFamily, number> = { agent: 0, llm: 0, tool: 0, retrieval: 0, mcp: 0, app: 0 };
  for (const r of rows) families[stepFamily(r)] += 1;
  const tools = summary.tools.map((t) => ({
    name: t.name,
    calls: t.calls,
    errors: t.errors,
    repeats: Math.max(0, t.calls - 1),
    // Unnamed tools cannot be told apart, so the rule never groups them (as in the backend).
    possibleLoop: t.name !== null && t.calls >= REPEATED_TOOL_CALLS,
  }));
  const finding = diagnostics?.findings.find((f) => f.category === LOOP_CATEGORY) ?? null;
  return {
    families,
    tools,
    loopTools: tools.filter((t) => t.possibleLoop).map((t) => t.name as string),
    loopFindingId: finding?.id ?? null,
    hasAgentSteps: summary.agent_steps + summary.tool_calls + summary.retrievals + summary.mcp_calls > 0,
  };
}

/** An HTML id for a diagnostics finding (ids such as `repeated_tool_calls:trace`). */
export function findingAnchor(id: string): string {
  return `finding-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

export interface SpanFinding {
  anchor: string;
  category: string;
  hypothesis: string;
}

/** Findings that name a span as evidence, per span id (only the evidence the backend returned). */
export function findingsBySpan(diagnostics: Diagnostics | null): Record<string, SpanFinding[]> {
  const out: Record<string, SpanFinding[]> = {};
  for (const f of diagnostics?.findings ?? []) {
    for (const e of f.evidence) {
      (out[e.span_id] ??= []).push({ anchor: findingAnchor(f.id), category: f.category, hypothesis: f.hypothesis.code });
    }
  }
  return out;
}
