"use server";

/**
 * Phase 4D Server Action for mock A/B experiments (ADR-26). One explicit write: a person
 * chooses a dataset, two prompt versions and a model, confirms a mock-only run and presses the
 * start button; nothing runs on page load or navigation. The form is parsed and validated again
 * here (lib/experiment-form.ts), the mode is the constant "mock" (never read from the form),
 * and the backend validates everything once more and refuses `mode: real`. Same CSRF posture as
 * lib/actions/reviews.ts; the bearer token never leaves the server (CR-06).
 */

import "server-only";

import { refresh } from "next/cache";
import { invalidForm, writeFailure, type ActionState } from "../action-state";
import { parseExperimentForm } from "../experiment-form";
import { readConfig } from "../server/config";
import { createExperiment } from "../server/experiments";

function config() {
  const cfg = readConfig();
  return cfg.ok ? cfg.config : null;
}

export async function submitExperiment(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const parsed = parseExperimentForm(formData);
  if (!parsed.ok) return invalidForm(parsed.message, parsed.fields);
  const cfg = config();
  if (cfg === null) return invalidForm("The dashboard server is not configured; nothing was run.", []);
  const result = await createExperiment(cfg, parsed.value);
  if (!result.ok) return writeFailure(result.error, "the experiment");
  refresh();
  const { experiment, results, provenance } = result.data;
  return {
    status: "success",
    message: `Experiment #${experiment.id} ran in mock mode and stored ${results.length} results (${provenance.synthetic} SYNTHETIC, ${provenance.real} real). Open the results to see what was compared and what was not.`,
    masked: null,
    link: { href: `/experiments/${experiment.id}?project=${parsed.value.project}`, label: "Open the experiment results" },
  };
}
