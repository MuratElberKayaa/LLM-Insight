import { Skeleton } from "../../components/states";

/** First-load skeleton of the model comparison (filter navigations keep the previous render instead). */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading model comparison" data-testid="models-loading">
      <Skeleton className="h-8 w-56" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <Skeleton className="mt-6 h-10 w-full max-w-3xl" />
      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-56 w-full" />
      </div>
      <div className="mt-6 space-y-2">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
      <span className="sr-only">Loading model comparison…</span>
    </div>
  );
}
