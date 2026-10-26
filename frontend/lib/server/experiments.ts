import "server-only";

import { experimentListDecoder, experimentOptionsDecoder, experimentReportDecoder } from "../api/experiment-schema";
import type { ExperimentList, ExperimentOptions, ExperimentReport } from "../api/experiment-types";
import type { ApiResult } from "../api/result";
import { caseListDecoder } from "../api/review-schema";
import type { DatasetList, TestCase } from "../api/review-types";
import type { ExperimentInput } from "../experiment-form";
import type { ProjectParam } from "../project";
import { datasetCasesPath, experimentPath, getJson, sendJson, type BackendDeps } from "./backend";
import type { DashboardConfig } from "./config";
import { failedContext, loadProjects, pageScope, type ProjectContext } from "./context";
import { listDatasets } from "./datasets";

/**
 * Phase 4D: mock A/B experiments (ARCHITECTURE.md §5.9–§5.10). Reads for the experiment pages
 * and the one write, `POST /api/v1/experiments`, which only the Server Action calls after an
 * explicit, confirmed submission. No render and no page load starts a run.
 */

export const EXPERIMENT_LIST_LIMIT = "200";
/** The backend stops a run after 15 s (`RUN_TIME_LIMIT_S`); this deadline is longer, so a
 * finished run's answer always arrives instead of an "outcome unknown". */
export const EXPERIMENT_RUN_TIMEOUT_MS = 30_000;

export interface ExperimentsPageData extends ProjectContext {
  fetchedAt: Date;
  experiments: ApiResult<ExperimentList>;
}

export async function loadExperimentsPage(project: ProjectParam, now: Date = new Date(), deps: BackendDeps = {}): Promise<ExperimentsPageData> {
  const scope = pageScope(project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, experiments: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const [experiments, projects] = await Promise.all([
    getJson(config, "/api/v1/experiments", { project: projectId }, { limit: EXPERIMENT_LIST_LIMIT }, experimentListDecoder, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, experiments };
}

export interface ExperimentSetupData extends ProjectContext {
  fetchedAt: Date;
  datasets: ApiResult<DatasetList>;
  options: ApiResult<ExperimentOptions>;
}

/** What the setup form offers: the project's datasets (with case counts) and the run options. */
export async function loadExperimentSetup(project: ProjectParam, now: Date = new Date(), deps: BackendDeps = {}): Promise<ExperimentSetupData> {
  const scope = pageScope(project);
  if (!scope.ok) {
    const error = { ok: false as const, error: scope.error };
    return { ...failedContext(), fetchedAt: now, datasets: error, options: error };
  }
  const { config, projectId, explicit } = scope;
  const [datasets, options, projects] = await Promise.all([
    listDatasets(config, projectId, deps),
    getJson(config, "/api/v1/experiment-options", { project: projectId }, {}, experimentOptionsDecoder, deps),
    loadProjects(config, deps),
  ]);
  return { projectId, explicit, projects, fetchedAt: now, datasets, options };
}

export interface ExperimentReportData extends ProjectContext {
  fetchedAt: Date;
  report: ApiResult<ExperimentReport>;
}

/** One experiment's stored report. The caller validates `experimentId` by format first. */
export async function loadExperimentReport(project: ProjectParam, experimentId: string, now: Date = new Date(), deps: BackendDeps = {}): Promise<ExperimentReportData> {
  const scope = pageScope(project);
  if (!scope.ok) return { ...failedContext(), fetchedAt: now, report: { ok: false, error: scope.error } };
  const { config, projectId, explicit } = scope;
  const path = experimentPath(experimentId);
  if (path === null) return { projectId, explicit, projects: null, fetchedAt: now, report: { ok: false, error: { kind: "invalid_request", fields: ["experiment_id"] } } };
  const [report, projects] = await Promise.all([getJson(config, path, { project: projectId }, {}, experimentReportDecoder, deps), loadProjects(config, deps)]);
  return { projectId, explicit, projects, fetchedAt: now, report };
}

export interface ExperimentCaseData extends ExperimentReportData {
  /** The case as stored in the experiment's dataset; null when it is no longer there. Not
   * loaded (null result) when the report failed or has no result for the case. */
  testCase: ApiResult<TestCase | null> | null;
}

/**
 * One case of one experiment: the report's results for it, plus the case's stored metadata,
 * read with the existing keyset listing (`after = id - 1`, `limit = 1`): no new endpoint, and
 * the case is accepted only when its id is exactly the one asked for.
 */
export async function loadExperimentCase(
  project: ProjectParam,
  experimentId: string,
  caseId: string,
  now: Date = new Date(),
  deps: BackendDeps = {},
): Promise<ExperimentCaseData> {
  const base = await loadExperimentReport(project, experimentId, now, deps);
  const scope = pageScope(project);
  if (!base.report.ok || !scope.ok || base.projectId === null) return { ...base, testCase: null };
  const id = Number(caseId);
  if (!base.report.data.results.some((r) => r.test_case_id === id)) return { ...base, testCase: null };
  const path = datasetCasesPath(String(base.report.data.experiment.dataset_id));
  if (path === null) return { ...base, testCase: null };
  const page = await getJson(scope.config, path, { project: base.projectId }, { after: String(id - 1), limit: "1" }, caseListDecoder, deps);
  if (!page.ok) return { ...base, testCase: page };
  const found = page.data.items.find((c) => c.id === id) ?? null;
  return { ...base, testCase: { ok: true, data: found } };
}

/** `POST /api/v1/experiments` (mode is always "mock", set by the form parser). */
export function createExperiment(config: DashboardConfig, input: ExperimentInput, deps: BackendDeps = {}): Promise<ApiResult<ExperimentReport>> {
  const timeoutMs = Math.max(config.timeoutMs, EXPERIMENT_RUN_TIMEOUT_MS);
  return sendJson(config, "POST", "/api/v1/experiments", input.project, input.body, experimentReportDecoder, deps, { timeoutMs });
}
