"use client";

/** Unexpected render failure. Shows no error message or stack (they could carry data). */
export default function OverviewError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-critical/50 bg-critical/10 px-6 py-6">
      <p className="text-sm font-semibold text-ink">The dashboard failed to render this page.</p>
      <p className="mt-1 text-sm text-ink-2">
        This is a dashboard error, not a backend response. Details are in the dashboard server log
        {error.digest ? ` (reference ${error.digest})` : ""}.
      </p>
      <button type="button" onClick={reset} className="mt-3 rounded-md border border-line px-3 py-1.5 text-sm text-ink-2 hover:bg-raised hover:text-ink">
        Try again
      </button>
    </div>
  );
}
