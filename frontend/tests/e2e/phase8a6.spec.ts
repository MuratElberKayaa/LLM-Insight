/**
 * Phase 8A-6: the multi-provider comparison set in the dashboard, against the REAL stack:
 * SYNTHETIC OTLP fixtures → POST /v1/traces → PostgreSQL → authenticated /api/v1 → Next.js →
 * browser. Nothing is mocked and no provider is ever called.
 *
 * Data: the run-owned comparison project (scripts/dev/e2e_fixtures.py, `--set phase8a6`):
 * - OpenAI, priced by the verified catalogue;
 * - Gemini;
 * - the three Groq comparison scenarios of Phase 8A-4 under a `demo.live-compare` root;
 * - a SYNTHETIC OpenRouter compatibility row (no OpenRouter adapter exists);
 * - a root-only `demo.live-compare` trace (R131);
 * - one adversarial trace.
 * Every inference span is labelled `synthetic`. These tests show how the dashboard renders
 * stored comparison telemetry; they are never evidence about a real provider response (R115).
 *
 * Expectations come from direct backend reads for the window the page echoes, and from the
 * values the fixture defines. The Traces list has no prompt-version filter (R130), and no
 * dashboard page sends one (R132), so scenarios are told apart by their own Compare rows and by
 * the prompt version each trace carries.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";
import { formatUsd } from "../../lib/decimal";
import { formatInt } from "../../lib/format";
import { API, FOREIGN_PROJECT, HOSTILE_PROJECT, PROJECT, PROVIDERS_PROJECT, SHOTS, TOKEN, comparisonProject, u } from "./e2e-env";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Win = { from: string; to: string };

// ---------------------------------------------------------------- the fixture (`--set phase8a6`)
const GROQ_MODEL = "openai/gpt-oss-20b";
const OPENROUTER_MODEL = "synthetic/compat-only:free";
/** docs/PRICING.md §4 worked example, as in backend/tests/integration/test_multi_provider_read.py. */
const OPENAI_COST = "0.0015750000";
const XSS = "window.__llmiXss=1";
const SCENARIOS = [
  { service: "cmp8a6.groq.ticket-triage", name: "cmp.ticket-triage", version: "cmp-v1.ticket-triage", tokens: [20, 32] },
  { service: "cmp8a6.groq.json-aggregate", name: "cmp.json-aggregate", version: "cmp-v1.json-aggregate", tokens: [24, 40] },
  { service: "cmp8a6.groq.release-summary", name: "cmp.release-summary", version: "cmp-v1.release-summary", tokens: [30, 64] },
] as const;
const OPENROUTER = "cmp8a6.openrouter-synthetic";
const ROOT_ONLY = "cmp8a6.refused-root-only";
const ADVERSARIAL = `<img src=x onerror="${XSS}">cmp8a6.adversarial`;
const SERVICES = ["cmp8a6.openai", "cmp8a6.gemini", ...SCENARIOS.map((s) => s.service), OPENROUTER, ROOT_ONLY, ADVERSARIAL];
const PROMPT_VERSION: Record<string, string | null> = {
  "cmp8a6.openai": "v1",
  "cmp8a6.gemini": "v1",
  ...Object.fromEntries(SCENARIOS.map((s) => [s.service, s.version])),
  [OPENROUTER]: "cmp-v1.ticket-triage",
  [ROOT_ONLY]: null,
  [ADVERSARIAL]: null,
};
/** Values seeded on the adversarial span that the server must drop; never in any response. */
const NEVER_SHOWN = ["PHASE8A6-CONTENT-SENTINEL-never-stored", "PHASE8A6-REASONING-SENTINEL-never-stored", ["gsk", "Q7xE2EDASH".repeat(4)].join("_")];
const DROPPED_KEYS = ["gen_ai.provider.name", "gen_ai.request.model", "gen_ai.response.id", "gen_ai.input.messages", "gen_ai.output.messages", "llm.reasoning"];
const ZERO_USD = /\$0(\.0+)?(?![\d.])/;
const CREDENTIAL_TEXT = /LLMI_API_TOKEN|LLMI_ADMIN_TOKEN|Bearer\s+[A-Za-z0-9]/;

/** The run's comparison project id (read in `beforeAll`, never at import: see e2e-env.ts). */
let C = "";

test.beforeAll(() => {
  C = comparisonProject();
  expect(TOKEN.length, "frontend/.env.local must hold the server-side token").toBeGreaterThan(31);
  expect(new Set([C, PROJECT, FOREIGN_PROJECT, HOSTILE_PROJECT, PROVIDERS_PROJECT]).size).toBe(5);
});

