/**
 * Controlled auto-refresh (Phase 3C, extended in Phase 6C-C): re-runs the page's current server
 * render on a timer. It re-reads already stored telemetry; it is polling, not a live stream or
 * a push channel, and makes no freshness guarantee. Rules:
 * - off by default, chosen per tab, never stored;
 * - never overlapping: while any navigation or refresh is pending, the next one waits (Next.js
 *   also runs router actions one at a time, and a navigation discards a pending refresh, so a
 *   superseded render is never applied);
 * - at most one refresh per interval, counted from the last completed one; never faster than
 *   MIN_INTERVAL_MS;
 * - paused while the tab is hidden (no timer at all), resumed when it is visible again;
 * - bounded: it switches itself off after AUTO_REFRESH_MAX_MS;
 * - a 429 (or a 503 with Retry-After) starts a cooldown that neither the timer nor the manual
 *   Refresh button can cut short; 5xx and network failures back off exponentially;
 * - failures that a retry cannot fix (bad configuration, rejected token, unknown project,
 *   refused query, a response that does not match the contract or names another project)
 *   stop polling instead of repeating the request;
 * - pages pass a reason to disable it where a refresh would be misleading (later cursor pages).
 *
 * The controller below holds that state outside React (read with useSyncExternalStore), so the
 * timer lifecycle is testable without a DOM and is the same under React Strict Mode: `attach`
 * and `detach` may run twice, and there is never more than one refresh timer.
 */

import type { ReactNode } from "react";
import type { ApiFailure, ApiFailureKind, ApiResult } from "./api/result";

export const AUTO_REFRESH_INTERVALS_S = [30, 60, 300] as const;
export type AutoRefreshSeconds = (typeof AUTO_REFRESH_INTERVALS_S)[number];
export const DEFAULT_INTERVAL_S: AutoRefreshSeconds = 30;
/** No configuration can poll faster than this. */
export const MIN_INTERVAL_MS = 15_000;
export const AUTO_REFRESH_MAX_MS = 30 * 60_000;
/** How soon to look again while another request is still pending. */
export const PENDING_RECHECK_MS = 1_000;
/** Upper bound of the failure backoff (never below the chosen interval). */
export const MAX_BACKOFF_MS = 5 * 60_000;
/** Cooldown after a 429 that carried no usable Retry-After. */
export const DEFAULT_RATE_LIMIT_COOLDOWN_S = 60;

// ---------------------------------------------------------------- failure classes
/**
 * - `cooldown`: the backend asked us to slow down (429);
 * - `transient`: a retry may succeed (outage, timeout, 5xx); the last good data may be shown as stale;
 * - `fatal`: retrying sends the same refused request; polling stops and nothing stale is shown.
 */
export type FailureClass = "cooldown" | "transient" | "fatal";

export function failureClass(kind: ApiFailureKind): FailureClass {
  switch (kind) {
    case "rate_limited":
      return "cooldown";
    case "unreachable":
    case "timeout":
    case "storage_unavailable":
    case "server_error":
    case "unexpected_status":
      return "transient";
    case "config":
    case "invalid_project":
    case "unauthorized":
    case "not_found":
    case "invalid_request":
    case "conflict":
    case "payload_too_large":
    case "malformed": // includes "response for another project" (requireProject)
      return "fatal";
  }
}

const CLASS_RANK: Record<FailureClass, number> = { fatal: 3, cooldown: 2, transient: 1 };

/**
 * Why polling stopped, for the status line of a client component. Fixed copy kept here on
 * purpose: lib/api/result.ts `describeFailure` carries operator hints that name server-side
 * settings, and importing it into a client component would ship them in the browser bundle (T42).
 */
export const STOP_REASON: Record<ApiFailureKind, string> = {
  config: "the dashboard configuration is incomplete",
  invalid_project: "the project id is not valid",
  unreachable: "the backend is unreachable",
  timeout: "the backend timed out",
  unauthorized: "the backend rejected the dashboard's credentials",
  not_found: "the project was not found",
  invalid_request: "the backend refused the query",
  conflict: "the backend refused the request",
  payload_too_large: "the backend refused the request",
  rate_limited: "the backend rate limit was reached",
  storage_unavailable: "the backend database is unavailable",
  server_error: "the backend failed",
  unexpected_status: "the backend answered unexpectedly",
  malformed: "the backend response did not match the contract",
};

