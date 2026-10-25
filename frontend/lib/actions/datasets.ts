"use server";

/**
 * Phase 4B Server Actions for datasets and human-approved test cases (Trace-to-Testset). A
 * test case is sent only after the approval box was ticked, with the content the person typed;
 * nothing is copied from telemetry, which never stored prompts or completions (ADR-19). Same
 * validation and CSRF posture as lib/actions/reviews.ts.
 */

import "server-only";

import { refresh } from "next/cache";
import { invalidForm, writeFailure, type ActionState } from "../action-state";
import { parseCaseForm, parseDatasetForm } from "../review-forms";
import { readConfig } from "../server/config";
import { createDataset, createTestCase } from "../server/datasets";

function config() {
  const cfg = readConfig();
  return cfg.ok ? cfg.config : null;
}

export async function submitDataset(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const parsed = parseDatasetForm(formData);
  if (!parsed.ok) return invalidForm(parsed.message, parsed.fields);
  const cfg = config();
  if (cfg === null) return invalidForm("The dashboard server is not configured; nothing was stored.", []);
  const result = await createDataset(cfg, parsed.value);
  if (!result.ok) return writeFailure(result.error, "the dataset");
  refresh();
  return {
    status: "success",
    message: `Dataset #${result.data.id} created.`,
    masked: null,
    link: { href: `/datasets/${result.data.id}?project=${parsed.value.project}`, label: "Open the dataset" },
  };
}

export async function submitTestCase(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const parsed = parseCaseForm(formData);
  if (!parsed.ok) return invalidForm(parsed.message, parsed.fields);
  const cfg = config();
  if (cfg === null) return invalidForm("The dashboard server is not configured; nothing was stored.", []);
  const result = await createTestCase(cfg, parsed.value);
  if (!result.ok) return writeFailure(result.error, "the test case");
  refresh();
  return {
    status: "success",
    message: `Test case #${result.data.id} approved and stored in dataset #${result.data.dataset_id}. It has not been evaluated (evaluation is Phase 4C).`,
    masked: { sanitized_input: result.data.redaction.masked.sanitized_input },
    link: { href: `/datasets/${result.data.dataset_id}?project=${parsed.value.project}`, label: "Open the dataset" },
  };
}
