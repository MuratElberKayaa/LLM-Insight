import Link from "next/link";

interface NavItem {
  label: string;
  href?: string;
  note?: string;
}

// Only built pages are links. Planned pages are listed, clearly marked, and not navigable.
const ITEMS: NavItem[] = [
  { label: "Overview", href: "/" },
  { label: "Traces", note: "Not built yet" },
  { label: "Models", note: "Not built yet" },
  { label: "Failure reviews", note: "Not built yet" },
  { label: "Experiments", note: "Not built yet" },
];

export function SideNav({ current }: { current: string }) {
  return (
    <nav aria-label="Primary" className="flex gap-1 overflow-x-auto px-3 py-2 lg:flex-col lg:overflow-visible lg:px-3 lg:py-4">
      {ITEMS.map((item) =>
        item.href ? (
          <Link
            key={item.label}
            href={item.href}
            aria-current={item.href === current ? "page" : undefined}
            className="flex shrink-0 items-center rounded-md px-3 py-2 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink aria-[current=page]:bg-raised aria-[current=page]:text-ink aria-[current=page]:shadow-[inset_2px_0_0_var(--color-series-1)]"
          >
            {item.label}
          </Link>
        ) : (
          <span
            key={item.label}
            aria-disabled="true"
            className="flex shrink-0 cursor-not-allowed items-center justify-between gap-3 rounded-md px-3 py-2 text-sm text-ink-3"
          >
            {item.label}
            <span className="hidden text-[11px] uppercase tracking-wide lg:inline">{item.note}</span>
            <span className="sr-only">({item.note})</span>
          </span>
        ),
      )}
    </nav>
  );
}