// ---------------------------------------------------------------- helpers
/** Direct backend read, paced under the shared /api/v1 rate limit. */
async function api(request: APIRequestContext, pathAndQuery: string, project: string = C): Promise<any> {
  await new Promise((r) => setTimeout(r, 150));
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const res = await request.get(`${API}${pathAndQuery}${sep}project=${project}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  expect(res.status(), pathAndQuery).toBe(200);
  return res.json();
}

async function rawText(request: APIRequestContext, pathAndQuery: string, project: string = C): Promise<{ status: number; text: string }> {
  await new Promise((r) => setTimeout(r, 150));
  const sep = pathAndQuery.includes("?") ? "&" : "?";
  const res = await request.get(`${API}${pathAndQuery}${sep}project=${project}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  return { status: res.status(), text: await res.text() };
}

async function pageWindow(page: Page): Promise<Win> {
  const el = page.getByTestId("window");
  return { from: (await el.getAttribute("data-from")) ?? "", to: (await el.getAttribute("data-to")) ?? "" };
}

const q = (w: Win, extra: Record<string, string> = {}) => new URLSearchParams({ from: w.from, to: w.to, ...extra }).toString();

/** All traces of the comparison project in the default 24 h window (one page: 8 traces). */
async function fixtures(page: Page, request: APIRequestContext): Promise<{ w: Win; items: any[]; by: (service: string) => any }> {
  await page.goto(u("/traces?limit=100", C));
  const w = await pageWindow(page);
  const data = await api(request, `/api/v1/traces?${q(w, { limit: "100" })}`);
  const by = (service: string) => {
    const t = data.items.find((x: any) => x.service_name === service);
    expect(t, `seeded trace ${service} of this run (global setup)`).toBeTruthy();
    return t;
  };
  return { w, items: data.items, by };
}

async function uiTraceIds(page: Page): Promise<string[]> {
  await expect(page.getByTestId("trace-table")).toBeVisible(); // evaluateAll does not wait (R72)
  return page.getByTestId("trace-table").getByTestId("trace-link").evaluateAll((els) => els.map((e) => e.getAttribute("data-trace-id") ?? ""));
}

async function services(page: Page): Promise<string[]> {
  await expect(page.getByTestId("trace-table")).toBeVisible();
  return (await page.getByTestId("trace-table").getByTestId("trace-link").evaluateAll((els) => els.map((e) => e.querySelector("span")?.textContent ?? ""))).sort();
}

function rowOf(page: Page, traceId: string) {
  return page.getByTestId("trace-table").locator("tr", { has: page.locator(`[data-trace-id="${traceId}"]`) });
}

function groupRows(page: Page) {
  return page.getByTestId("compare-table").locator("tbody tr");
}

async function inert(page: Page): Promise<void> {
  expect(await page.evaluate(() => (window as unknown as { __llmiXss?: number }).__llmiXss)).toBeUndefined();
  expect(await page.locator("main img, main script, header img").count()).toBe(0);
}

async function shot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  await expect(page.locator('[role="status"][aria-label^="Loading"]')).toHaveCount(0);
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
}

// ---------------------------------------------------------------- B: Traces
test("Traces: the comparison project lists exactly its SYNTHETIC set, as the backend returns it", async ({ page, request }) => {
  const { items } = await fixtures(page, request);
  expect(items.map((t: any) => t.service_name).sort()).toEqual([...SERVICES].sort());
  expect(await uiTraceIds(page)).toEqual(items.map((t: any) => t.trace_id)); // backend order, newest first
  for (const t of items) {
    expect(t.provenance_verified).toBe(false);
    expect(t.provenance).toBe(t.service_name === ROOT_ONLY ? "unknown" : "synthetic");
    expect(t.prompt_version).toBe(PROMPT_VERSION[t.service_name]);
    expect(t.usage.inference_calls).toBe(t.service_name === ROOT_ONLY ? 0 : 1);
    const row = rowOf(page, t.trace_id);
    await expect(row.getByTestId("trace-link")).toContainText(t.service_name);
    await expect(row.locator(`[data-provenance="${t.provenance}"]`)).toBeVisible();
    await expect(row.locator("[data-trace-status]")).toHaveAttribute("data-trace-status", "ok");
    if (t.prompt_version !== null) await expect(row).toContainText(t.prompt_version);
    const cost = row.locator("[data-cost-state]");
    if (t.usage.inference_calls === 0) {
      await expect(cost).toHaveCount(0);
    } else if (t.usage.known_cost_usd === null) {
      await expect(cost).toHaveAttribute("data-cost-state", "unknown");
      await expect(cost).not.toContainText("$");
    } else {
      expect(t.service_name).toBe("cmp8a6.openai");
      expect(t.usage.known_cost_usd).toBe(OPENAI_COST);
      await expect(cost).toContainText(formatUsd(OPENAI_COST)!.text);
    }
  }
  for (const s of SCENARIOS) {
    const row = rowOf(page, items.find((t: any) => t.service_name === s.service).trace_id);
    await expect(row.getByTestId("trace-model")).toHaveText(`groq · ${GROQ_MODEL} ×1`);
    await expect(row).toContainText(`${s.tokens[0]} / ${s.tokens[1]}`);
  }
  const table = page.getByTestId("trace-table");
  await expect(table.locator('[data-provenance="real"]')).toHaveCount(0);
  await expect(table).not.toContainText(ZERO_USD);
  await expect(page.locator("main")).not.toContainText(/verified real|provenance verified/i);
  await shot(page, "8a6-01-traces");
});

