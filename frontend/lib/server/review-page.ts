import "server-only";

import type { Diagnostics } from "../api/diagnostics";
import type { ApiResult } from "../api/result";
import type { DatasetList } from "../api/review-types";
import type { ProjectParam } from "../project";
import type { BackendDeps } from "./backend";
import { pageScope } from "./context";
import { listDatasets } from "./datasets";
import { loadDiagnostics } from "./reviews";

/**
 * Extra reads of the Phase 4B trace pages, made in parallel with the trace detail
 * (lib/server/traces.ts, unchanged). A failure here degrades only its own section.
 */

/** Diagnostics for the Trace Detail page; null when the page has no valid project. */
export async function loadTraceDiagnostics(traceId: string, project: ProjectParam, deps: BackendDeps = {}): Promise<ApiResult<Diagnostics> | null> {
  const scope = pageScope(project);
  if (!scope.ok) return null;
  return loadDiagnostics(scope.config, scope.projectId, traceId, deps);
}

/** Datasets for the test-case form; null when the page has no valid project. */
export async function loadDatasetChoices(project: ProjectParam, deps: BackendDeps = {}): Promise<ApiResult<DatasetList> | null> {
  const scope = pageScope(project);
  if (!scope.ok) return null;
  return listDatasets(scope.config, scope.projectId, deps);
}
