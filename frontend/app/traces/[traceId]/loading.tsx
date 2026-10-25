import { Skeleton } from "../../../components/states";

export default function Loading() {
  return (
    <div role="status" aria-label="Loading trace" data-testid="trace-loading">
      <Skeleton className="h-4 w-28" />
      <Skeleton className="mt-4 h-8 w-32" />
      <Skeleton className="mt-3 h-4 w-80 max-w-full" />
      <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="space-y-2">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
        <Skeleton className="h-72" />
      </div>
      <span className="sr-only">Loading trace…</span>
    </div>
  );
}
