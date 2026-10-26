import { Skeleton } from "../../components/states";

/** First-load skeleton of the experiment pages. */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading experiments" data-testid="experiments-loading">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <Skeleton className="mt-6 h-24 w-full" />
      <div className="mt-6 space-y-2">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
      <span className="sr-only">Loading experiments…</span>
    </div>
  );
}