/** What one server render reports to the refresh controller (serializable, no data, no secret). */
export interface RenderOutcome {
  /** Distinct per server render. */
  renderId: string;
  /** When the render read the backend (ISO, UTC). */
  fetchedAt: string;
  /** The most severe failure among the render's reads, or null when every read succeeded. */
  failure: { kind: ApiFailureKind; cls: FailureClass } | null;
  /** Epoch ms before which no refresh may be sent (from Retry-After), or null. */
  cooldownUntil: number | null;
}

/**
 * Builds a render's outcome from all of its backend reads. `settledAt` is the server clock after
 * the reads returned, so a Retry-After counts from (at the earliest) the response that carried it.
 * The dashboard is bound to loopback, so the browser and the server share this machine's clock.
 */
export function renderOutcome(fetchedAt: Date, settledAt: number, results: readonly (ApiResult<unknown> | null)[]): RenderOutcome {
  let failure: RenderOutcome["failure"] = null;
  let waitS: number | null = null;
  for (const r of results) {
    if (r === null || r.ok) continue;
    const cls = failureClass(r.error.kind);
    if (failure === null || CLASS_RANK[cls] > CLASS_RANK[failure.cls]) failure = { kind: r.error.kind, cls };
    const s = cooldownSeconds(r.error);
    if (s !== null) waitS = Math.max(waitS ?? 0, s);
  }
  const iso = fetchedAt.toISOString();
  return { renderId: `${iso}/${settledAt}`, fetchedAt: iso, failure, cooldownUntil: waitS === null ? null : settledAt + waitS * 1000 };
}

function cooldownSeconds(error: ApiFailure): number | null {
  if (error.kind === "rate_limited") return error.retryAfterSeconds ?? DEFAULT_RATE_LIMIT_COOLDOWN_S;
  if (error.kind === "storage_unavailable" && error.retryAfterSeconds !== undefined) return error.retryAfterSeconds;
  return null;
}

// ---------------------------------------------------------------- decisions
export interface RefreshState {
  intervalMs: number;
  /** When auto-refresh was switched on. */
  startedAt: number;
  /** When the last refresh (or the switch-on) happened. */
  lastAt: number;
  now: number;
  pending: boolean;
  hidden: boolean;
  /** Epoch ms of the end of a server-enforced cooldown (0 = none). */
  cooldownUntil?: number;
  /** Consecutive transient failures (backoff). */
  failures?: number;
}

export type RefreshDecision = { action: "refresh" } | { action: "wait"; delayMs: number } | { action: "pause" } | { action: "stop" };

/** The interval after `failures` consecutive transient failures: doubled each time, bounded. */
export function backoffMs(intervalMs: number, failures: number): number {
  const base = Math.max(intervalMs, MIN_INTERVAL_MS);
  if (failures <= 0) return base;
  return Math.min(base * 2 ** Math.min(failures, 6), Math.max(base, MAX_BACKOFF_MS));
}

export function decideRefresh(s: RefreshState): RefreshDecision {
  const stopAt = s.startedAt + AUTO_REFRESH_MAX_MS;
  if (s.now >= stopAt) return { action: "stop" };
  if (s.hidden) return { action: "pause" };
  if (s.pending) return { action: "wait", delayMs: PENDING_RECHECK_MS };
  const due = Math.max(s.lastAt + backoffMs(s.intervalMs, s.failures ?? 0), s.cooldownUntil ?? 0);
  if (s.now < due) return { action: "wait", delayMs: Math.min(due, stopAt) - s.now };
  return { action: "refresh" };
}

export function parseInterval(value: string): AutoRefreshSeconds | null {
  return AUTO_REFRESH_INTERVALS_S.find((s) => String(s) === value) ?? null;
}

export function intervalLabel(seconds: AutoRefreshSeconds): string {
  return seconds < 60 ? `${seconds} s` : `${seconds / 60} min`;
}

// ---------------------------------------------------------------- controller
/** What the controller needs from its environment (the browser, or a fake clock in tests). */
export interface RefreshRuntime {
  now(): number;
  setTimer(fn: () => void, ms: number): unknown;
  clearTimer(handle: unknown): void;
  isHidden(): boolean;
  /** Re-run the current server render (router.refresh inside a transition). */
  refresh(): void;
}

export type StopReason = "max_duration" | "fatal";

