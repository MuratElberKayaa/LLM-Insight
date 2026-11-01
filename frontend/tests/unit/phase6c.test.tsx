/**
 * Phase 6C-C: controlled refresh (timer lifecycle, cooldown, backoff, stale display), the
 * provider/model filters, asserted-real labels and the overview's latest-trace summary.
 * Pure modules, a fake clock and server-rendered components; SYNTHETIC fixtures only, no
 * network, no credential, no provider.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { autoRefreshStatus } from "../../components/auto-refresh";
import { ComparisonTable } from "../../components/models/model-comparison";
import { showsStale } from "../../components/live-region";
import { LatestTrace } from "../../components/overview/latest-trace";
import { ProvenanceBadge } from "../../components/provenance-badge";
import { ScopeFilterForm } from "../../components/scope-filter-form";
import type { ApiFailureKind, ApiResult } from "../../lib/api/result";
import { modelsCompareDecoder, overviewDecoder } from "../../lib/api/schema";
import type { TraceList } from "../../lib/api/trace-types";
import {
  AUTO_REFRESH_MAX_MS,
  DEFAULT_RATE_LIMIT_COOLDOWN_S,
  INITIAL_SNAPSHOT,
  MIN_INTERVAL_MS,
  RefreshController,
  backoffMs,
  decideRefresh,
  failureClass,
  renderOutcome,
  type RefreshRuntime,
  type RenderOutcome,
} from "../../lib/auto-refresh";
import { compareParams, parseModelQuery } from "../../lib/model-query";
import { PROVENANCE_DESCRIPTION, PROVENANCE_LABEL } from "../../lib/provenance";
import { echoMatches, filterOptions, parseScopeFilters, type FilterOptions } from "../../lib/scope-filters";
import { getJson, parseRetryAfter } from "../../lib/server/backend";
import type { DashboardConfig } from "../../lib/server/config";
import { requireScope, requireTraceScope } from "../../lib/server/filter-options";
import { loadModels } from "../../lib/server/models";
import { loadOverview } from "../../lib/server/overview";
import { loadTraceList } from "../../lib/server/traces";
import { backendParams, overviewHref, parseOverviewQuery } from "../../lib/time-range";
import { parseTraceListQuery, traceListHref, traceListParams, withFilter, listStateOf } from "../../lib/trace-query";
import { decode } from "../../lib/validate";
import { json, makeCompare, makeOverview } from "../fixtures/overview";
import { makeList, makeTrace, tid, toWire } from "../fixtures/traces";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const T0 = Date.parse("2026-11-01T10:00:00Z");

// ---------------------------------------------------------------- fake runtime
class FakeRuntime implements RefreshRuntime {
  t = T0;
  hidden = false;
  refreshes: number[] = [];
  private seq = 0;
  readonly timers = new Map<number, { at: number; fn: () => void }>();
  now = () => this.t;
  isHidden = () => this.hidden;
  refresh = () => {
    this.refreshes.push(this.t);
  };
  setTimer = (fn: () => void, ms: number) => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, fn });
    return id;
  };
  clearTimer = (h: unknown) => {
    this.timers.delete(h as number);
  };
  /** Advance the clock, firing due timers in order (like a browser event loop). */
  advance(ms: number): void {
    const end = this.t + ms;
    for (;;) {
      let next: [number, { at: number; fn: () => void }] | null = null;
      for (const e of this.timers) if (e[1].at <= end && (next === null || e[1].at < next[1].at)) next = e;
      if (next === null) break;
      this.timers.delete(next[0]);
      this.t = Math.max(this.t, next[1].at);
      next[1].fn();
    }
    this.t = end;
  }
}

function setup(): { rt: FakeRuntime; c: RefreshController } {
  const rt = new FakeRuntime();
  const c = new RefreshController();
  c.attach(rt);
  return { rt, c };
}

let n = 0;
function outcome(failure: ApiFailureKind | null, cooldownUntil: number | null = null, fetchedAt = new Date(T0)): RenderOutcome {
  n += 1;
  return { renderId: `r${n}`, fetchedAt: fetchedAt.toISOString(), failure: failure === null ? null : { kind: failure, cls: failureClass(failure) }, cooldownUntil };
}

/** One completed refresh: pending while the server renders, then the render's outcome. */
function complete(rt: FakeRuntime, c: RefreshController, o: RenderOutcome, renderMs = 200): void {
  c.setPending(true);
  rt.advance(renderMs);
  c.setPending(false);
  c.observe(o);
}

