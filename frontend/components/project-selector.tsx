import { describeFailure } from "../lib/api/result";
import type { ProjectItem } from "../lib/api/types";
import type { ProjectContext } from "../lib/server/context";

export type HiddenFields = readonly (readonly [name: string, value: string])[];

function optionLabel(p: ProjectItem): string {
  // Text only: React escapes it. Names are admin-chosen and may contain anything.
  return `${p.name} (#${p.id})`;
}

/**
 * Project switcher (Phase 3C). A plain GET form: it works without JavaScript, and switching is
 * a full page load of the target URL, so nothing rendered for the previous project survives
 * the switch. `hidden` carries the page's safe navigation state (filters and window), never a
 * cursor, page number or selected span, which belong to the previous project's data.
 */
export function ProjectSelector({ context, action, hidden }: { context: ProjectContext; action: string; hidden: HiddenFields }) {
  const current = context.projectId;
  if (current === null) return null;
  const listing = context.projects;
  const items = listing?.ok ? listing.data.items : [];
  // The backend lists at most `limit` projects by id; when more exist, say so in the list itself.
  const truncatedAt = listing?.ok && listing.data.truncated ? listing.data.limit : null;
  const known = items.find((p) => String(p.id) === current);
  return (
    <div className="mb-5 flex flex-wrap items-end gap-x-4 gap-y-2" data-testid="project-bar">
      <form method="get" action={action} className="flex flex-wrap items-end gap-2" aria-label="Switch project" data-testid="project-selector">
        {hidden.map(([name, value]) => (
          <input key={name} type="hidden" name={name} value={value} />
        ))}
        <label className="flex min-w-0 flex-col gap-1 text-xs font-medium uppercase tracking-wide text-ink-3">
          Project
          <select
            name="project"
            defaultValue={current}
            className="min-h-8 w-72 max-w-full rounded-md border border-line bg-surface px-2 text-sm normal-case tracking-normal text-ink"
            data-testid="project-select"
          >
            {!known && <option value={current}>{`#${current}${listing?.ok ? " (not in the listing)" : ""}`}</option>}
            {items.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {optionLabel(p)}
              </option>
            ))}
            {truncatedAt !== null && (
              <option disabled value="" data-testid="project-list-truncated-option">
                {`More projects exist: only the first ${truncatedAt} by id are listed`}
              </option>
            )}
          </select>
        </label>
        <button type="submit" className="min-h-8 rounded-md border border-line px-3 text-sm font-medium text-ink-2 hover:bg-raised hover:text-ink">
          Switch
        </button>
      </form>
      <p className="pb-1.5 text-xs text-ink-3" data-testid="project-current" data-project-id={current}>
        Showing project <span className="text-ink-2">#{current}</span>
        {known ? (
          <>
            {" · "}
            <span className="text-ink-2" data-testid="project-name">
              {known.name}
            </span>
          </>
        ) : null}
        {context.explicit ? "" : " (configured default)"}
      </p>
      {truncatedAt !== null && (
        <p className="w-full text-xs text-ink-3" data-testid="project-list-truncated">
          The project list is incomplete: only the first {truncatedAt} projects by id are listed. Open any other project with{" "}
          <code>?project=&lt;id&gt;</code>.
        </p>
      )}
      {listing && !listing.ok && (
        // One line, not a second alert: the page's own error state reports an outage.
        <p className="w-full text-xs text-ink-3" data-testid="project-list-error" data-error-kind={listing.error.kind}>
          Project list unavailable ({describeFailure(listing.error, "the project list").title.toLowerCase()}); the current project is unchanged.
        </p>
      )}
    </div>
  );
}
