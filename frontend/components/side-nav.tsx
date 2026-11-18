"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { parseProjectParam, projectHref, projectLinkValue } from "../lib/project";
import { DatasetsIcon, ExperimentsIcon, ModelsIcon, OverviewIcon, TracesIcon } from "./icons";

interface NavItem {
  label: string;
  icon: ReactNode;
  href?: string;
  note?: string;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

// Grouped by the product loop (Observe → Evaluate). Only built pages are links; a planned
// page would be listed, clearly marked, and not navigable. The link text is the accessible
// name (icons are decorative).
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Observe",
    items: [
      { label: "Overview", href: "/", icon: <OverviewIcon /> },
      { label: "Traces", href: "/traces", icon: <TracesIcon /> },
      { label: "Models", href: "/models", icon: <ModelsIcon /> },
    ],
  },
  {
    label: "Evaluate",
    items: [
      // Phase 4B: failure reviews live on each trace page; approved test cases in datasets.
      { label: "Datasets", href: "/datasets", icon: <DatasetsIcon /> },
      // Phase 4D: mock A/B experiments on a dataset (SYNTHETIC results).
      { label: "Experiments", href: "/experiments", icon: <ExperimentsIcon /> },
    ],
  },
];

/** The current section: "/" only for the overview itself, otherwise the longest prefix. */
function isCurrent(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

const LINK =
  "group flex h-9 shrink-0 items-center gap-2.5 rounded-lg px-2.5 text-sm font-medium text-ink-2 hover:bg-raised/60 hover:text-ink aria-[current=page]:bg-raised aria-[current=page]:text-ink";

function NavLinks({ project }: { project: string | null }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary" className="flex gap-1 overflow-x-auto px-3 py-2 lg:flex-col lg:gap-0 lg:overflow-visible lg:py-2">
      {NAV_GROUPS.map((group) => (
        <div key={group.label} className="contents lg:flex lg:flex-col lg:gap-0.5">
          <p className="hidden px-2.5 pb-1.5 pt-4 text-[11px] font-semibold uppercase tracking-wider text-ink-3 lg:block">{group.label}</p>
          {group.items.map((item) =>
            item.href ? (
              <Link
                key={item.label}
                // Only a validated project id is carried over; filters, cursors and spans are not.
                href={projectHref(item.href, project)}
                aria-current={isCurrent(item.href, pathname) ? "page" : undefined}
                className={LINK}
              >
                <span className="text-ink-3 group-hover:text-ink-2 group-aria-[current=page]:text-series-1">{item.icon}</span>
                {item.label}
              </Link>
            ) : (
              <span key={item.label} aria-disabled="true" className="flex h-9 shrink-0 cursor-not-allowed items-center justify-between gap-3 rounded-lg px-2.5 text-sm text-ink-3">
                <span className="flex items-center gap-2.5">
                  {item.icon}
                  {item.label}
                </span>
                <span className="hidden text-[11px] uppercase tracking-wide lg:inline">{item.note}</span>
                <span className="sr-only">({item.note})</span>
              </span>
            ),
          )}
        </div>
      ))}
    </nav>
  );
}

/** Navigation that keeps the page's explicit `?project=` (Phase 3C). */
export function SideNav() {
  const params = useSearchParams();
  const all = params.getAll("project");
  const project = projectLinkValue(parseProjectParam(all.length > 1 ? all : (all[0] ?? undefined)));
  return <NavLinks project={project} />;
}

/** Suspense fallback (static renders without search parameters): the plain links. */
export function SideNavFallback() {
  return <NavLinks project={null} />;
}