// ---------------------------------------------------------------- lifecycle
describe("refresh controller: lifecycle", () => {
  it("is disabled by default: no timer and no refresh, however long the page stays open", () => {
    const { rt, c } = setup();
    assert.deepEqual(c.getSnapshot().intervalS, null);
    rt.advance(2 * 60 * 60_000);
    assert.equal(rt.refreshes.length, 0);
    assert.equal(rt.timers.size, 0);
    assert.equal(c.getServerSnapshot(), INITIAL_SNAPSHOT);
  });

  it("enable → one refresh per interval, counted from the completed one; disable stops it", () => {
    const { rt, c } = setup();
    c.enable(30);
    assert.equal(c.timerCount(), 1);
    rt.advance(29_999);
    assert.equal(rt.refreshes.length, 0);
    rt.advance(1);
    assert.deepEqual(rt.refreshes, [T0 + 30_000]);
    complete(rt, c, outcome(null), 5_000); // a slow render: the next one is 30 s after it finished
    rt.advance(29_999);
    assert.equal(rt.refreshes.length, 1);
    rt.advance(1);
    assert.equal(rt.refreshes.length, 2);
    c.disable();
    assert.equal(rt.timers.size, 0);
    rt.advance(10 * 60_000);
    assert.equal(rt.refreshes.length, 2);
  });

  it("never overlaps: while a refresh or navigation is pending, no second one starts", () => {
    const { rt, c } = setup();
    c.enable(30);
    rt.advance(30_000);
    c.setPending(true); // the refresh is still rendering
    rt.advance(5 * 60_000);
    assert.equal(rt.refreshes.length, 1);
    assert.ok(c.timerCount() <= 1);
    assert.equal(c.requestRefresh(), false, "manual refresh is refused while one is pending");
  });

  it("keeps exactly one refresh timer across repeated enable, attach and Strict Mode re-mounts", () => {
    const { rt, c } = setup();
    c.enable(30);
    c.enable(60);
    c.enable(30);
    assert.equal(rt.timers.size, 1);
    c.detach();
    c.attach(rt); // Strict Mode: effect cleanup then effect again
    c.detach();
    c.attach(rt);
    assert.equal(rt.timers.size, 1);
    rt.advance(30_000);
    assert.equal(rt.refreshes.length, 1);
  });

  it("detach (unmount) clears every timer: nothing fires afterwards", () => {
    const { rt, c } = setup();
    c.enable(30);
    c.observe(outcome("rate_limited", T0 + 120_000)); // also a countdown ticker
    assert.equal(rt.timers.size, 2);
    c.detach();
    assert.equal(rt.timers.size, 0);
    rt.advance(60 * 60_000);
    assert.equal(rt.refreshes.length, 0);
    assert.equal(c.requestRefresh(), false, "a detached controller sends nothing");
  });

  it("pauses entirely while the tab is hidden and resumes when it is visible again", () => {
    const { rt, c } = setup();
    c.enable(30);
    rt.hidden = true;
    c.visibilityChanged();
    assert.equal(rt.timers.size, 0, "no timer at all while hidden");
    rt.advance(20 * 60_000);
    assert.equal(rt.refreshes.length, 0);
    assert.equal(c.getSnapshot().hidden, true);
    rt.hidden = false;
    c.visibilityChanged();
    assert.equal(rt.refreshes.length, 1, "the overdue refresh runs once on return");
    rt.advance(1_000);
    assert.equal(rt.refreshes.length, 1);
  });

  it("switches itself off after the bound, also when the bound passed while hidden", () => {
    const { rt, c } = setup();
    c.enable(30);
    rt.advance(AUTO_REFRESH_MAX_MS + 1);
    assert.equal(c.getSnapshot().intervalS, null);
    assert.equal(c.getSnapshot().stopped, "max_duration");
    const fired = rt.refreshes.length;
    rt.advance(10 * 60_000);
    assert.equal(rt.refreshes.length, fired);
    const h = setup();
    h.c.enable(30);
    h.rt.hidden = true;
    h.c.visibilityChanged();
    h.rt.advance(AUTO_REFRESH_MAX_MS + 1);
    h.rt.hidden = false;
    h.c.visibilityChanged();
    assert.equal(h.rt.refreshes.length, 0);
    assert.equal(h.c.getSnapshot().stopped, "max_duration");
  });

  it("never polls faster than the floor, whatever interval it is given", () => {
    assert.equal(MIN_INTERVAL_MS, 15_000);
    assert.equal(backoffMs(1_000, 0), 15_000);
    assert.deepEqual(decideRefresh({ intervalMs: 1_000, startedAt: 0, lastAt: 0, now: 1_000, pending: false, hidden: false }), { action: "wait", delayMs: 14_000 });
  });

  it("each render is observed once; a repeated outcome is ignored", () => {
    const { c } = setup();
    const o = outcome("unreachable");
    c.observe(o);
    c.observe(o);
    c.observe({ ...o });
    assert.equal(c.getSnapshot().failures, 1);
  });
});

