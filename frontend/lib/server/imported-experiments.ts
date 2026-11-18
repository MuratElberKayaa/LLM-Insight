import "server-only";
import { importedListDecoder, importedReportDecoder, type ImportedReport } from "../api/imported-experiments";
import type { ApiResult } from "../api/result";
import type { ProjectParam } from "../project";
import { experimentPath, getJson, type BackendDeps } from "./backend";
import { pageScope, requireProject } from "./context";

export async function loadImportedExperiments(project: ProjectParam, deps: BackendDeps = {}) {
  const scope = pageScope(project);
  if (!scope.ok) return { ok: false as const, error: scope.error };
  const result = await getJson(scope.config, "/api/v1/imported-experiments", { project: scope.projectId }, {}, importedListDecoder, deps);
  if (result.ok && result.data.items.some(r => String(r.project) !== scope.projectId)) return { ok: false as const, error: { kind: "malformed" as const, detail: "response for another project" } };
  return result;
}

export async function loadImportedReport(project: ProjectParam, id: string, deps: BackendDeps = {}): Promise<ApiResult<ImportedReport>> {
  const scope = pageScope(project);
  if (!scope.ok) return { ok: false, error: scope.error };
  if (experimentPath(id) === null) return { ok: false, error: { kind: "invalid_request", fields: ["experiment_id"] } };
  const result = requireProject(await getJson(scope.config, `/api/v1/imported-experiments/${id}`, { project: scope.projectId }, {}, importedReportDecoder, deps), scope.projectId);
  if (result.ok && String(result.data.id) !== id) return { ok: false, error: { kind: "malformed", detail: "response for another experiment" } };
  return result;
}
