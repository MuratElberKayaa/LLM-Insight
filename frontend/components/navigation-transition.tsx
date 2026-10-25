"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useTransition, type MouseEvent, type ReactNode } from "react";

/**
 * Filter navigation that keeps the current render on screen (dimmed, `aria-busy`) while the
 * server renders the new query, instead of flashing a skeleton. Links are real anchors, so
 * every control also works without JavaScript and can be opened in a new tab.
 */

interface TransitionState {
  pending: boolean;
  navigate: (href: string) => void;
  refresh: () => void;
}

const Ctx = createContext<TransitionState | null>(null);

export function NavigationTransition({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const value: TransitionState = {
    pending,
    navigate: (href) => startTransition(() => router.push(href, { scroll: false })),
    refresh: () => startTransition(() => router.refresh()),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function useNav(): TransitionState | null {
  return useContext(Ctx);
}

function isPlainClick(e: MouseEvent<HTMLAnchorElement>): boolean {
  return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

export function FilterLink({ href, current, children, label }: { href: string; current: boolean; children: ReactNode; label?: string }) {
  const nav = useNav();
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
  const nav = useNav();
  return (
    <button
      type="button"
      onClick={() => nav?.refresh()}
      disabled={nav?.pending}
      className="inline-flex min-h-8 items-center gap-1.5 rounded-md border border-line px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink disabled:cursor-progress disabled:opacity-60"
    >
      <span aria-hidden="true">↻</span>
      {nav?.pending ? "Refreshing…" : "Refresh"}
    </button>
  );
}

/** Wraps the data region: dims it and sets `aria-busy` while a filter navigation runs. */
export function PendingRegion({ children }: { children: ReactNode }) {
  const nav = useNav();
  const pending = nav?.pending ?? false;
  return (
    <div aria-busy={pending} data-pending={pending ? "true" : "false"} className="transition-opacity duration-150 data-[pending=true]:opacity-55">
      {children}
    </div>
  );
}
