/**
 * UI-R2: carrying page context between views, and the words of the sticky context bar.
 *
 * Overview → Traces keeps project, window, provenance, provider and model, because each has the
 * same meaning on both pages (ARCHITECTURE.md §5.11): provenance is the trace's own value for
 * trace-level metrics and for the list, and provider/model mean "a usage row of the trace with
 * that provider (and model)" on both. Nothing else is carried: the overview has no status filter
 * (the error link sets `status=error`, which is what the trace error rate counts), and the error
 * patterns, which ignore every filter but the window, keep their own links.
 */
import type { ApiResult } from "./api/result";
import type { ProjectList } from "./api/types";
import { formatUtcMinute } from "./format";
import { PROVENANCE_LABEL } from "./provenance";
import { DEFAULT_PAGE_SIZE, type ListState, type StatusFilter } from "./trace-query";
import { windowLabel, type OverviewState, type ProvenanceFilter, type WindowSpec } from "./time-range";

export function overviewTraceListState(state: OverviewState, status: StatusFilter = "all"): ListState {
  return {
    project: state.project,
    window: state.window,
    status,
    provenance: state.provenance,
    provider: state.provider ?? null,
    model: state.model ?? null,
    session: null,
    limit: DEFAULT_PAGE_SIZE,
    cursor: null,
    page: 1,
  };
}

export interface ContextEntry {
  key: string;
  label: string;
  value: string;
}

/** "#7 · name" from the project listing, "#7" when the name is not known, "not resolved" without an id. */
export function projectContext(projectId: string | null, projects: ApiResult<ProjectList> | null): string {
  if (projectId === null) return "not resolved";
  const item = projects?.ok ? projects.data.items.find((p) => String(p.id) === projectId) : undefined;
  return item ? `#${projectId} · ${item.name}` : `#${projectId}`;
}

export function provenanceContext(p: ProvenanceFilter): string {
  return p === "all" ? "All provenance" : `${PROVENANCE_LABEL[p]} only`;
}

/** The window as the backend echoed it when known, else the requested preset's name. */
export function windowContext(spec: WindowSpec, shown: { from: string; to: string } | null): string {
  const name = windowLabel(spec);
  return shown ? `${formatUtcMinute(shown.from)} → ${formatUtcMinute(shown.to)} UTC (${name})` : name;
}

export function scopeEntries(args: {
  projectId: string | null;
  projects: ApiResult<ProjectList> | null;
  window: WindowSpec;
  shown: { from: string; to: string } | null;
  provenance: ProvenanceFilter;
  provider?: string | null | undefined;
  model?: string | null | undefined;
  status?: StatusFilter | undefined;
}): ContextEntry[] {
  const out: ContextEntry[] = [
    { key: "project", label: "Project", value: projectContext(args.projectId, args.projects) },
    { key: "window", label: "Window", value: windowContext(args.window, args.shown) },
    { key: "provenance", label: "Provenance", value: provenanceContext(args.provenance) },
  ];
  if (args.status && args.status !== "all") out.push({ key: "status", label: "Status", value: args.status });
  if (args.provider) out.push({ key: "provider", label: "Provider", value: args.provider });
  if (args.model) out.push({ key: "model", label: "Model", value: args.model });
  return out;
}
