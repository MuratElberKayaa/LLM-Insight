"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, useTransition, type MouseEvent, type ReactNode } from "react";
import { INITIAL_SNAPSHOT, RefreshController, type LiveSnapshot, type RenderOutcome } from "../lib/auto-refresh";

/**
 * Filter navigation that keeps the current render on screen (dimmed, `aria-busy`) while the
 * server renders the new query, instead of flashing a skeleton. Links are real anchors, so
 * every control also works without JavaScript and can be opened in a new tab.
 *
 * Phase 6C-C: the provider also owns the page's refresh controller (lib/auto-refresh.ts). The
 * server page passes the outcome of its backend reads as `outcome`; every re-render (manual or
 * automatic refresh, filter navigation) reports a new one, so a 429 cooldown, the failure
 * backoff and "stop on a fatal failure" apply to the timer and the Refresh button alike.
 */

interface TransitionState {
  pending: boolean;
  navigate: (href: string) => void;
  /** Manual refresh; refused (returns false) during a server-enforced cooldown. */
  refresh: () => boolean;
  controller: RefreshController;
}

const Ctx = createContext<TransitionState | null>(null);

export function NavigationTransition({ children, outcome }: { children: ReactNode; outcome?: RenderOutcome }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [controller] = useState(() => new RefreshController());
  const rerun = useCallback(() => startTransition(() => router.refresh()), [router]);

  useEffect(() => {
    const onVisibility = () => controller.visibilityChanged();
    controller.attach({
      now: () => Date.now(),
      setTimer: (fn, ms) => window.setTimeout(fn, ms),
      clearTimer: (handle) => window.clearTimeout(handle as number),
      isHidden: () => document.visibilityState === "hidden",
      refresh: rerun,
    });
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      controller.detach();
    };
  }, [controller, rerun]);

  useEffect(() => controller.setPending(pending), [controller, pending]);
  useEffect(() => {
    if (outcome !== undefined) controller.observe(outcome);
  }, [controller, outcome]);

  const value = useMemo<TransitionState>(
    () => ({
      pending,
      navigate: (href) => startTransition(() => router.push(href, { scroll: false })),
      refresh: () => controller.requestRefresh(),
      controller,
    }),
    [pending, router, controller],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useNavigationState(): TransitionState | null {
  return useContext(Ctx);
}

const noSubscribe = () => () => {};
const initial = () => INITIAL_SNAPSHOT;

/** The refresh controller's state (the initial state outside a NavigationTransition and on the server). */
export function useLiveSnapshot(controller: RefreshController | undefined): LiveSnapshot {
  return useSyncExternalStore(controller?.subscribe ?? noSubscribe, controller?.getSnapshot ?? initial, controller?.getServerSnapshot ?? initial);
}

function isPlainClick(e: MouseEvent<HTMLAnchorElement>): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

export function FilterLink({ href, current, children, label }: { href: string; current: boolean; children: ReactNode; label?: string }) {
  const nav = useNavigationState();
  return (
    <Link
      href={href}
      scroll={false}
      aria-current={current ? "true" : undefined}
      aria-label={label}
      onClick={(e) => {
        if (nav && isPlainClick(e)) {
          e.preventDefault();
          nav.navigate(href);
        }
      }}
      className="inline-flex min-h-8 items-center whitespace-nowrap rounded-md px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink aria-[current=true]:bg-raised aria-[current=true]:text-ink aria-[current=true]:shadow-[inset_0_-2px_0_var(--color-series-1)]"
    >
      {children}
    </Link>
  );
}

export function RefreshButton() {
  const nav = useNavigationState();
  const snap = useLiveSnapshot(nav?.controller);
  const cooling = snap.cooldownRemainingS > 0;
  return (
    <button
      type="button"
      onClick={() => nav?.refresh()}
      disabled={nav?.pending || cooling}
      data-testid="refresh-button"
      data-cooldown={cooling ? String(snap.cooldownRemainingS) : undefined}
      title={cooling ? "The backend asked the dashboard to wait (429 Retry-After); refreshing earlier would be refused." : undefined}
      className="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-line px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
    >
      <span aria-hidden="true">↻</span>
      {nav?.pending ? "Refreshing…" : cooling ? `Retry in ${snap.cooldownRemainingS} s` : "Refresh"}
    </button>
  );
}

/** Wraps the data region: dims it and sets `aria-busy` while a filter navigation runs. */
export function PendingRegion({ children }: { children: ReactNode }) {
  const nav = useNavigationState();
  const pending = nav?.pending ?? false;
  return (
    <div aria-busy={pending} data-pending={pending ? "true" : "false"} className="transition-opacity duration-150 data-[pending=true]:opacity-55">
      {children}
    </div>
  );
}