test("Traces: provider and model are exact backend filters; prompt_version is not a Traces filter (R130)", async ({ page, request }) => {
  await page.goto(u("/traces", C));
  const w = await pageWindow(page);
  const select = page.getByTestId("provider-select");
  const values = await select.locator("option").evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value));
  expect(values.sort()).toEqual(["", "gcp.gemini", "groq", "openai", "openrouter", "unknown"]);
  await expect(page.getByTestId("scope-note")).toContainText("1 attempt reported no model"); // the adversarial span

  await select.selectOption("groq");
  await page.getByTestId("scope-apply").click();
  await expect(page).toHaveURL(/provider=groq/);
  const groq = await api(request, `/api/v1/traces?${q(w, { provider: "groq", limit: "100" })}`);
  expect(groq.items.map((t: any) => t.service_name).sort()).toEqual(SCENARIOS.map((s) => s.service).sort());
  expect(await uiTraceIds(page)).toEqual(groq.items.map((t: any) => t.trace_id));

  await page.goto(u(`/traces?${new URLSearchParams({ provider: "groq", model: GROQ_MODEL })}`, C));
  expect(await services(page)).toEqual(SCENARIOS.map((s) => s.service).sort());
  await page.goto(u("/traces?provider=openrouter", C));
  expect(await services(page)).toEqual([OPENROUTER]);
  await page.goto(u(`/traces?${new URLSearchParams({ model: OPENROUTER_MODEL })}`, C));
  expect(await services(page)).toEqual([OPENROUTER]);
  await page.goto(u("/traces?provider=gcp.gemini", C));
  expect(await services(page)).toEqual(["cmp8a6.gemini"]);
  await page.goto(u("/traces?provider=unknown", C));
  expect(await services(page)).toEqual([ADVERSARIAL]);

  // R130: no prompt-version filter exists for the list. The page shows the unfiltered list, and
  // the backend returns the same list as without the parameter (it is not a filter).
  await page.goto(u("/traces?prompt_version=cmp-v1.ticket-triage", C));
  expect(await services(page)).toEqual([...SERVICES].sort());
  const all = await api(request, `/api/v1/traces?${q(w, { limit: "100" })}`);
  const ignored = await api(request, `/api/v1/traces?${q(w, { limit: "100", prompt_version: "cmp-v1.ticket-triage" })}`);
  expect(ignored.items.map((t: any) => t.trace_id)).toEqual(all.items.map((t: any) => t.trace_id));
});

