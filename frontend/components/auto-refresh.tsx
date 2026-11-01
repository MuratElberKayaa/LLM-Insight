"use client";

import { useEffect, useId } from "react";
import { AUTO_REFRESH_INTERVALS_S, AUTO_REFRESH_MAX_MS, STOP_REASON, backoffMs, intervalLabel, parseInterval, type LiveSnapshot } from "../lib/auto-refresh";
import { formatUtcTime } from "../lib/format";
import { useLiveSnapshot, useNavigationState } from "./navigation-transition";

const MAX_MIN = AUTO_REFRESH_MAX_MS / 60_000;

/** The one-line state of the control (fixed copy; the failure kind only, never a body). */
export function autoRefreshStatus(snap: LiveSnapshot, disabledReason?: string): string {
  if (disabledReason !== undefined) return disabledReason;
  if (snap.intervalS === null) {
    if (snap.stopped === "max_duration") return `Stopped after ${MAX_MIN} minutes.`;
    if (snap.stopped === "fatal" && snap.lastFailure !== null) {
      return `Stopped: ${STOP_REASON[snap.lastFailure.kind]}. Repeating the same request would fail again; fix the cause, then refresh manually.`;
    }
    return "Off. Data changes only when you refresh.";
  }
  const parts = [`Every ${intervalLabel(snap.intervalS)} while this tab is visible; stops after ${MAX_MIN} min. Re-reads stored telemetry; not live.`];
  if (snap.hidden) parts.push("Paused while this tab is hidden.");
  // An absolute time, not a countdown: this line is a polite live region and must not re-announce every second.
  if (snap.cooldownUntil > 0) parts.push(`Waiting for the backend rate limit (429) until ${formatUtcTime(new Date(snap.cooldownUntil))}.`);
  else if (snap.failures > 0) parts.push(`The last refresh failed; next try in ${Math.round(backoffMs(snap.intervalS * 1000, snap.failures) / 1000)} s.`);
  return parts.join(" ");
}

/**
 * Off by default. When on, re-runs the current server render (same URL, same filters) every
 * N seconds while the tab is visible, never while another request is pending or a 429 cooldown
 * runs, backs off after failures, stops on failures a retry cannot fix, and switches itself
 * off after 30 minutes. See lib/auto-refresh.ts.
 */
export function AutoRefresh({ disabledReason }: { disabledReason?: string | undefined }) {
  const nav = useNavigationState();
  const controller = nav?.controller;
  const snap = useLiveSnapshot(controller);
  const id = useId();

  useEffect(() => {
    if (disabledReason !== undefined) controller?.disable();
  }, [controller, disabledReason]);

  return (
    <div className="flex flex-col items-start gap-1" data-testid="auto-refresh" data-interval={snap.intervalS ?? "off"} data-stopped={snap.stopped ?? undefined}>
      <label htmlFor={id} className="flex items-center gap-2 text-xs font-medium text-ink-2">
        Auto-refresh
        <select
          id={id}
          value={snap.intervalS === null ? "off" : String(snap.intervalS)}
          disabled={disabledReason !== undefined || controller === undefined}
          aria-describedby={`${id}-status`}
          onChange={(e) => {
            const next = parseInterval(e.target.value);
            if (next === null) controller?.disable();
            else controller?.enable(next);
          }}
          className="min-h-8 rounded-md border border-line bg-surface px-2 text-sm text-ink disabled:opacity-60"
        >
          <option value="off">Off</option>
          {AUTO_REFRESH_INTERVALS_S.map((s) => (
            <option key={s} value={String(s)}>
              Every {intervalLabel(s)}
            </option>
          ))}
        </select>
      </label>
      <p id={`${id}-status`} className="max-w-72 text-[11px] text-ink-3" aria-live="polite" data-testid="auto-refresh-status">
        {autoRefreshStatus(snap, disabledReason)}
      </p>
      {snap.lastSuccessAt !== null && (
        <p className="max-w-72 text-[11px] text-ink-3" data-testid="last-success">
          Last successful load {formatUtcTime(new Date(snap.lastSuccessAt))}
          {snap.lastFailure !== null ? " · the latest load failed" : ""}
        </p>
      )}
    </div>
  );
}
