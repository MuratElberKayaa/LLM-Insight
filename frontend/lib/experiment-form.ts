/**
 * Phase 4D form parsing for the experiment Server Action. Pure (no I/O), unit-tested directly.
 * Everything in the FormData is untrusted: each field is read once, as a string, checked
 * against the rules of `POST /api/v1/experiments` (the backend stays authoritative) and turned
 * into its exact JSON body.
 *
 * Mock only (ADR-25, ADR-26): `mode` is the constant "mock" here and is never read from the
 * form, so no submitted value (a hidden field, a crafted POST) can request a real provider.
 */

import { InexactNumber, LosslessInt, parseJsonLossless } from "./json";
import { isProjectId } from "./project";
import type { Parsed } from "./review-forms";

/** The ingest identifier formats the backend applies to prompt versions and models. */
const PROMPT_VERSION_RE = /^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,63}$/;
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$/;
const RECORD_ID_RE = /^[1-9][0-9]{0,15}$/;
/** > 0, at most 10 integer digits and 10 decimal places (NUMERIC(20,10)). */
const BUDGET_RE = /^(?:0|[1-9]\d{0,9})(?:\.\d{1,10})?$/;

export const EXPERIMENT_MODE = "mock";

export interface ExperimentInput {
  project: string;
  body: {
    dataset_id: number;
    prompt_version_a: string;
    prompt_version_b: string;
    model: string;
    mode: typeof EXPERIMENT_MODE;
    budget_usd?: number;
  };
}

function fail<T>(field: string, message: string): Parsed<T> {
  return { ok: false, message, fields: [field] };
}

function str(fd: FormData, name: string): string {
  const v = fd.get(name);
  return typeof v === "string" ? v : "";
}

/** The optional budget: "" → none; otherwise a decimal that a JSON number carries exactly. */
export function parseBudget(text: string): Parsed<number | null> {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  if (!BUDGET_RE.test(t)) return fail("budget_usd", "The budget must be a positive amount in USD with at most 10 decimal places, such as 0.01.");
  const value = parseJsonLossless(t, { refuseInexact: true });
  if (value instanceof InexactNumber || value instanceof LosslessInt || typeof value !== "number") {
    return fail("budget_usd", "The budget has more digits than this form can send exactly; use fewer significant digits.");
  }
  if (!(value > 0)) return fail("budget_usd", "The budget must be greater than 0.");
  return { ok: true, value };
}

export function parseExperimentForm(fd: FormData): Parsed<ExperimentInput> {
  const project = str(fd, "project");
  if (!isProjectId(project)) return fail("project", "The project of this page is not valid.");
  const datasetId = str(fd, "dataset_id");
  if (!RECORD_ID_RE.test(datasetId) || BigInt(datasetId) > BigInt(Number.MAX_SAFE_INTEGER)) return fail("dataset_id", "Choose a dataset.");
  const a = str(fd, "prompt_version_a");
  if (!PROMPT_VERSION_RE.test(a)) return fail("prompt_version_a", "Choose prompt version A.");
  const b = str(fd, "prompt_version_b");
  if (!PROMPT_VERSION_RE.test(b)) return fail("prompt_version_b", "Choose prompt version B.");
  if (a === b) return { ok: false, message: "Prompt versions A and B must differ.", fields: ["prompt_version_a", "prompt_version_b"] };
  const model = str(fd, "model");
  if (!MODEL_RE.test(model)) return fail("model", "Choose a model.");
  const budget = parseBudget(str(fd, "budget_usd"));
  if (!budget.ok) return budget;
  // The explicit start: nothing runs unless the person confirmed a mock-only, synthetic run.
  if (str(fd, "confirm_mock") !== "yes") return fail("confirm_mock", "Tick the box to confirm a mock-only run with SYNTHETIC results.");
  return {
    ok: true,
    value: {
      project,
      body: {
        dataset_id: Number(datasetId),
        prompt_version_a: a,
        prompt_version_b: b,
        model,
        mode: EXPERIMENT_MODE,
        ...(budget.value === null ? {} : { budget_usd: budget.value }),
      },
    },
  };
}