for (const s of SCENARIOS) {
  test(`Trace detail: ${s.service} keeps its demo.live-compare root, CLIENT span, prompt identity, finish reason and unknown cost`, async ({ page, request }) => {
    const { by } = await fixtures(page, request);
    const t = by(s.service);
    const d = await api(request, `/api/v1/traces/${t.trace_id}`);
    expect(d.spans).toHaveLength(2);
    const root = d.spans.find((x: any) => x.role === "root");
    const client = d.spans.find((x: any) => x.kind === "CLIENT");
    expect([root.name, root.kind, root.parent_span_id]).toEqual(["demo.live-compare", "INTERNAL", null]);
    expect(client.parent_span_id).toBe(root.span_id);
    expect(client.server_dropped_attributes_count).toBe(0);
    expect(client.attributes["gen_ai.provider.name"]).toBe("groq");
    expect([client.attributes["gen_ai.prompt.name"], client.attributes["gen_ai.prompt.version"]]).toEqual([s.name, s.version]);
    expect(client.attributes["gen_ai.response.finish_reasons"]).toEqual(["stop"]);
    expect(d.trace.prompt_version).toBe(s.version);
    expect([d.trace.provenance, d.trace.provenance_verified, d.trace.status]).toEqual(["synthetic", false, "ok"]);
    expect(d.usage).toHaveLength(1);
    const usage = d.usage[0];
    expect([usage.provider, usage.requested_model, usage.response_model]).toEqual(["groq", GROQ_MODEL, GROQ_MODEL]);
    expect([Number(usage.input_tokens), Number(usage.output_tokens)]).toEqual([...s.tokens]);
    expect([usage.cost_usd, usage.cost_status, usage.cost_unknown_reason, usage.price_version]).toEqual([null, "unknown", "price_missing", null]);

    await page.goto(u(`/traces/${t.trace_id}`, C));
    await expect(page.getByRole("heading", { level: 1, name: "Trace" })).toBeVisible();
    const items = page.getByRole("tree", { name: "Span hierarchy and timeline" }).getByRole("treeitem");
    await expect(items).toHaveCount(2);
    await expect(items.nth(0)).toHaveAttribute("data-span-id", root.span_id);
    await expect(items.nth(0)).toHaveAttribute("aria-level", "1");
    await expect(items.nth(1)).toHaveAttribute("data-span-id", client.span_id);
    await expect(items.nth(1)).toHaveAttribute("aria-level", "2");
    await expect(page.getByTestId("fact-prompt")).toHaveText(s.version);
    await expect(page.getByTestId("fact-status")).toContainText("OK");
    await expect(page.getByTestId("fact-spans")).toHaveText("2");
    await expect(page.getByTestId("fact-tokens")).toContainText("1 attempts");
    await expect(page.getByTestId("fact-tokens")).toContainText(`${s.tokens[0]} / ${s.tokens[1]}`);
    await expect(page.getByTestId("fact-cost").locator('[data-cost-state="unknown"]')).toBeVisible();
    await expect(page.getByTestId("fact-cost")).not.toContainText("$");
    await expect(page.getByTestId("trace-provenance-notice")).toHaveAttribute("data-provenance", "synthetic");
    await expect(page.getByTestId("status-calls")).toContainText("1 (1 physical attempt)");
    const attempt = page.getByTestId("table-attempts").locator("tbody tr");
    await expect(attempt).toHaveCount(1);
    await expect(attempt).toContainText("groq");
    await expect(attempt).toContainText(GROQ_MODEL);
    await expect(attempt.locator('[data-cost-state="unknown"]')).toContainText("no verified price for this model");
    await expect(attempt).not.toContainText("$");

    // The CLIENT span's stored metadata, selected through the URL.
    await page.goto(u(`/traces/${t.trace_id}?span=${client.span_id}`, C));
    const details = page.getByTestId("span-details");
    await expect(details).toHaveAttribute("data-span-id", client.span_id);
    await expect(page.getByTestId("span-details-name")).toHaveText(`chat ${GROQ_MODEL}`);
    await expect(details).toContainText("Status UNSET (no error recorded)");
    await expect(page.getByTestId("span-inference")).toContainText("groq");
    await expect(page.getByTestId("span-cost")).toHaveAttribute("data-cost-state", "unknown");
    await expect(page.getByTestId("span-cost")).toHaveText("Unknown: no verified price for this model");
    const attrs = page.getByTestId("span-attributes");
    for (const text of ["gen_ai.response.finish_reasons", "stop", "gen_ai.prompt.version", s.version, "gen_ai.prompt.name", s.name]) await expect(attrs).toContainText(text);
    await expect(details).toContainText("Dropped by server0");

    await page.goto(u(`/traces/${t.trace_id}?span=${root.span_id}`, C));
    await expect(page.getByTestId("span-details-name")).toHaveText("demo.live-compare");
    await expect(page.getByTestId("span-details")).toContainText("Not an inference span: no usage row.");
  });
}

