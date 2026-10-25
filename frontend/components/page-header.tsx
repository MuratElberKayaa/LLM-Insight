import type { ReactNode } from "react";

export function PageHeader({ title, description, meta, actions }: { title: string; description?: ReactNode; meta?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-ink-2">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-3">{meta}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Section heading inside a page. */
export function SectionHeading({ id, title, description }: { id: string; title: string; description?: ReactNode }) {
  return (
    <div className="mb-3">
      <h2 id={id} className="text-base font-semibold text-ink">
        {title}
      </h2>
      {description && <p className="mt-0.5 text-sm text-ink-3">{description}</p>}
    </div>
  );
}