// ---------------------------------------------------------------- 429, backoff, fatal
describe("refresh controller: rate limit, failures and stop conditions", () => {
  it("a 429 cooldown blocks the timer AND the manual button until Retry-After has passed", () => {
    const { rt, c } = setup();
    c.enable(30);
    rt.advance(30_000);
    complete(rt, c, outcome("rate_limited", rt.t + 200 + 90_000));
    const until = rt.t + 90_000;
    assert.equal(c.canRefresh(), false);
    assert.equal(c.requestRefresh(), false, "manual Refresh cannot bypass the cooldown");
    assert.equal(c.getSnapshot().cooldownRemainingS, 90);
    rt.advance(45_000);
    assert.equal(c.getSnapshot().cooldownRemainingS, 45, "the countdown ticks");
    assert.equal(rt.refreshes.length, 1, "no early automatic retry");
    rt.advance(44_999);
    assert.equal(rt.refreshes.length, 1);
    assert.equal(c.requestRefresh(), false);
    rt.advance(1);
    assert.ok(rt.t >= until);
    assert.equal(rt.refreshes.length, 2, "first retry exactly when the cooldown ends");
    assert.equal(c.getSnapshot().cooldownRemainingS, 0);
  });

  it("a second, shorter Retry-After never shortens a running cooldown", () => {
    const { rt, c } = setup();
    c.observe(outcome("rate_limited", T0 + 100_000));
    c.observe(outcome("rate_limited", T0 + 10_000));
    rt.advance(50_000);
    assert.equal(c.requestRefresh(), false);
    rt.advance(50_000);
    assert.equal(c.requestRefresh(), true);
  });

  it("5xx and network failures back off exponentially (bounded), and a success resets it", () => {
    const { rt, c } = setup();
    c.enable(30);
    rt.advance(30_000);
    complete(rt, c, outcome("server_error"), 0);
    rt.advance(59_999);
    assert.equal(rt.refreshes.length, 1, "doubled after one failure");
    rt.advance(1);
    assert.equal(rt.refreshes.length, 2);
    complete(rt, c, outcome("unreachable"), 0);
    rt.advance(119_999);
    assert.equal(rt.refreshes.length, 2, "doubled again");
    rt.advance(1);
    assert.equal(rt.refreshes.length, 3);
    complete(rt, c, outcome(null), 0);
    rt.advance(30_000);
    assert.equal(rt.refreshes.length, 4, "back to the chosen interval");
    assert.equal(backoffMs(30_000, 10), 300_000, "capped at 5 min");
    assert.equal(backoffMs(300_000, 3), 300_000, "never below the interval");
  });

  it("stops polling on a failure a retry cannot fix (401, 404, invalid project, malformed / other project)", () => {
    for (const kind of ["unauthorized", "not_found", "invalid_project", "config", "invalid_request", "malformed"] as const) {
      const { rt, c } = setup();
      c.enable(30);
      rt.advance(30_000);
      complete(rt, c, outcome(kind));
      assert.equal(c.getSnapshot().intervalS, null, kind);
      assert.equal(c.getSnapshot().stopped, "fatal", kind);
      rt.advance(60 * 60_000);
      assert.equal(rt.refreshes.length, 1, `${kind}: no further automatic request`);
      assert.match(autoRefreshStatus(c.getSnapshot()), /^Stopped: /);
      assert.equal(c.requestRefresh(), true, `${kind}: a deliberate manual refresh stays possible`);
    }
  });

  it("classifies every failure kind, and a failed read is never reported as success", () => {
    const kinds: ApiFailureKind[] = ["config", "invalid_project", "unreachable", "timeout", "unauthorized", "not_found", "invalid_request", "conflict", "payload_too_large", "rate_limited", "storage_unavailable", "server_error", "unexpected_status", "malformed"];
    const cls = Object.fromEntries(kinds.map((k) => [k, failureClass(k)]));
    assert.deepEqual(cls, {
      config: "fatal",
      invalid_project: "fatal",
      unreachable: "transient",
      timeout: "transient",
      unauthorized: "fatal",
      not_found: "fatal",
      invalid_request: "fatal",
      conflict: "fatal",
      payload_too_large: "fatal",
      rate_limited: "cooldown",
      storage_unavailable: "transient",
      server_error: "transient",
      unexpected_status: "transient",
      malformed: "fatal",
    });
    const { c } = setup();
    c.observe(outcome("server_error"));
    assert.equal(c.getSnapshot().lastSuccessAt, null, "no success recorded for a failed read");
  });

  it("status copy: off, polling, hidden, cooldown (absolute time, no countdown), backoff, stopped", () => {
    const base = { ...INITIAL_SNAPSHOT };
    assert.equal(autoRefreshStatus(base), "Off. Data changes only when you refresh.");
    assert.equal(autoRefreshStatus(base, "Off on later pages."), "Off on later pages.");
    const on = { ...base, intervalS: 30 as const };
    assert.match(autoRefreshStatus(on), /Every 30 s while this tab is visible; stops after 30 min\. Re-reads stored telemetry; not live\./);
    assert.match(autoRefreshStatus({ ...on, hidden: true }), /Paused while this tab is hidden/);
    const cooling = autoRefreshStatus({ ...on, cooldownRemainingS: 77, cooldownUntil: Date.parse("2026-11-01T10:01:30Z") });
    assert.match(cooling, /Waiting for the backend rate limit \(429\) until 10:01:30 UTC\./);
    assert.doesNotMatch(cooling, /77/, "the polite live region carries no per-second countdown");
    assert.match(autoRefreshStatus({ ...on, failures: 2 }), /next try in 120 s/);
    assert.equal(autoRefreshStatus({ ...base, stopped: "max_duration" }), "Stopped after 30 minutes.");
    assert.match(autoRefreshStatus({ ...base, stopped: "fatal", lastFailure: { kind: "unauthorized", cls: "fatal" } }), /Stopped: the backend rejected the dashboard's credentials/);
  });
});

// ---------------------------------------------------------------- render outcome
describe("render outcome", () => {
  const ok: ApiResult<unknown> = { ok: true, data: {} };
  const fail = (kind: ApiFailureKind, retryAfterSeconds?: number): ApiResult<unknown> => ({ ok: false, error: retryAfterSeconds === undefined ? { kind } : { kind, retryAfterSeconds } });
  it("all reads succeeded → no failure and no cooldown", () => {
    const o = renderOutcome(new Date(T0), T0 + 50, [ok, ok, null]);
    assert.equal(o.failure, null);
    assert.equal(o.cooldownUntil, null);
    assert.equal(o.fetchedAt, "2026-11-01T10:00:00.000Z");
  });
  it("reports the most severe failure and the longest Retry-After, counted from the settled time", () => {
    const o = renderOutcome(new Date(T0), T0 + 50, [ok, fail("server_error"), fail("rate_limited", 7), fail("storage_unavailable", 30)]);
    assert.deepEqual(o.failure, { kind: "rate_limited", cls: "cooldown" });
    assert.equal(o.cooldownUntil, T0 + 50 + 30_000);
    assert.deepEqual(renderOutcome(new Date(T0), T0, [fail("rate_limited"), fail("unauthorized")]).failure, { kind: "unauthorized", cls: "fatal" });
  });
  it("a 429 without Retry-After still cools down (never a tight loop)", () => {
    assert.equal(renderOutcome(new Date(T0), T0, [fail("rate_limited")]).cooldownUntil, T0 + DEFAULT_RATE_LIMIT_COOLDOWN_S * 1000);
    assert.equal(renderOutcome(new Date(T0), T0, [fail("storage_unavailable")]).cooldownUntil, null);
  });
  it("distinct renders get distinct ids", () => {
    assert.notEqual(renderOutcome(new Date(T0), T0 + 1, [ok]).renderId, renderOutcome(new Date(T0), T0 + 2, [ok]).renderId);
  });
});

// ---------------------------------------------------------------- Retry-After
describe("Retry-After", () => {
  const now = Date.parse("2026-11-01T08:30:00Z");
  it("delay-seconds and IMF-fixdate HTTP-dates, rounded up so a retry is never early", () => {
    assert.equal(parseRetryAfter("120", now), 120);
    assert.equal(parseRetryAfter(" 5 ", now), 5);
    assert.equal(parseRetryAfter("Sun, 01 Nov 2026 08:31:00 GMT", now), 60);
    assert.equal(parseRetryAfter("Sun, 01 Nov 2026 08:30:00 GMT", now + 1), 0, "a past date means no wait");
    assert.equal(parseRetryAfter("Sun, 01 Nov 2026 08:30:01 GMT", now + 1), 1, "partial seconds round up");
    assert.equal(parseRetryAfter("Fri, 01 Jan 2100 00:00:00 GMT", now), 999_999, "bounded");
  });
  it("ignores anything else", () => {
    for (const bad of [null, "", "-1", "1.5", "1e3", "soon", "1234567", "2026-11-01T08:31:00Z", "Sunday, 01-Nov-26 08:31:00 GMT", "Sun Nov  1 08:31:00 2026"]) assert.equal(parseRetryAfter(bad, now), undefined, String(bad));
  });
  it("a 429 with an HTTP-date reaches the page as seconds", async () => {
    const config: DashboardConfig = { baseUrl: new URL("http://127.0.0.1:8000"), token: "SYNTHETIC-token-0123456789abcdef0123456789abcd", defaultProjectId: "7", timeoutMs: 200 };
    const at = new Date(Date.now() + 61_000).toUTCString();
    const fetch = (async () => new Response('{"error":{"code":"rate_limited","message":"x"}}', { status: 429, headers: { "content-type": "application/json", "retry-after": at } })) as unknown as typeof globalThis.fetch;
    const warn = console.warn;
    console.warn = () => {};
    try {
      const r = await getJson(config, "/api/v1/overview", { project: "7" }, {}, overviewDecoder, { fetch });
      assert.equal(r.ok, false);
      if (!r.ok) {
        assert.equal(r.error.kind, "rate_limited");
        assert.ok(r.error.retryAfterSeconds !== undefined && r.error.retryAfterSeconds >= 60 && r.error.retryAfterSeconds <= 62, String(r.error.retryAfterSeconds));
      }
    } finally {
      console.warn = warn;
    }
  });
});

// ---------------------------------------------------------------- stale display
describe("stale display of the last good data", () => {
  const retained = { viewKey: "9?range=7d", fetchedAt: "2026-11-01T10:00:00.000Z", content: "old" };
  const at = "2026-11-01T10:00:30.000Z";
  it("only for the same view, only after a transient or rate-limit failure, never for a fatal one", () => {
    assert.equal(showsStale({ kind: "unreachable" }, retained, "9?range=7d", at), true);
    assert.equal(showsStale({ kind: "rate_limited", retryAfterSeconds: 5 }, retained, "9?range=7d", at), true);
    assert.equal(showsStale({ kind: "server_error" }, retained, "9?range=7d", at), true);
    assert.equal(showsStale(null, retained, "9?range=7d", at), false, "a successful render is current data");
    assert.equal(showsStale({ kind: "unreachable" }, retained, "9?range=24h", at), false, "other filters: never");
    assert.equal(showsStale({ kind: "unreachable" }, retained, "10?range=7d", at), false, "another project: never");
    for (const kind of ["unauthorized", "not_found", "invalid_project", "malformed", "config", "invalid_request"] as const) {
      assert.equal(showsStale({ kind }, retained, "9?range=7d", at), false, kind);
    }
    assert.equal(showsStale({ kind: "unreachable" }, null, "9?range=7d", at), false, "nothing retained: the failure alone");
  });
  it("the controller keeps one view and notifies only on change", () => {
    const c = new RefreshController();
    let calls = 0;
    c.subscribe(() => (calls += 1));
    c.retain(retained);
    c.retain({ ...retained });
    assert.equal(calls, 1);
    assert.equal(c.getRetained()?.viewKey, "9?range=7d");
    assert.equal(c.getServerRetained(), null);
  });
});

// ---------------------------------------------------------------- provider / model filters
describe("provider and model filters", () => {
  it("parses stored identifier formats only and names refused values", () => {
    const ignored: string[] = [];
    const get = (m: Record<string, string>) => (k: string) => m[k];
    assert.deepEqual(parseScopeFilters(get({ provider: "gcp.gemini", model: "gemini-3.1-flash-lite" }), ignored), { provider: "gcp.gemini", model: "gemini-3.1-flash-lite" });
    assert.deepEqual(parseScopeFilters(get({ provider: " unknown ", model: "" }), ignored), { provider: "unknown", model: null });
    assert.deepEqual(ignored, []);
    for (const bad of ["open ai", "open:ai", "-x", "<script>", "p".repeat(65), "x%y"]) {
      const ig: string[] = [];
      assert.deepEqual(parseScopeFilters(get({ provider: bad }), ig), { provider: null, model: null }, bad);
      assert.deepEqual(ig, ["provider"]);
    }
  });

  it("forwards provider and model to every aggregate and list read, and keeps them in links", () => {
    const now = new Date("2026-11-01T10:00:00Z");
    const o = parseOverviewQuery({ project: "148", range: "7d", provenance: "real", provider: "gcp.gemini", model: "gemini-3.1-flash-lite" });
    assert.deepEqual(backendParams(o, now), { from: "2026-10-26T00:00:00.000Z", to: "2026-11-02T00:00:00.000Z", provenance: "real", provider: "gcp.gemini", model: "gemini-3.1-flash-lite" });
    assert.equal(overviewHref({ project: "148", window: o.window, provenance: o.provenance, provider: o.provider, model: o.model }), "/?project=148&range=7d&provenance=real&provider=gcp.gemini&model=gemini-3.1-flash-lite");
    const m = parseModelQuery({ provider: "openai" });
    assert.equal(compareParams(m, now).provider, "openai");
    const t = parseTraceListQuery({ provider: "unknown", model: "gpt-a", status: "error" });
    assert.equal(traceListParams(t, now).provider, "unknown");
    const s = listStateOf(t);
    assert.equal(traceListHref(s), "/traces?status=error&model=gpt-a&provider=unknown");
    assert.equal(traceListHref(withFilter({ ...s, cursor: "abc", page: 3 }, { provider: "openai" })), "/traces?status=error&model=gpt-a&provider=openai", "a filter change restarts paging");
  });

  it("suggests only values the backend observed: providers, the union of requested/response models, unreported models counted", () => {
    const c = makeCompare();
    c.groups.push({ ...c.groups[1]!, provider: "gcp.gemini", requested_model: null, response_model: null, attempts: 3 });
    c.groups_truncated = true;
    const o = filterOptions(c);
    assert.deepEqual(o.providers, [
      { value: "openai", attempts: 4 },
      { value: "gcp.gemini", attempts: 3 },
      { value: "mock", attempts: 2 },
    ]);
    assert.deepEqual(
      o.models.map((m) => m.value),
      ["gpt-5.4-mini", "gpt-5.4-mini-2026-03-17", "mock-model"],
    );
    assert.equal(o.attemptsWithoutModel, 3);
    assert.equal(o.truncated, true);
  });

  it("refuses an aggregate whose echo does not show the requested filter (e.g. a backend that ignores provider)", () => {
    const wire = json(makeCompare()) as Record<string, unknown>;
    delete (wire.filters as Record<string, unknown>).provider; // an older backend
    const decoded = decode(modelsCompareDecoder, wire);
    assert.equal(decoded.ok, true, "the additive field may be absent");
    if (!decoded.ok) return;
    assert.equal(decoded.value.filters.provider, null);
    const r = requireScope({ ok: true, data: decoded.value }, { provider: "gcp.gemini", model: null });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.kind, "malformed");
    assert.equal(requireScope({ ok: true, data: decoded.value }, { provider: null, model: null }).ok, true);
    assert.equal(echoMatches({ model: "m", provider: "openai" }, { provider: "openai", model: "m" }), true);
    assert.equal(echoMatches({ model: null, provider: "openai" }, { provider: "openai", model: "m" }), false);
  });

  it("refuses a trace list containing a trace without the requested provider/model call (unless that item's list is truncated)", () => {
    const gem = { provider: "gcp.gemini", requested_model: "gemini-3.1-flash-lite", response_model: "gemini-3.1-flash-lite", calls: 1 };
    const list = (items: TraceList["items"]): ApiResult<TraceList> => ({ ok: true, data: makeList(items) });
    const g = makeTrace({ trace_id: tid(2), usage: { ...makeTrace().usage, models: { items: [gem], distinct: 1, truncated: false } } });
    assert.equal(requireTraceScope(list(makeList([g]).items), { provider: "gcp.gemini", model: null }).ok, true);
    assert.equal(requireTraceScope(list(makeList([g]).items), { provider: "gcp.gemini", model: "gemini-3.1-flash-lite" }).ok, true);
    assert.equal(requireTraceScope(list(makeList([g, makeTrace()]).items), { provider: "gcp.gemini", model: null }).ok, false, "an openai-only trace in a gemini listing");
    assert.equal(requireTraceScope(list(makeList([g]).items), { provider: "gcp.gemini", model: "gpt-5.4-mini" }).ok, false, "provider and model must be one call");
    const truncated = makeTrace({ usage: { ...makeTrace().usage, models: { items: [], distinct: 11, truncated: true } } });
    assert.equal(requireTraceScope(list(makeList([truncated]).items), { provider: "gcp.gemini", model: null }).ok, true);
    assert.equal(requireTraceScope(list(makeList([makeTrace()]).items), { provider: null, model: null }).ok, true);
  });

  it("the filter form: observed values with counts, the unknown provider explained, an unobserved URL value kept and marked, hostile names escaped", () => {
    const options: ApiResult<FilterOptions> = {
      ok: true,
      data: {
        providers: [
          { value: "gcp.gemini", attempts: 1 },
          { value: "unknown", attempts: 2 },
        ],
        models: [{ value: "a<script>b", attempts: 1 }],
        attemptsWithoutModel: 2,
        truncated: true,
      },
    };
    const m = html(<ScopeFilterForm action="/traces" hidden={[["range", "7d"]]} scope={{ provider: "openai", model: null }} options={options} clearHref="/traces?range=7d" idPrefix="t" />);
    assert.match(m, /gcp\.gemini · 1 attempt</);
    assert.match(m, /unknown \(no provider name reported\) · 2 attempts/);
    assert.match(m, /openai \(not observed in this window\)/);
    assert.match(m, /2 attempts reported no model; a model filter never selects them/);
    assert.match(m, /200 largest groups only/);
    assert.match(m, /not only the rows on this page/);
    assert.doesNotMatch(m, /<script>/);
    assert.match(m, /name="range" value="7d"/);
    const failed = html(<ScopeFilterForm action="/" hidden={[]} scope={{ provider: null, model: null }} options={{ ok: false, error: { kind: "rate_limited", retryAfterSeconds: 3 } }} clearHref="/" idPrefix="o" />);
    assert.match(failed, /Suggested values unavailable \(backend rate limit reached\)/);
    assert.doesNotMatch(failed, /data-state="error"/, "a degraded control is not a second error state");
    assert.doesNotMatch(failed, /Clear provider and model/);
  });
});