export interface LiveSnapshot {
  intervalS: AutoRefreshSeconds | null;
  stopped: StopReason | null;
  hidden: boolean;
  pending: boolean;
  /** Consecutive transient failures since the last successful render. */
  failures: number;
  /** Whole seconds left in a server-enforced cooldown (0 = none). */
  cooldownRemainingS: number;
  /** Epoch ms when that cooldown ends (0 = none). */
  cooldownUntil: number;
  /** The failure of the latest observed render, if any. */
  lastFailure: RenderOutcome["failure"];
  /** fetchedAt of the latest render whose reads all succeeded. */
  lastSuccessAt: string | null;
  /** fetchedAt of the latest observed render. */
  lastFetchedAt: string | null;
  /** When the next automatic refresh is planned (epoch ms), if one is. */
  nextAt: number | null;
}

/** The last successfully rendered content of one view (Phase 6C-C stale display). */
export interface RetainedView {
  viewKey: string;
  fetchedAt: string;
  content: ReactNode;
}

export const INITIAL_SNAPSHOT: LiveSnapshot = Object.freeze({
  intervalS: null,
  stopped: null,
  hidden: false,
  pending: false,
  failures: 0,
  cooldownRemainingS: 0,
  cooldownUntil: 0,
  lastFailure: null,
  lastSuccessAt: null,
  lastFetchedAt: null,
  nextAt: null,
});

export class RefreshController {
  private rt: RefreshRuntime | null = null;
  private timer: { handle: unknown } | null = null;
  private ticker: { handle: unknown } | null = null;
  private intervalS: AutoRefreshSeconds | null = null;
  private startedAt = 0;
  private lastAt = 0;
  private nextAt: number | null = null;
  private pending = false;
  private failures = 0;
  private cooldownUntil = 0;
  private stopped: StopReason | null = null;
  private lastFailure: RenderOutcome["failure"] = null;
  private lastSuccessAt: string | null = null;
  private lastFetchedAt: string | null = null;
  private lastRenderId: string | null = null;
  private retained: RetainedView | null = null;
  private snapshot: LiveSnapshot = INITIAL_SNAPSHOT;
  private readonly listeners = new Set<() => void>();

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  readonly getSnapshot = (): LiveSnapshot => this.snapshot;
  readonly getServerSnapshot = (): LiveSnapshot => INITIAL_SNAPSHOT;
  readonly getRetained = (): RetainedView | null => this.retained;
  readonly getServerRetained = (): RetainedView | null => null;

  /** Connect to a runtime (on mount). Idempotent; Strict Mode may call attach/detach twice. */
  attach(rt: RefreshRuntime): void {
    this.detach();
    this.rt = rt;
    this.reschedule();
  }

  /** Stop every timer (on unmount). State such as the chosen interval is kept for a re-attach. */
  detach(): void {
    this.clearTimer();
    this.clearTicker();
    this.rt = null;
  }

  /** Number of live timers (tests: never more than one refresh timer plus one countdown). */
  timerCount(): number {
    return (this.timer ? 1 : 0) + (this.ticker ? 1 : 0);
  }

  enable(seconds: AutoRefreshSeconds): void {
    if (this.rt === null) return;
    const now = this.rt.now();
    this.intervalS = seconds;
    this.startedAt = now;
    this.lastAt = now;
    this.stopped = null;
    this.reschedule();
  }

  disable(): void {
    if (this.intervalS === null && this.stopped === null) return;
    this.intervalS = null;
    this.stopped = null;
    this.reschedule();
  }

  /** Whether a manual refresh is allowed now (no pending request, no server cooldown). */
  canRefresh(): boolean {
    return this.rt !== null && !this.pending && this.rt.now() >= this.cooldownUntil;
  }

  /** Manual Refresh: refused while a request is pending or a server-enforced cooldown runs. */
  requestRefresh(): boolean {
    if (this.rt === null || !this.canRefresh()) return false;
    this.lastAt = this.rt.now();
    this.rt.refresh();
    this.reschedule();
    return true;
  }

  setPending(pending: boolean): void {
    if (pending === this.pending) return;
    // A refresh counts from its completion: when a pending render settles, restart the interval.
    if (this.pending && !pending && this.rt !== null) this.lastAt = this.rt.now();
    this.pending = pending;
    this.reschedule();
  }

