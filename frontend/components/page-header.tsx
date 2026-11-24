import type { ReactNode } from "react";

/** Page header (UI-R1): an optional eyebrow naming the product area, the one `h1`, a description and a meta line. */
export function PageHeader({ title, description, meta, actions, eyebrow }: { title: string; description?: ReactNode; meta?: ReactNode; actions?: ReactNode; eyebrow?: string }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-line pb-5">
      <div className="min-w-0">
        {eyebrow && <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">{eyebrow}</p>}
        <h1 className="text-[26px] font-semibold leading-tight tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-ink-2">{description}</p>}
        {meta && <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-3">{meta}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Section heading inside a page. */
export function SectionHeading({ id, title, description }: { id: string; title: string; description?: ReactNode }) {
  return (
    <div className="mb-3">
      <h2 id={id} className="text-[15px] font-semibold tracking-tight text-ink">
        {title}
      </h2>
      {description && <p className="mt-0.5 text-sm text-ink-3">{description}</p>}
    </div>
  );
}

/** UI-R1: a titled group of KPI cards (an `h2` with a short rule), used on the overview. */
export function MetricGroup({ id, title, description, children }: { id: string; title: string; description?: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="min-w-0">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={id} className="text-[13px] font-semibold uppercase tracking-[0.1em] text-ink-2">
          {title}
        </h2>
        {description && <p className="text-xs text-ink-3">{description}</p>}
      </div>
      {children}
    </section>
  );
}