// ---------------------------------------------------------------- loaders
describe("loaders (Phase 6C-C reads)", () => {
  const saved = { ...process.env };
  beforeEach(() => {
    process.env.LLMI_API_BASE_URL = "http://127.0.0.1:8000";
    process.env.LLMI_API_TOKEN = "SYNTHETIC-test-token-0123456789abcdef0123456789";
    process.env.LLMI_DASHBOARD_PROJECT_ID = "3";
  });
  afterEach(() => {
    for (const k of ["LLMI_API_BASE_URL", "LLMI_API_TOKEN", "LLMI_DASHBOARD_PROJECT_ID"]) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  });

  function deps(calls: URL[], provider: string | null) {
    const respond = (url: URL): Response => {
      calls.push(url);
      const body = url.pathname.endsWith("/overview")
        ? { ...json(makeOverview()), project: 148, filters: { model: null, status: null, prompt_version: null, provenance: null, provider: url.searchParams.get("provider") } }
        : url.pathname.endsWith("/compare")
          ? { ...json(makeCompare()), project: 148, filters: { model: null, status: null, prompt_version: null, provenance: null, provider: url.searchParams.get("provider") } }
          : url.pathname.endsWith("/projects")
            ? { items: [{ id: 148, name: "gemini-live", created_at: "2026-10-28T18:18:00.919388Z" }], truncated: false, limit: 200 }
            : toWire(makeList([makeTrace({ usage: { ...makeTrace().usage, models: { items: [{ provider: provider ?? "openai", requested_model: "m", response_model: "m", calls: 1 }], distinct: 1, truncated: false } } })]));
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    };
    return { fetch: (async (input: string | URL | Request) => respond(new URL(input instanceof Request ? input.url : input.toString()))) as typeof fetch };
  }

  it("the overview reads the latest trace (limit 1) and the unfiltered filter values of the same project and window", async () => {
    const calls: URL[] = [];
    const now = new Date("2026-11-01T10:00:00Z");
    const ctx = await loadOverview(parseOverviewQuery({ project: "148", range: "7d", provider: "gcp.gemini" }), now, deps(calls, "gcp.gemini"));
    assert.equal(ctx.overview.ok, true, JSON.stringify(ctx.overview));
    assert.equal(ctx.latest?.ok, true);
    assert.equal(ctx.options?.ok, true);
    const data = calls.filter((u) => !u.pathname.endsWith("/projects"));
    for (const u of data) assert.equal(u.searchParams.get("project"), "148", u.pathname);
    const latest = data.find((u) => u.pathname === "/api/v1/traces");
    assert.equal(latest?.searchParams.get("limit"), "1");
    assert.equal(latest?.searchParams.get("provider"), "gcp.gemini");
    const compares = data.filter((u) => u.pathname === "/api/v1/models/compare");
    assert.deepEqual(compares.map((u) => u.searchParams.get("provider")).sort(), ["gcp.gemini", null].sort());
    const options = compares.find((u) => u.searchParams.get("limit") === "200");
    assert.ok(options, "filter values: the backend's maximum of groups");
    assert.equal(options.searchParams.get("provider"), null, "suggestions are not narrowed by the current filter");
    assert.equal(options.searchParams.get("from"), latest?.searchParams.get("from"), "same window");
    assert.ok(ctx.settledAt >= now.getTime() || ctx.settledAt > 0);
  });

  it("models and traces send the provider; a trace list that ignored it is refused", async () => {
    const calls: URL[] = [];
    const m = await loadModels(parseModelQuery({ project: "148", provider: "gcp.gemini" }), new Date(), deps(calls, "gcp.gemini"));
    assert.equal(m.compare.ok, true);
    const l = await loadTraceList(parseTraceListQuery({ project: "148", provider: "gcp.gemini" }), new Date(), deps(calls, "gcp.gemini"));
    assert.equal(l.list.ok, true);
    assert.equal(calls.find((u) => u.pathname === "/api/v1/traces")?.searchParams.get("provider"), "gcp.gemini");
    const warn = console.warn;
    console.warn = () => {};
    try {
      const ignored = await loadTraceList(parseTraceListQuery({ project: "148", provider: "gcp.gemini" }), new Date(), deps([], null));
      assert.equal(ignored.list.ok, false);
      if (!ignored.list.ok) assert.equal(ignored.list.error.kind, "malformed");
    } finally {
      console.warn = warn;
    }
  });
});