  /** A server render arrived (each render reports once; repeats are ignored). */
  observe(outcome: RenderOutcome): void {
    if (outcome.renderId === this.lastRenderId) return;
    this.lastRenderId = outcome.renderId;
    this.lastFetchedAt = outcome.fetchedAt;
    if (outcome.cooldownUntil !== null) this.cooldownUntil = Math.max(this.cooldownUntil, outcome.cooldownUntil);
    this.lastFailure = outcome.failure;
    if (outcome.failure === null) {
      this.failures = 0;
      this.lastSuccessAt = outcome.fetchedAt;
    } else if (outcome.failure.cls === "fatal") {
      if (this.intervalS !== null) {
        this.intervalS = null;
        this.stopped = "fatal";
      }
    } else if (outcome.failure.cls === "transient") {
      this.failures += 1;
    }
    this.reschedule();
  }

  visibilityChanged(): void {
    this.reschedule();
  }

  /** Keep the content of a fully successful render, to show as stale if a later refresh fails. */
  retain(view: RetainedView): void {
    if (this.retained !== null && this.retained.viewKey === view.viewKey && this.retained.fetchedAt === view.fetchedAt && this.retained.content === view.content) return;
    this.retained = view;
    this.emit();
  }

  private clearTimer(): void {
    if (this.timer !== null) this.rt?.clearTimer(this.timer.handle);
    this.timer = null;
    this.nextAt = null;
  }

  private clearTicker(): void {
    if (this.ticker !== null) this.rt?.clearTimer(this.ticker.handle);
    this.ticker = null;
  }

  private setTimer(delayMs: number): void {
    const rt = this.rt;
    if (rt === null) return;
    const slot: { handle: unknown } = { handle: null };
    slot.handle = rt.setTimer(() => {
      if (this.timer !== slot) return; // superseded
      this.timer = null;
      this.reschedule();
    }, delayMs);
    this.timer = slot;
    this.nextAt = rt.now() + delayMs;
  }

  /** A one-second countdown while a cooldown runs (display only; it sends nothing). */
  private syncTicker(): void {
    const rt = this.rt;
    if (rt === null) return;
    const remaining = this.cooldownUntil - rt.now();
    if (remaining <= 0) {
      this.clearTicker();
      return;
    }
    if (this.ticker !== null) return;
    const slot: { handle: unknown } = { handle: null };
    slot.handle = rt.setTimer(
      () => {
        if (this.ticker !== slot) return;
        this.ticker = null;
        this.syncTicker();
        this.emit();
      },
      Math.min(1_000, remaining),
    );
    this.ticker = slot;
  }

  private reschedule(): void {
    this.clearTimer();
    const rt = this.rt;
    if (rt !== null) {
      this.syncTicker();
      if (this.intervalS !== null) {
        const now = rt.now();
        const d = decideRefresh({
          intervalMs: this.intervalS * 1000,
          startedAt: this.startedAt,
          lastAt: this.lastAt,
          now,
          pending: this.pending,
          hidden: rt.isHidden(),
          cooldownUntil: this.cooldownUntil,
          failures: this.failures,
        });
        if (d.action === "stop") {
          this.intervalS = null;
          this.stopped = "max_duration";
        } else if (d.action === "refresh") {
          this.lastAt = now;
          rt.refresh();
          this.setTimer(Math.max(this.intervalS * 1000, MIN_INTERVAL_MS));
        } else if (d.action === "wait") {
          this.setTimer(d.delayMs);
        }
        // "pause": no timer while the tab is hidden; visibilityChanged() resumes.
      }
    }
    this.emit();
  }

  private emit(): void {
    const now = this.rt?.now() ?? 0;
    this.snapshot = {
      intervalS: this.intervalS,
      stopped: this.stopped,
      hidden: this.rt?.isHidden() ?? false,
      pending: this.pending,
      failures: this.failures,
      cooldownRemainingS: this.rt === null ? 0 : Math.max(0, Math.ceil((this.cooldownUntil - now) / 1000)),
      cooldownUntil: this.cooldownUntil > now ? this.cooldownUntil : 0,
      lastFailure: this.lastFailure,
      lastSuccessAt: this.lastSuccessAt,
      lastFetchedAt: this.lastFetchedAt,
      nextAt: this.nextAt,
    };
    for (const l of this.listeners) l();
  }
}
