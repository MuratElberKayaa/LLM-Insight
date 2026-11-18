/**
 * UI-4 (trace detail): the Timeline/Tree switch, the per-row duration, tokens and cost columns
 * (unknown cost stays "unknown", never $0) and the one-strip trace facts.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { TraceFacts } from "../../components/traces/trace-detail-sections";
import { Waterfall } from "../../components/traces/waterfall";
import { buildTraceView } from "../../lib/span-tree";
import { makeDetail, makeSpan, makeUsage, retryDetail } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const count = (m: string, re: RegExp) => (m.match(re) ?? []).length;

describe("waterfall view switch", () => {
  it("defaults to Timeline: bars on the trace axis, Timeline pressed", () => {
    const view = buildTraceView(retryDetail());
    const m = html(<Waterfall view={view} initialSelected={null} />);
    assert.match(m, /role="group" aria-label="Span view"/);
    assert.match(m, /aria-pressed="true"[^>]*data-waterfall-mode="timeline"/);
    assert.match(m, /aria-pressed="false"[^>]*data-waterfall-mode="tree"/);
    assert.equal(count(m, /data-testid="span-bar"/g), view.rows.length);
    assert.equal(count(m, /data-testid="span-offset-cell"/g), 0);
  });

  it("Tree replaces the bars with the start offset, keeping every row and the ARIA tree", () => {
    const view = buildTraceView(retryDetail());
    const m = html(<Waterfall view={view} initialSelected={null} initialMode="tree" />);
    assert.equal(count(m, /data-testid="span-bar"/g), 0);
    assert.equal(count(m, /data-testid="span-offset-cell"/g), view.rows.length);
    assert.equal(count(m, /role="treeitem"/g), view.rows.length);
    assert.match(m, /Start offset/);
    // The offset is shown exactly as built (already signed), never with a doubled sign.
    assert.match(view.rows[0]!.offsetLabel, /^\+/);
    assert.match(m, new RegExp(`<span aria-hidden="true">${view.rows[0]!.offsetLabel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</span>`));
    assert.doesNotMatch(m, /\+\+/);
  });
});

describe("waterfall row columns", () => {
  it("adds duration, tokens and cost headers", () => {
    const m = html(<Waterfall view={buildTraceView(retryDetail())} initialSelected={null} />);
    for (const h of ["Duration", "Tokens in / out", "Cost"]) assert.match(m, new RegExp(`>${h}<`));
  });

  it("shows tokens and cost only on inference rows, with unknown cost as unknown", () => {
    const spans = [
      makeSpan({ id: 1, start: 0n, end: 1000n }),
      makeSpan({ id: 2, parent: 1, start: 10n, end: 500n, kind: "CLIENT" }),
      makeSpan({ id: 3, parent: 1, start: 600n, end: 900n, kind: "CLIENT" }),
    ];
    const usage = [makeUsage(2, { cost_status: "unknown", cost_usd: null, cost_unknown_reason: "price_missing" }), makeUsage(3)];
    const view = buildTraceView(makeDetail(spans, usage, {}, 1000n));
    const m = html(<Waterfall view={view} initialSelected={null} />);
    const inference = view.rows.filter((r) => r.inference !== null).length;
    assert.equal(inference, 2);
    assert.equal(count(m, /data-testid="span-tokens"/g), inference);
    assert.equal(count(m, /data-testid="span-row-cost"/g), inference);
    assert.match(m, /data-testid="span-row-cost" data-cost-state="unknown"><span class="sr-only">cost <\/span>unknown</);
    const rowCosts = [...m.matchAll(/data-testid="span-row-cost"[^>]*>(.*?)<\/span><\/span>/g)].map((x) => x[1] ?? "");
    for (const c of rowCosts) if (c.includes("unknown")) assert.doesNotMatch(c, /\$0/);
  });
});

describe("trace facts strip", () => {
  it("keeps the ten facts with their test ids in one hairline-divided strip", () => {
    const d = retryDetail();
    const m = html(<TraceFacts trace={d.trace} returnedSpans={d.spans.length} spansTruncated={false} />);
    assert.match(m, /data-testid="trace-facts"/);
    assert.match(m, /gap-px[^"]*bg-line/);
    for (const id of ["status", "service", "duration", "spans", "started", "ended", "tokens", "cost", "prompt", "provenance"]) {
      assert.match(m, new RegExp(`data-testid="fact-${id}"`), id);
    }
  });
});