// ---------------------------------------------------------------- provenance and the latest trace
describe("asserted provenance and the latest stored trace", () => {
  it("real is always labelled as asserted, never as verified", () => {
    assert.equal(PROVENANCE_LABEL.real, "Real (asserted)");
    assert.match(PROVENANCE_DESCRIPTION.real, /asserted, not verified/);
    const b = html(<ProvenanceBadge provenance="real" />);
    assert.match(b, /Real \(asserted\)/);
    assert.doesNotMatch(b, /verified real/i);
    assert.match(html(<ProvenanceBadge provenance="unknown" />), />Unknown</);
  });

  const gemini = makeTrace({
    trace_id: tid(7), // the shape of the first real Gemini trace (Phase 6C-B); a SYNTHETIC fixture
    service_name: "live-gemini",
    started_at: "2026-10-29T10:48:49.757174600Z",
    duration_ns: "6751514200",
    provenance: "real",
    span_count: 2,
    usage: {
      inference_calls: 1,
      input_tokens: "18",
      output_tokens: "1",
      calls_with_unknown_tokens: 0,
      known_cost_usd: null,
      unknown_cost_calls: 1,
      models: { items: [{ provider: "gcp.gemini", requested_model: "gemini-3.1-flash-lite", response_model: "gemini-3.1-flash-lite", calls: 1 }], distinct: 1, truncated: false },
    },
  });

  it("shows the instrumentation's event time (UTC, ns kept in the title), the read time, status, provenance and models", () => {
    const m = html(<LatestTrace result={{ ok: true, data: makeList([gemini]) }} fetchedAt={new Date("2026-11-01T08:23:46.501Z")} project="148" filtered={false} />);
    assert.match(m, /data-latest="found"/);
    assert.match(m, /2026-10-29 10:48:49\.757 UTC/);
    assert.match(m, /title="2026-10-29 10:48:49\.757174600 UTC"/);
    assert.match(m, /event time reported by the instrumentation/);
    assert.match(m, /Read from the backend at 08:23:46 UTC/);
    assert.match(m, /not live/);
    assert.match(m, /6\.75 s|6751/);
    assert.match(m, /Real \(asserted\)/);
    assert.match(m, /gcp\.gemini/);
    assert.match(m, /gemini-3\.1-flash-lite/);
    assert.ok(m.includes(`href="/traces/${tid(7)}?project=148"`));
  });

  it("no trace / unavailable are stated, never shown as a zero or an invented trace", () => {
    const none = html(<LatestTrace result={{ ok: true, data: makeList([]) }} fetchedAt={new Date(T0)} project={null} filtered={true} />);
    assert.match(none, /data-latest="none"/);
    assert.match(none, /No trace started in this window with these filters/);
    const down = html(<LatestTrace result={{ ok: false, error: { kind: "unreachable" } }} fetchedAt={new Date(T0)} project={null} filtered={false} />);
    assert.match(down, /data-latest="unavailable"/);
    assert.match(down, /backend unreachable\. No trace is assumed/);
  });

  it("a Gemini group (cost never computed, ADR-35) shows unknown cost, never $0, and its asserted-real count", () => {
    const c = makeCompare();
    const g = c.groups[1]!; // the fixture group whose cost is unknown (price_missing)
    c.groups = [{ ...g, provider: "gcp.gemini", requested_model: "gemini-3.1-flash-lite", response_model: "gemini-3.1-flash-lite", by_provenance: { real: g.attempts, synthetic: 0, unknown: 0 } }];
    const decoded = decode(modelsCompareDecoder, json(c));
    assert.equal(decoded.ok, true, decoded.ok ? "" : decoded.path);
    if (!decoded.ok) return;
    assert.equal(decoded.value.groups[0]?.cost.known_cost_usd, null);
    const m = html(<ComparisonTable data={decoded.value} />);
    assert.match(m, /gcp\.gemini/);
    assert.match(m, /data-cost-state="unknown"/);
    assert.doesNotMatch(m, /\$0(\.0+)?(?![\d.])/, "unknown cost is never $0");
  });
});

