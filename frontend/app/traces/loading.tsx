import { Skeleton } from "../../components/states";

/** First-load skeleton of the trace list (filter navigations keep the previous render instead). */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading traces" data-testid="traces-loading">
      <Skeleton className="h-8 w-40" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <Skeleton className="mt-6 h-10 w-full max-w-3xl" />
      <div className="mt-6 space-y-2">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
      <span className="sr-only">Loading traces…</span>
    </div>
  );
}