// ---------------------------------------------------------------- A: Overview
test("Overview: counts, tokens and cost completeness match the backend; unknown cost is never $0 and synthetic is never verified", async ({ page, request }) => {
  await page.goto(u("/", C));
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  const w = await pageWindow(page);
  const o = await api(request, `/api/v1/overview?${q(w)}`);
  expect(o.traces.count.total).toBe(8); // 7 traces with one inference attempt each, 1 root-only (R131)
  expect(o.inference.attempts.total).toBe(7);
  expect(o.traces.count.by_provenance).toEqual({ real: 0, synthetic: 7, mixed: 0, unknown: 1 });
  expect(o.inference.attempts.by_provenance).toEqual({ real: 0, synthetic: 7, unknown: 0 });
  const tok = o.inference.tokens.total;
  expect([tok.input_tokens, tok.output_tokens]).toEqual([1312, 474]);
  expect([tok.calls_with_unknown_input_tokens, tok.calls_with_unknown_output_tokens]).toEqual([1, 1]);
  const cost = o.inference.cost.total;
  expect([cost.known_cost_usd, cost.computed_cost_calls, cost.unknown_cost_calls, cost.cost_complete]).toEqual([OPENAI_COST, 1, 6, false]);
  expect(cost.unknown_cost_by_reason).toEqual({ price_missing: 5, usage_missing: 1, usage_inconsistent: 0 });
  expect(o.provenance_verified).toBe(false);

  await expect(page.getByTestId("traces-value")).toHaveText(formatInt(o.traces.count.total));
  await expect(page.getByTestId("inference-attempts-value")).toHaveText(formatInt(o.inference.attempts.total));
  await expect(page.getByTestId("tokens-value")).toContainText(formatInt(1312));
  await expect(page.getByTestId("tokens-value")).toContainText(formatInt(474));
  await expect(page.locator('[data-metric="tokens"]')).toContainText("Not reported: input by 1, output by 1 of 7 attempts");
  await expect(page.getByTestId("known-cost-value")).toHaveAttribute("data-state", "known");
  await expect(page.getByTestId("known-cost-value")).toContainText(formatUsd(OPENAI_COST)!.text);
  await expect(page.locator('[data-metric="known-cost"]')).toContainText("Incomplete: 6 of 7 unpriced");
  await expect(page.locator('[data-metric="known-cost"]')).toContainText("Priced 1 of 7 attempts · unknown cost 6");
  await expect(page.getByTestId("table-model-groups").locator("tbody tr")).toHaveCount(7);

  const notice = page.getByTestId("data-notice");
  await expect(notice).toHaveAttribute("data-notice", "contains_synthetic");
  await expect(notice).toContainText("not verified");
  await expect(notice.locator('[data-provenance="real"]')).toContainText("0");
  await expect(page.locator("main")).not.toContainText(/verified real|provenance verified/i);
  await shot(page, "8a6-02-overview");

  // Groq only: the backend applies the filter; no Groq call is priced, so the cost is unknown, never $0.
  await page.goto(u("/?provider=groq", C));
  const g = await api(request, `/api/v1/overview?${q(await pageWindow(page), { provider: "groq" })}`);
  expect(g.filters.provider).toBe("groq");
  expect([g.traces.count.total, g.inference.attempts.total]).toEqual([3, 3]);
  expect([g.inference.tokens.total.input_tokens, g.inference.tokens.total.output_tokens]).toEqual([74, 136]);
  expect([g.inference.cost.total.known_cost_usd, g.inference.cost.total.unknown_cost_by_reason.price_missing]).toEqual([null, 3]);
  await expect(page.getByTestId("inference-attempts-value")).toHaveText("3");
  await expect(page.getByTestId("traces-value")).toHaveText("3");
  await expect(page.getByTestId("known-cost-value")).toHaveAttribute("data-state", "unknown");
  await expect(page.getByTestId("known-cost-value")).toHaveText("Unknown: no call priced");
  await expect(page.locator('[data-metric="known-cost"]')).toContainText("Incomplete: 3 of 3 unpriced");
  await expect(page.getByTestId("table-model-groups")).not.toContainText(ZERO_USD);

  // The other unpriced providers, one at a time.
  for (const provider of ["gcp.gemini", "openrouter"]) {
    await page.goto(u(`/?provider=${provider}`, C));
    await expect(page.getByTestId("inference-attempts-value")).toHaveText("1");
    await expect(page.getByTestId("known-cost-value")).toHaveAttribute("data-state", "unknown");
    await expect(page.getByTestId("known-cost-value")).toHaveText("Unknown: no call priced");
    await expect(page.getByTestId("table-model-groups")).not.toContainText(ZERO_USD);
  }
});

