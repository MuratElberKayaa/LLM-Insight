"use client";

import { useEffect, useId, useRef, useState } from "react";
import { AUTO_REFRESH_INTERVALS_S, AUTO_REFRESH_MAX_MS, decideRefresh, intervalLabel, parseInterval, type AutoRefreshSeconds } from "../lib/auto-refresh";
import { useNavigationState } from "./navigation-transition";

/**
 * Off by default. When on, re-runs the current server render (same URL, same filters) every
 * N seconds while the tab is visible, never while another request is pending, and switches
 * itself off after 30 minutes. See lib/auto-refresh.ts.
 */
export function AutoRefresh({ disabledReason }: { disabledReason?: string | undefined }) {
  const nav = useNavigationState();
  const id = useId();
  const [seconds, setSeconds] = useState<AutoRefreshSeconds | null>(null);
  const [stopped, setStopped] = useState(false);
  const startedAt = useRef(0);
  const lastAt = useRef(0);
  const pendingRef = useRef(false);
  const wasPending = useRef(false);
  const pending = nav?.pending ?? false;
  const refresh = nav?.refresh;

  // A refresh counts from its completion: when a pending render settles, restart the interval.
  useEffect(() => {
    pendingRef.current = pending;
    if (wasPending.current && !pending) lastAt.current = Date.now();
    wasPending.current = pending;
  }, [pending]);

  const active = seconds !== null && disabledReason === undefined && refresh !== undefined;

  useEffect(() => {
    if (!active || refresh === undefined) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      const d = decideRefresh({
        intervalMs: seconds * 1000,
        startedAt: startedAt.current,
        lastAt: lastAt.current,
        now: Date.now(),
        pending: pendingRef.current,
        hidden: document.visibilityState === "hidden",
      });
      if (d.action === "stop") {
        setSeconds(null);
        setStopped(true);
        return;
      }
      if (d.action === "refresh") {
        lastAt.current = Date.now();
        refresh();
        timer = setTimeout(tick, seconds * 1000);
        return;
      }
      timer = setTimeout(tick, d.delayMs);
    };
    timer = setTimeout(tick, seconds * 1000);
    return () => clearTimeout(timer);
  }, [active, seconds, refresh]);

  const status =
    disabledReason !== undefined
      ? disabledReason
      : seconds !== null
        ? `Every ${intervalLabel(seconds)} while this tab is visible; stops after ${AUTO_REFRESH_MAX_MS / 60_000} min. Polling, not live.`
        : stopped
          ? `Stopped after ${AUTO_REFRESH_MAX_MS / 60_000} minutes.`
          : "Off. Data changes only when you refresh.";

  return (
    <div className="flex flex-col items-start gap-1" data-testid="auto-refresh">
      <label htmlFor={id} className="flex items-center gap-2 text-xs font-medium text-ink-2">
        Auto-refresh
        <select
          id={id}
          value={seconds === null ? "off" : String(seconds)}
          disabled={disabledReason !== undefined}
          aria-describedby={`${id}-status`}
          onChange={(e) => {
            const next = parseInterval(e.target.value);
            if (next !== null) {
              startedAt.current = Date.now();
              lastAt.current = Date.now();
            }
            setStopped(false);
            setSeconds(next);
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
      <p id={`${id}-status`} className="max-w-64 text-[11px] text-ink-3" aria-live="polite">
        {status}
      </p>
    </div>
  );
}
