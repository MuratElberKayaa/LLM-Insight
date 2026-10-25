"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { COST_REASON_LABEL, PLACEMENT_LABEL, formatDigits, type SpanView, type TraceView } from "../../lib/span-tree";
import { ProvenanceBadge } from "../provenance-badge";
import { StatusIndicator } from "../status-indicator";

/**
 * Span hierarchy with a relative-time waterfall (WAI-ARIA tree pattern) and the selected
 * span's stored metadata. Everything shown is precomputed on the server from the backend
 * response (lib/span-tree.ts); this component only adds selection, expand/collapse,
 * keyboard navigation and bounded rendering.
 */

export const ROW_BATCH = 200;
const MAX_INDENT = 12;

const KIND_SHORT: Record<SpanView["kind"], string> = {
  SERVER: "SRV",
  CLIENT: "CLI",
  INTERNAL: "INT",
  PRODUCER: "PRD",
  CONSUMER: "CON",
  UNSPECIFIED: "—",
};

function barClass(row: SpanView): string {
  if (row.status === "ERROR") return "bg-critical";
  return row.inference ? "bg-series-1" : "bg-unknown";
}

/** Rows hidden under a collapsed ancestor are skipped (rows arrive in depth-first order). */
function visibleRows(rows: SpanView[], collapsed: ReadonlySet<string>): SpanView[] {
  const out: SpanView[] = [];
  let hideBelow: number | null = null;
  for (const row of rows) {
    if (hideBelow !== null) {
      if (row.depth > hideBelow) continue;
      hideBelow = null;
    }
    out.push(row);
    if (collapsed.has(row.spanId) && row.childIds.length > 0) hideBelow = row.depth;
  }
  return out;
}

function Legend() {
  const item = (cls: string, label: string, glyph?: string) => (
    <li className="flex items-center gap-1.5">
      <span aria-hidden="true" className={`swatch inline-block h-2.5 w-4 rounded-[2px] ${cls}`} />
      {glyph && <span aria-hidden="true" className="text-critical-ink">{glyph}</span>}
      {label}
    </li>
  );
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2" aria-label="Legend" data-testid="waterfall-legend">
      {item("bg-unknown", "Application span")}
      {item("bg-series-1", "Inference attempt (one physical provider call)")}
      {item("bg-critical", "Span with status ERROR", "✕")}
    </ul>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-3">{label}</dt>
      <dd className="break-words text-sm text-ink-2">{children}</dd>
    </div>
  );
}

const Unknown = ({ label = "not reported" }: { label?: string }) => (
  <span className="text-ink-3">
    unknown<span className="sr-only"> ({label})</span>
  </span>
);
const Mono = ({ v }: { v: string }) => <code className="font-mono text-xs break-all text-ink">{v}</code>;
const tokens = (v: string | null) => (v === null ? <Unknown /> : <span className="tabular">{formatDigits(v)}</span>);

