import type { ReactNode } from "react";
import { describeFailure, type ApiFailure } from "../lib/api/result";

/** Empty population: explains why nothing is shown and how to produce data. */
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="status" className="rounded-lg border border-dashed border-line bg-surface px-6 py-10 text-center" data-state="empty">
      <p className="text-base font-semibold text-ink">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-xl text-sm text-ink-2">{children}</div>}
    </div>
  );
}

/** A failed backend read. Fixed copy per failure kind; no backend body or credential. */
export function ErrorState({ error, resource, compact = false }: { error: ApiFailure; resource: string; compact?: boolean }) {
  const copy = describeFailure(error, resource);
  return (
    <div
      role="alert"
      className={`rounded-lg border border-critical/50 bg-critical/10 ${compact ? "px-4 py-3" : "px-6 py-6"}`}
      data-state="error"
      data-error-kind={error.kind}
    >
      <p className="flex items-center gap-2 text-sm font-semibold text-ink">
        <span aria-hidden="true" className="text-critical-ink">
          ✕
        </span>
        {copy.title}
      </p>
      <p className="mt-1 text-sm text-ink-2">{copy.description}</p>
      {copy.hint && <p className="mt-1 text-xs text-ink-3">{copy.hint}</p>}
    </div>
  );
}

/** Inline notice (informational / caution). */
export function Notice({ tone, title, children, testId }: { tone: "info" | "caution"; title: string; children?: ReactNode; testId?: string }) {
  const cls = tone === "caution" ? "border-synthetic/60 bg-synthetic/10" : "border-line bg-surface";
  return (
    <div role="note" className={`rounded-lg border px-4 py-3 ${cls}`} data-testid={testId}>
      <p className="text-sm font-semibold text-ink">{title}</p>
      {children && <div className="mt-1 text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-md bg-raised ${className}`} />;
}