// ---------------------------------------------------------------- no provider call
describe("no provider call from the dashboard", () => {
  const ROOT = path.resolve(__dirname, "..", "..", "..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = path.join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(ts|tsx|mjs)$/.test(name)) files.push(p);
    }
  };
  for (const d of ["app", "components", "lib"]) walk(path.join(ROOT, d));
  it("no source names a provider endpoint or provider key, and the backend allowlist has no execute route", () => {
    assert.ok(files.length > 50);
    for (const f of files) {
      const s = readFileSync(f, "utf8");
      assert.doesNotMatch(s, /generativelanguage\.googleapis\.com|api\.openai\.com|GEMINI_API_KEY|OPENAI_API_KEY|GOOGLE_API_KEY/, f);
    }
    const backend = readFileSync(path.join(ROOT, "lib/server/backend.ts"), "utf8");
    assert.doesNotMatch(backend, /execute|live|gemini|openai/i);
  });
  it("no client component imports describeFailure (its operator hints name server-side settings, T42)", () => {
    const clients = files.filter((f) => /^\s*["']use client["']/.test(readFileSync(f, "utf8")));
    assert.ok(clients.length >= 5, "client components found");
    for (const f of clients) assert.doesNotMatch(readFileSync(f, "utf8"), /\bdescribeFailure\b/, f);
  });
});
