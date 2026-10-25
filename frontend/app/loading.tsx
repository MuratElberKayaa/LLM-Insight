import { Skeleton } from "../components/states";

/** First-load skeleton (filter navigations keep the previous render instead). */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading overview" data-testid="overview-loading">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <Skeleton className="mt-6 h-10 w-full max-w-2xl" />
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-36" />
        ))}
      </div>
      <div className="mt-8 grid grid-cols-1 gap-4 xl:grid-cols-3">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-72" />
        ))}
      </div>
      <span className="sr-only">Loading overview…</span>
    </div>
  );
}