// ---------------------------------------------------------------- C: Models / Compare
test("Models / Compare: one row per provider, model and scenario; unknown cost, low sample and no ranking", async ({ page, request }) => {
  await page.goto(u("/models", C));
  await expect(page.getByRole("heading", { level: 1, name: "Model comparison" })).toBeVisible();
  const w = await pageWindow(page);
  const data = await api(request, `/api/v1/models/compare?${q(w, { limit: "50" })}`);
  expect(data.groups).toHaveLength(7);
  const key = (g: any) => [g.provider, g.requested_model, g.response_model, g.prompt_version];
  const groq = data.groups.filter((g: any) => g.provider === "groq");
  expect(groq.map((g: any) => g.prompt_version).sort()).toEqual(SCENARIOS.map((s) => s.version).sort());
  for (const s of SCENARIOS) {
    const g = groq.find((x: any) => x.prompt_version === s.version);
    expect(key(g)).toEqual(["groq", GROQ_MODEL, GROQ_MODEL, s.version]);
    expect([g.attempts, g.tokens.input_tokens, g.tokens.output_tokens]).toEqual([1, ...s.tokens]);
    expect([g.cost.known_cost_usd, g.cost.cost_complete, g.cost.unknown_cost_by_reason.price_missing]).toEqual([null, false, 1]);
    expect(g.by_provenance).toEqual({ real: 0, synthetic: 1, unknown: 0 });
  }
  const openrouter = data.groups.filter((g: any) => g.provider === "openrouter");
  expect(openrouter.map(key)).toEqual([["openrouter", OPENROUTER_MODEL, OPENROUTER_MODEL, "cmp-v1.ticket-triage"]]); // its own row, never merged with Groq's
  const openai = data.groups.find((g: any) => g.provider === "openai");
  expect(key(openai)).toEqual(["openai", "gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "v1"]);
  expect([openai.cost.known_cost_usd, openai.cost.cost_complete]).toEqual([OPENAI_COST, true]);
  expect(key(data.groups.find((g: any) => g.provider === "gcp.gemini"))).toEqual(["gcp.gemini", "gemini-3.1-flash-lite", "gemini-3.1-flash-lite", "v1"]);
  const unknown = data.groups.find((g: any) => g.provider === "unknown"); // the adversarial span: provider and model dropped
  expect(key(unknown)).toEqual(["unknown", null, null, null]);
  expect([unknown.tokens.input_tokens, unknown.cost.unknown_cost_by_reason.usage_missing]).toEqual([null, 1]);
  for (const g of data.groups) {
    expect(g.attempts).toBe(1);
    expect(g.latency.low_sample).toEqual({ p95: true, p99: true });
  }

  const rows = groupRows(page);
  await expect(rows).toHaveCount(data.groups.length);
  for (const [i, g] of data.groups.entries()) {
    const row = rows.nth(i);
    const name = row.getByTestId("group-name");
    await expect(name).toContainText(g.provider);
    if (g.requested_model) await expect(name).toContainText(g.requested_model);
    if (g.prompt_version) await expect(name).toContainText(`Prompt version ${g.prompt_version}`);
    await expect(row.getByTestId("group-attempts")).toHaveText("1");
    await expect(row.getByTestId("group-latency")).toContainText("low sample for p95");
    await expect(row.getByTestId("group-latency")).toContainText("(low sample)");
    const cost = row.getByTestId("group-cost");
    if (g.cost.known_cost_usd === null) {
      await expect(cost).toHaveAttribute("data-cost-state", "unknown");
      await expect(cost).not.toContainText("$");
    } else {
      await expect(cost.locator("[data-exact]")).toHaveAttribute("data-exact", OPENAI_COST);
    }
    const tokens = row.getByTestId("group-tokens");
    if (g.tokens.input_tokens === null) await expect(tokens).toContainText("unknown");
    else await expect(tokens).toContainText(`${formatInt(g.tokens.input_tokens)} / ${formatInt(g.tokens.output_tokens)}`);
  }
  for (const s of SCENARIOS) await expect(rows.filter({ hasText: "groq" }).filter({ hasText: s.version })).toHaveCount(1);
  await expect(rows.filter({ hasText: "cmp-v1.ticket-triage" })).toHaveCount(2); // Groq and the synthetic OpenRouter row

  await expect(page.getByTestId("compare-synthetic")).toContainText("SYNTHETIC data only");
  const notRanked = page.getByTestId("compare-not-ranked");
  await expect(notRanked).toContainText("no group is presented as the best");
  await expect(notRanked).toContainText("▲ marks a p95 from fewer than 20 samples");
  await expect(notRanked).toContainText("Unknown cost is never counted as $0");
  await expect(page.locator("main")).not.toContainText(/best model|winner|recommended|ranked first/i);
  await expect(page.getByTestId("compare-table")).not.toContainText(ZERO_USD);
  await shot(page, "8a6-03-models");

  // Provider and model filters are applied by the backend.
  await page.goto(u("/models?provider=groq", C));
  await expect(groupRows(page)).toHaveCount(3);
  await expect(page.getByTestId("compare-table")).not.toContainText(/openrouter|gcp\.gemini|gpt-5\.4-mini/);
  await page.goto(u(`/models?${new URLSearchParams({ provider: "groq", model: GROQ_MODEL })}`, C));
  await expect(groupRows(page)).toHaveCount(3);
  await page.goto(u("/models?provider=openrouter", C));
  await expect(groupRows(page)).toHaveCount(1);
  await expect(groupRows(page)).toContainText(OPENROUTER_MODEL);
  await expect(groupRows(page)).toContainText("cmp-v1.ticket-triage");
  await page.goto(u("/models?model=gpt-5.4-mini", C));
  await expect(groupRows(page)).toHaveCount(1);
  await expect(groupRows(page).getByTestId("group-cost").locator("[data-exact]")).toHaveAttribute("data-exact", OPENAI_COST);

  // R132: the backend filters Compare by prompt version, but no dashboard page sends that
  // parameter, so the page shows every group.
  const one = await api(request, `/api/v1/models/compare?${q(w, { limit: "50", prompt_version: "cmp-v1.json-aggregate" })}`);
  expect(one.groups.map(key)).toEqual([["groq", GROQ_MODEL, GROQ_MODEL, "cmp-v1.json-aggregate"]]);
  await page.goto(u("/models?prompt_version=cmp-v1.json-aggregate", C));
  await expect(groupRows(page)).toHaveCount(7);
});

// ---------------------------------------------------------------- D: R131 root-only refusal trace
test("R131: a root-only demo.live-compare trace counts as a trace but adds no attempt, tokens, cost or Compare row", async ({ page, request }) => {
  const { w, items, by } = await fixtures(page, request);
  const t = by(ROOT_ONLY);
  expect([t.span_count, t.usage.inference_calls, t.usage.known_cost_usd, t.usage.unknown_cost_calls]).toEqual([1, 0, null, 0]);
  expect([t.prompt_version, t.provenance, t.status]).toEqual([null, "unknown", "ok"]);
  const row = rowOf(page, t.trace_id);
  await expect(row.locator("[data-cost-state]")).toHaveCount(0);
  await expect(row.getByTestId("trace-models")).toHaveCount(0);

  const d = await api(request, `/api/v1/traces/${t.trace_id}`);
  expect(d.spans.map((s: any) => [s.name, s.kind, s.parent_span_id])).toEqual([["demo.live-compare", "INTERNAL", null]]);
  expect(Object.keys(d.spans[0].attributes).filter((k) => k.startsWith("gen_ai."))).toEqual([]);
  expect(d.usage).toEqual([]);
  await page.goto(u(`/traces/${t.trace_id}`, C));
  await expect(page.getByRole("treeitem")).toHaveCount(1);
  await expect(page.getByTestId("fact-tokens")).toContainText("0 attempts");
  await expect(page.getByTestId("fact-cost").locator("[data-cost-state]")).toHaveCount(0);
  await expect(page.getByTestId("status-calls")).toContainText("no inference attempt in this trace.");
  await expect(page.getByTestId("table-attempts")).toHaveCount(0);
  await expect(page.getByTestId("trace-provenance-notice")).toHaveAttribute("data-provenance", "unknown");
  await expect(page.getByTestId("span-details")).toContainText("Not an inference span: no usage row.");

  // Traces 8, attempts 7: the root-only trace is the difference, and it creates no inference.
  const o = await api(request, `/api/v1/overview?${q(w)}`);
  const calls = items.reduce((n: number, x: any) => n + x.usage.inference_calls, 0);
  expect([o.traces.count.total, o.inference.attempts.total, calls]).toEqual([items.length, 7, 7]);
  expect(o.inference.cost.total.n).toBe(7);
  expect(o.inference.cost.total.computed_cost_calls + o.inference.cost.total.unknown_cost_calls).toBe(7);
  const cmp = await api(request, `/api/v1/models/compare?${q(w, { limit: "50" })}`);
  expect(cmp.groups.reduce((n: number, g: any) => n + g.attempts, 0)).toBe(7);
  expect(cmp.groups.every((g: any) => g.attempts > 0)).toBe(true);
  // The only group without a prompt version is the adversarial span's (provider dropped), never the root.
  expect(cmp.groups.filter((g: any) => g.prompt_version === null).map((g: any) => [g.provider, g.attempts])).toEqual([["unknown", 1]]);
});

// ---------------------------------------------------------------- E: isolation and authentication
test("isolation: comparison telemetry never appears in another project, and the API needs the token", async ({ page, request }) => {
  const { w, items, by } = await fixtures(page, request);
  const ids = items.map((t: any) => t.trace_id);
  const groq = by(SCENARIOS[0].service);
  const missing = await rawText(request, `/api/v1/traces/${"a".repeat(32)}`, FOREIGN_PROJECT);
  for (const other of [FOREIGN_PROJECT, PROJECT, PROVIDERS_PROJECT]) {
    const r = await rawText(request, `/api/v1/traces/${groq.trace_id}`, other);
    expect([r.status, r.text]).toEqual([404, missing.text]); // indistinguishable from a missing trace
  }
  const foreignList = await api(request, `/api/v1/traces?${q(w, { limit: "200" })}`, FOREIGN_PROJECT);
  expect(foreignList.items.filter((t: any) => ids.includes(t.trace_id))).toEqual([]);
  const foreignGroq = await api(request, `/api/v1/overview?${q(w, { provider: "groq" })}`, FOREIGN_PROJECT);
  expect([foreignGroq.traces.count.total, foreignGroq.inference.attempts.total]).toEqual([0, 0]);
  const foreignCompare = await api(request, `/api/v1/models/compare?${q(w, { limit: "50" })}`, FOREIGN_PROJECT);
  expect(foreignCompare.groups.filter((g: any) => ["groq", "openrouter"].includes(g.provider) || String(g.prompt_version).startsWith("cmp-v1."))).toEqual([]);

  await page.goto(u(`/traces/${groq.trace_id}`, FOREIGN_PROJECT));
  await expect(page.getByTestId("trace-not-found")).toBeVisible();
  await expect(page.getByRole("treeitem")).toHaveCount(0);
  await page.goto(u("/traces", FOREIGN_PROJECT));
  await expect(page.getByTestId("trace-table")).toBeVisible();
  const html = await page.content();
  for (const id of ids) expect(html.includes(id)).toBe(false);
  await page.goto(u("/models", FOREIGN_PROJECT));
  await expect(page.locator("main")).not.toContainText(/cmp-v1\.|openrouter|gpt-oss-20b/);

  // The backend refuses the read API without the server-side token.
  await new Promise((r) => setTimeout(r, 150));
  const anonymous = await request.get(`${API}/api/v1/overview?project=${C}&${q(w)}`);
  expect(anonymous.status()).toBe(401);
});

// ---------------------------------------------------------------- E: hostile labels, content and credentials
test("adversarial trace: hostile labels render as text; dropped content, reasoning and key never reach the browser", async ({ page, request }) => {
  const dialogs: string[] = [];
  const bodies: string[] = [];
  const problems: string[] = [];
  page.on("dialog", (dlg) => {
    dialogs.push(dlg.message());
    void dlg.dismiss();
  });
  page.on("response", async (res) => {
    try {
      bodies.push(await res.text());
    } catch {
      /* redirects and aborted responses have no body */
    }
  });
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text().slice(0, 200)}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 200)}`));

  const { w, by } = await fixtures(page, request);
  await inert(page);
  const adv = by(ADVERSARIAL);
  await expect(rowOf(page, adv.trace_id).getByTestId("trace-link")).toContainText(ADVERSARIAL);
  const d = await api(request, `/api/v1/traces/${adv.trace_id}`);
  const root = d.spans.find((x: any) => x.role === "root");
  const client = d.spans.find((x: any) => x.kind === "CLIENT");
  expect(root.name).toBe(`<script>${XSS}</script>`);
  expect(client.server_dropped_attributes_count).toBe(DROPPED_KEYS.length);
  for (const k of DROPPED_KEYS) expect(Object.keys(client.attributes)).not.toContain(k);
  expect([d.usage[0].provider, d.usage[0].requested_model, d.usage[0].input_tokens, d.usage[0].cost_unknown_reason]).toEqual(["unknown", null, null, "usage_missing"]);

  await page.goto(u(`/traces/${adv.trace_id}?span=${client.span_id}`, C));
  await expect(page.getByTestId("fact-service")).toHaveText(ADVERSARIAL);
  await expect(page.getByTestId("span-details-name")).toHaveText(`chat <img src=x onerror=${XSS}>`);
  await expect(page.getByTestId("span-details")).toContainText(`Dropped by server${DROPPED_KEYS.length}`);
  await expect(page.getByTestId("span-cost")).toHaveText("Unknown: usage not reported");
  for (const k of DROPPED_KEYS) await expect(page.getByTestId("span-attributes")).not.toContainText(k);
  await inert(page);
  await page.goto(u(`/traces/${adv.trace_id}?span=${root.span_id}`, C));
  await expect(page.getByTestId("span-details-name")).toHaveText(`<script>${XSS}</script>`);
  await inert(page);
  for (const p of ["/models", "/"]) {
    await page.goto(u(p, C));
    await expect(page.locator("h1")).toBeVisible();
    await page.waitForLoadState("networkidle");
    await inert(page);
  }
  expect(dialogs).toEqual([]);

  // Nothing the server dropped, and no credential, reaches any browser response or the API.
  const direct = [
    await rawText(request, `/api/v1/traces?${q(w, { limit: "100" })}`),
    await rawText(request, `/api/v1/traces/${adv.trace_id}`),
    await rawText(request, `/api/v1/overview?${q(w)}`),
    await rawText(request, `/api/v1/models/compare?${q(w, { limit: "50" })}`),
  ];
  expect(direct.map((r) => r.status)).toEqual([200, 200, 200, 200]);
  expect(bodies.length).toBeGreaterThan(3);
  for (const text of [...bodies, ...direct.map((r) => r.text)]) {
    for (const secret of NEVER_SHOWN) expect(text.includes(secret), secret.slice(0, 20)).toBe(false);
    expect(text.includes(TOKEN)).toBe(false);
  }
  expect(bodies.filter((b) => CREDENTIAL_TEXT.test(b))).toEqual([]);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + document.cookie);
  expect(storage.includes(TOKEN)).toBe(false);
  expect(problems).toEqual([]); // no console error, CSP violation or hydration warning
});
