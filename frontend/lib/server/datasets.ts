import "server-only";

import type { ApiResult } from "../api/result";
import { caseListDecoder, createdCaseDecoder, createdDatasetDecoder, datasetListDecoder } from "../api/review-schema";
import type { CaseList, CreatedCase, Dataset, DatasetList } from "../api/review-types";
import type { ProjectParam } from "../project";
import type { CaseInput, DatasetInput } from "../review-forms";
import { datasetCasesPath, getJson, sendJson, type BackendDeps } from "./backend";
import type { DashboardConfig } from "./config";
import { failedContext, loadProjects, pageScope, type ProjectContext } from "./context";

/** Phase 4B: datasets and human-approved test cases (ARCHITECTURE.md §4.8, §4.10, §5.7). */

export const DATASET_LIST_LIMIT = "200";
export const CASE_PAGE_LIMIT = "50";

export function listDatasets(config: DashboardConfig, projectId: string, deps: BackendDeps = {}): Promise<ApiResult<DatasetList>> {
  return getJson(config, "/api/v1/datasets", { project: projectId }, { limit: DATASET_LIST_LIMIT }, datasetListDecoder, deps);
}

export interface DatasetsPageData extends ProjectContext {
  fetchedAt: Date;
  datasets: ApiResult<DatasetList>;
}

export async function loadDatasetsPage(project: ProjectParam, now: Date = new Date(), deps: BackendDeps = {}): Promise<DatasetsPageData> {
  const scope = pageScope(project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, datasets: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const [datasets, projects] = await Promise.all([listDatasets(config, projectId, deps), loadProjects(config, deps)]);
  return { projectId, explicit, projects, fetchedAt: now, datasets };
}

export interface DatasetCasesPageData extends ProjectContext {
  fetchedAt: Date;
  cases: ApiResult<CaseList>;
}

/** One page of a dataset's cases. The caller validates `datasetId` and `after` first. */
export async function loadDatasetCasesPage(
  project: ProjectParam,
  datasetId: string,
  after: string | null,
  now: Date = new Date(),
  deps: BackendDeps = {},
): Promise<DatasetCasesPageData> {
  const scope = pageScope(project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, cases: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const path = datasetCasesPath(datasetId);
  if (path === null) return { projectId, explicit, projects: null, fetchedAt: now, cases: { ok: false, error: { kind: "invalid_request", fields: ["dataset_id"] } } };
  const params: Record<string, string> = { limit: CASE_PAGE_LIMIT };
  if (after !== null) params.after = after;
  const [cases, projects] = await Promise.all([getJson(config, path, { project: projectId }, params, caseListDecoder, deps), loadProjects(config, deps)]);
  return { projectId, explicit, projects, fetchedAt: now, cases };
}

export function createDataset(config: DashboardConfig, input: DatasetInput, deps: BackendDeps = {}): Promise<ApiResult<Dataset>> {
  return sendJson(config, "POST", "/api/v1/datasets", input.project, input.body, createdDatasetDecoder, deps);
}

/** `POST /api/v1/datasets/{id}/cases` with `approved: true` only after the explicit approval. */
export async function createTestCase(config: DashboardConfig, input: CaseInput, deps: BackendDeps = {}): Promise<ApiResult<CreatedCase>> {
  const path = datasetCasesPath(input.datasetId);
  if (path === null) return { ok: false, error: { kind: "invalid_request", fields: ["dataset_id"] } };
  return sendJson(config, "POST", path, input.project, input.body, createdCaseDecoder, deps);
}
