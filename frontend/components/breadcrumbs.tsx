import Link from "next/link";

export interface Crumb {
  label: string;
  /** Omitted for the current page (the last crumb). */
  href?: string;
}

/** UI-R4: where a page sits (`nav` "Breadcrumb", the last item is the current page). */
export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-3 min-w-0" data-testid="breadcrumbs">
      <ol className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-ink-3">
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${c.label}-${i}`} className="flex min-w-0 items-center gap-1.5">
              {c.href && !last ? (
                <Link href={c.href} className="truncate underline-offset-2 hover:text-ink hover:underline">
                  {c.label}
                </Link>
              ) : (
                <span className="truncate text-ink-2" aria-current={last ? "page" : undefined}>
                  {c.label}
                </span>
              )}
              {!last && (
                <span aria-hidden="true" className="text-ink-3">
                  /
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
