/**
 * Optional auto-refresh (Phase 3C): re-runs the page's current server render on a timer. It is
 * polling, not a live stream, and makes no freshness guarantee. Rules:
 * - off by default, chosen per tab, never stored;
 * - never overlapping: while any navigation or refresh is pending, the next one waits;
 * - at most one refresh per interval, counted from the last completed one;
 * - postponed while the tab is hidden;
 * - bounded: it switches itself off after AUTO_REFRESH_MAX_MS;
 * - pages pass a reason to disable it where a refresh would be misleading (later cursor pages).
 */

export const AUTO_REFRESH_INTERVALS_S = [30, 60, 300] as const;
export type AutoRefreshSeconds = (typeof AUTO_REFRESH_INTERVALS_S)[number];
export const AUTO_REFRESH_MAX_MS = 30 * 60_000;
/** How soon to look again while another request is still pending. */
export const PENDING_RECHECK_MS = 1_000;

export interface RefreshState {
  intervalMs: number;
  /** When auto-refresh was switched on. */
  startedAt: number;
  /** When the last refresh (or the switch-on) happened. */
  lastAt: number;
  now: number;
  pending: boolean;
  hidden: boolean;
}

export type RefreshDecision = { action: "refresh" } | { action: "wait"; delayMs: number } | { action: "stop" };

export function decideRefresh(s: RefreshState): RefreshDecision {
  if (s.now - s.startedAt >= AUTO_REFRESH_MAX_MS) return { action: "stop" };
  if (s.pending) return { action: "wait", delayMs: PENDING_RECHECK_MS };
  const due = s.lastAt + s.intervalMs;
  if (s.now < due) return { action: "wait", delayMs: due - s.now };
  if (s.hidden) return { action: "wait", delayMs: s.intervalMs };
  return { action: "refresh" };
}

export function parseInterval(value: string): AutoRefreshSeconds | null {
  return AUTO_REFRESH_INTERVALS_S.find((s) => String(s) === value) ?? null;
}

export function intervalLabel(seconds: AutoRefreshSeconds): string {
  return seconds < 60 ? `${seconds} s` : `${seconds / 60} min`;
}
