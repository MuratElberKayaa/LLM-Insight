import { Skeleton } from "../../../components/states";

/** First-load skeleton of an experiment's results. */
export default function Loading() {
  return (
    <div role="status" aria-label="Loading experiment results" data-testid="experiment-loading">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="mt-6 h-16 w-full" />
      <Skeleton className="mt-4 h-28 w-full" />
      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
      <Skeleton className="mt-6 h-72 w-full" />
      <span className="sr-only">Loading experiment results…</span>
    </div>
  );
}