function AttrList({ title, items, testId }: { title: string; items: [string, string][]; testId: string }) {
  return (
    <div className="mt-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-3">{title}</h4>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-ink-3">None stored.</p>
      ) : (
        <dl className="mt-1 space-y-1" data-testid={testId}>
          {items.map(([k, v]) => (
            <div key={k} className="grid grid-cols-1 gap-x-3 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]">
              <dt className="font-mono text-xs break-all text-ink-3">{k}</dt>
              <dd className="font-mono text-xs break-all text-ink-2">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function SpanDetails({ row }: { row: SpanView }) {
  const inf = row.inference;
  return (
    <div data-testid="span-details" data-span-id={row.spanId}>
      <h3 className="break-words text-base font-semibold text-ink" data-testid="span-details-name">
        {row.name}
      </h3>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        {row.status === "ERROR" ? (
          <StatusIndicator tone="critical" label="Status ERROR" />
        ) : (
          <StatusIndicator tone={row.status === "OK" ? "good" : "neutral"} label={row.status === "OK" ? "Status OK" : "Status UNSET (no error recorded)"} />
        )}
        {inf ? <ProvenanceBadge provenance={row.provenance} emphasis={row.provenance === "synthetic"} /> : null}
      </div>
      {row.status === "ERROR" && (
        <p className="mt-2 text-xs text-ink-3" data-testid="span-error-note">
          Recorded <span className="font-mono">error.type</span>: {row.errorType === null ? <Unknown label="not recorded" /> : <Mono v={row.errorType} />}. This is what
          the instrumentation reported; LLM Insight does not infer a cause.
        </p>
      )}
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <Field label="Span ID">
          <Mono v={row.spanId} />
        </Field>
        <Field label="Parent span ID">{row.parentSpanId === null ? <span className="text-ink-3">none (root)</span> : <Mono v={row.parentSpanId} />}</Field>
        <Field label="Position">{PLACEMENT_LABEL[row.placement]}</Field>
        <Field label="Kind">{row.kind}</Field>
        <Field label="Offset from trace start">
          <span className="tabular" data-testid="span-offset">
            {row.offsetLabel}
          </span>
        </Field>
        <Field label="Duration">
          <span className="tabular" title={`${row.durationNs} ns`} data-testid="span-duration">
            {row.durationLabel}
          </span>
        </Field>
        <div className="col-span-2">
          <Field label="Start (UTC)">
            <span className="tabular" data-testid="span-start" data-ns={row.startNs}>
              {row.startLabel}
            </span>
          </Field>
        </div>
        <div className="col-span-2">
          <Field label="End (UTC)">
            <span className="tabular" data-testid="span-end">
              {row.endLabel}
            </span>
          </Field>
        </div>
      </dl>

      {inf ? (
        <div className="mt-4 rounded-md border border-line bg-raised/40 px-3 py-3" data-testid="span-inference">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-3">Inference attempt (usage ledger)</h4>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
            <Field label="Provider">
              <Mono v={inf.provider} />
            </Field>
            <Field label="Operation">{inf.operation}</Field>
            <Field label="Requested model">{inf.requestedModel === null ? <Unknown /> : <Mono v={inf.requestedModel} />}</Field>
            <Field label="Response model">{inf.responseModel === null ? <Unknown /> : <Mono v={inf.responseModel} />}</Field>
            <Field label="Attempt">{inf.attemptId === null ? <Unknown /> : inf.attemptId}</Field>
            <Field label="Logical call ID">{inf.callId === null ? <Unknown /> : <Mono v={inf.callId} />}</Field>
            <div className="col-span-2">
              <Field label="Provider response ID">{inf.responseId === null ? <Unknown /> : <Mono v={inf.responseId} />}</Field>
            </div>
            <Field label="Input tokens">{tokens(inf.inputTokens)}</Field>
            <Field label="Output tokens">{tokens(inf.outputTokens)}</Field>
            <Field label="Cached input (subset)">{tokens(inf.cachedInputTokens)}</Field>
            <Field label="Cache write (subset)">{tokens(inf.cacheWriteInputTokens)}</Field>
            <Field label="Reasoning output (subset)">{tokens(inf.reasoningOutputTokens)}</Field>
            <Field label="Usage source">{inf.usageSource.replace("_", " ")}</Field>
            <Field label="Cost">
              {inf.cost.kind === "computed" ? (
                <span title={`Exact: ${inf.cost.exact}`} data-testid="span-cost" data-cost-state="computed">
                  {inf.cost.exact}
                </span>
              ) : (
                <span data-testid="span-cost" data-cost-state="unknown">
                  Unknown: {COST_REASON_LABEL[inf.cost.reason]}
                </span>
              )}
            </Field>
            <Field label="Price version">{inf.priceVersion === null ? <span className="text-ink-3">none</span> : <Mono v={inf.priceVersion} />}</Field>
            <div className="col-span-2">
              <Field label="Call identity source">{inf.callKeySource.replaceAll("_", " ")}</Field>
            </div>
          </dl>
          <p className="mt-2 text-xs text-ink-3">Subsets are part of the input/output totals and are never added to them. Cost is computed once at ingest from provider-reported usage.</p>
        </div>
      ) : (
        <p className="mt-4 text-xs text-ink-3">Not an inference span: no usage row. Provenance applies to inference spans only.</p>
      )}

      <AttrList title="Span attributes (allowlisted)" items={row.attributes} testId="span-attributes" />
      <AttrList title="Resource attributes (allowlisted)" items={row.resourceAttributes} testId="span-resource" />
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2">
        <Field label="Scope">{row.scopeName === null ? <span className="text-ink-3">none</span> : `${row.scopeName}${row.scopeVersion ? ` ${row.scopeVersion}` : ""}`}</Field>
        <Field label="Dropped by server">{row.droppedAttributes}</Field>
        <div className="col-span-2">
          <Field label="Attribute map">
            <Mono v={row.schemaVersion} />
          </Field>
        </div>
      </dl>
      <p className="mt-4 text-xs text-ink-3">Prompt and completion content is not captured or stored, so it cannot be shown.</p>
    </div>
  );
}

export function Waterfall({ view, initialSelected }: { view: TraceView; initialSelected: string | null }) {
  const { rows, axis } = view;
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [selected, setSelected] = useState<string | null>(() => (initialSelected !== null && rows.some((r) => r.spanId === initialSelected) ? initialSelected : (rows[0]?.spanId ?? null)));
  const [limit, setLimit] = useState(ROW_BATCH);
  const focusPending = useRef(false);
  const rowRefs = useRef(new Map<string, HTMLDivElement>());

  const visible = useMemo(() => visibleRows(rows, collapsed), [rows, collapsed]);
  const shown = visible.slice(0, limit);
  const byId = useMemo(() => new Map(rows.map((r) => [r.spanId, r])), [rows]);
  // aria-setsize / aria-posinset among siblings (rows of one parent, or top-level rows).
  const siblings = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const r of rows) {
      const key = r.depth === 0 ? "" : (r.parentSpanId ?? "");
      groups.set(key, [...(groups.get(key) ?? []), r.spanId]);
    }
    const pos = new Map<string, { size: number; index: number }>();
    for (const list of groups.values()) list.forEach((id, i) => pos.set(id, { size: list.length, index: i + 1 }));
    return pos;
  }, [rows]);
  const selectedRow = (selected !== null ? byId.get(selected) : undefined) ?? shown[0];
  // One tab stop: the selected row, or the first row when the selection is collapsed away.
  const tabStop = shown.some((r) => r.spanId === selectedRow?.spanId) ? selectedRow?.spanId : shown[0]?.spanId;

  useEffect(() => {
    if (!focusPending.current || selected === null) return;
    focusPending.current = false;
    rowRefs.current.get(selected)?.focus();
  }, [selected, shown.length]);

  const select = (id: string, focus: boolean) => {
    focusPending.current = focus;
    setSelected(id);
    // Shareable selection without a navigation (Next.js syncs native history updates).
    const url = new URL(window.location.href);
    url.searchParams.set("span", id);
    window.history.replaceState(window.history.state, "", url);
  };

  const toggle = (id: string, open?: boolean) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      const isOpen = !next.has(id);
      if (open ?? !isOpen) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>, row: SpanView) => {
    const i = shown.findIndex((r) => r.spanId === row.spanId);
    const go = (target: SpanView | undefined) => {
      if (!target) return;
      e.preventDefault();
      select(target.spanId, true);
    };
    const hasKids = row.childIds.length > 0;
    const isOpen = hasKids && !collapsed.has(row.spanId);
    switch (e.key) {
      case "ArrowDown":
        return go(shown[i + 1]);
      case "ArrowUp":
        return go(shown[i - 1]);
      case "Home":
        return go(shown[0]);
      case "End":
        return go(shown[shown.length - 1]);
      case "ArrowRight":
        e.preventDefault();
        if (hasKids && !isOpen) toggle(row.spanId, true);
        else if (isOpen) go(shown[i + 1]);
        return;
      case "ArrowLeft":
        e.preventDefault();
        if (isOpen) toggle(row.spanId, false);
        else if (row.depth > 0 && row.parentSpanId !== null) go(byId.get(row.parentSpanId));
        return;
      case "Enter":
      case " ":
        e.preventDefault();
        if (hasKids) toggle(row.spanId);
        return;
    }
  };

  if (rows.length === 0) {
    return <p className="text-sm text-ink-3">The backend returned no spans for this trace.</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <Legend />
          <p className="text-xs text-ink-3">
            ↑ ↓ move · → ← expand / collapse · Enter toggles
          </p>
        </div>
        <div className="rounded-lg border border-line bg-surface">
          {/* Time axis: the trace's stored start to end. */}
          <div className="grid grid-cols-1 border-b border-line px-3 py-2 text-xs text-ink-3 sm:grid-cols-[minmax(10rem,38%)_minmax(0,1fr)] sm:gap-3">
            <span className="hidden sm:block">Span</span>
            <div className="relative h-4" aria-hidden="true">
              {axis.ticks.map((t) => (
                <span
                  key={t.pct}
                  // Quarter ticks only where there is room for five labels.
                  className={`tabular absolute top-0 whitespace-nowrap ${t.pct === 25 || t.pct === 75 ? "hidden md:inline" : ""}`}
                  style={t.pct === 100 ? { right: 0 } : t.pct === 0 ? { left: 0 } : { left: `${t.pct}%`, transform: "translateX(-50%)" }}
                >
                  {t.label}
                </span>
              ))}
            </div>
          </div>
          <div role="tree" aria-label="Span hierarchy and timeline" data-testid="span-tree" className="py-1">
            {shown.map((row) => {
              const isSelected = selectedRow?.spanId === row.spanId;
              const hasKids = row.childIds.length > 0;
              const isOpen = hasKids && !collapsed.has(row.spanId);
              const pos = siblings.get(row.spanId);
              return (
                <div
                  key={row.spanId}
                  ref={(el) => {
                    if (el) rowRefs.current.set(row.spanId, el);
                    else rowRefs.current.delete(row.spanId);
                  }}
                  role="treeitem"
                  aria-level={row.depth + 1}
                  aria-selected={isSelected}
                  aria-expanded={hasKids ? isOpen : undefined}
                  aria-setsize={pos?.size}
                  aria-posinset={pos?.index}
                  tabIndex={row.spanId === tabStop ? 0 : -1}
                  onClick={() => select(row.spanId, false)}
                  onKeyDown={(e) => onKeyDown(e, row)}
                  data-span-id={row.spanId}
                  data-depth={row.depth}
                  data-placement={row.placement}
                  className="grid cursor-pointer grid-cols-1 gap-1 px-3 py-1.5 outline-offset-[-2px] hover:bg-raised/60 aria-selected:bg-raised sm:grid-cols-[minmax(10rem,38%)_minmax(0,1fr)] sm:items-center sm:gap-3"
                >
                  <div className="flex min-w-0 items-center gap-1.5 text-sm" style={{ paddingLeft: `${Math.min(row.depth, MAX_INDENT) * 0.875}rem` }}>
                    {hasKids ? (
                      <button
                        type="button"
                        tabIndex={-1}
                        aria-hidden="true"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggle(row.spanId);
                        }}
                        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-3 hover:bg-raised hover:text-ink"
                      >
                        {isOpen ? "▾" : "▸"}
                      </button>
                    ) : (
                      <span aria-hidden="true" className="inline-block w-5 shrink-0" />
                    )}
                    <span className="shrink-0 rounded border border-line px-1 font-mono text-[10px] text-ink-3" title={row.kind}>
                      {KIND_SHORT[row.kind]}
                      <span className="sr-only"> {row.kind}</span>
                    </span>
                    {row.status === "ERROR" && (
                      <span className="shrink-0 text-critical-ink" title="Status ERROR">
                        ✕<span className="sr-only"> error</span>
                      </span>
                    )}
                    {/* The name gets the full line; tags go on a second line so they never squeeze it. */}
                    <span className="flex min-w-0 flex-col">
                      <span className="min-w-0 truncate text-ink" title={row.name} data-testid="span-name">
                        {row.name}
                      </span>
                      {(row.inference || (row.placement !== "root" && row.placement !== "child")) && (
                        <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-[10px] leading-4">
                          {row.inference && (
                            <span className="rounded bg-series-1/15 px-1 font-medium text-ink-2">
                              LLM{row.inference.attemptId !== null ? ` · attempt ${row.inference.attemptId}` : ""}
                            </span>
                          )}
                          {row.inference && row.provenance === "synthetic" && <span className="font-semibold uppercase tracking-wide text-ink-3">synthetic</span>}
                          {row.placement !== "root" && row.placement !== "child" && (
                            <span className="rounded border border-warning/50 px-1 text-ink-2" title={PLACEMENT_LABEL[row.placement]}>
                              {row.placement === "orphan" ? "orphan" : row.placement === "unreachable" ? "no root" : "parent cut"}
                            </span>
                          )}
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="relative h-5" title={`${row.name} · ${row.offsetLabel} · ${row.durationLabel}`}>
                    {axis.ticks.map((t) => (
                      <span key={t.pct} aria-hidden="true" className="absolute inset-y-0 w-px bg-grid" style={{ left: `${Math.min(t.pct, 99.9)}%` }} />
                    ))}
                    <span
                      aria-hidden="true"
                      data-testid="span-bar"
                      data-left={row.left}
                      data-width={row.width}
                      className={`swatch absolute top-1/2 h-2.5 -translate-y-1/2 rounded-[2px] ${barClass(row)}`}
                      style={{ left: `${row.left}%`, width: `${row.width}%`, minWidth: "2px" }}
                    />
                    <span className="sr-only">
                      starts {row.offsetLabel} after the trace start, lasts {row.durationLabel}
                      {row.inference ? ", inference attempt" : ""}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          {visible.length > shown.length && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-3 py-2 text-sm">
              <span className="text-ink-3" data-testid="rows-hidden">
                Showing {shown.length} of {visible.length} rows.
              </span>
              <button
                type="button"
                onClick={() => setLimit((n) => n + ROW_BATCH)}
                className="rounded-md border border-line px-3 py-1.5 text-ink-2 hover:bg-raised hover:text-ink"
                data-testid="show-more-rows"
              >
                Show {Math.min(ROW_BATCH, visible.length - shown.length)} more
              </button>
            </div>
          )}
        </div>
        <p className="mt-2 text-xs text-ink-3">
          Axis: trace start to trace end ({axis.durationLabel}). Bars use exact nanosecond offsets; a bar of zero length is drawn 2 px wide.
        </p>
      </div>
      <aside aria-label="Selected span" className="min-w-0 rounded-lg border border-line bg-surface px-4 py-4 xl:sticky xl:top-4 xl:max-h-[calc(100dvh-2rem)] xl:self-start xl:overflow-y-auto">
        {selectedRow ? <SpanDetails row={selectedRow} /> : <p className="text-sm text-ink-3">Select a span.</p>}
      </aside>
    </div>
  );
}
