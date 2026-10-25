"use server";

/**
 * Phase 4B Server Actions for human failure reviews (CR-03). Each action is one explicit write
 * by a person submitting a form; none runs on page load, and nothing here derives a review
 * from diagnostics or telemetry. Every argument comes from an untrusted POST: it is parsed and
 * validated again here (lib/review-forms.ts) before the one allowlisted backend call, and the
 * backend validates it once more. The bearer token never leaves the server (CR-06).
 *
 * CSRF: Next.js rejects Server Actions whose Origin does not match the Host; proxy.ts also
 * requires the Origin, `Sec-Fetch-Site: same-origin` when sent, and the `Next-Action` header,
 * which a cross-site HTML form cannot send (ARCHITECTURE.md §5.8).
 */

import "server-only";

import { refresh } from "next/cache";
import { invalidForm, writeFailure, type ActionState } from "../action-state";
import { parseDecisionForm, parseReviewForm } from "../review-forms";
import { readConfig } from "../server/config";
import { createReview, updateReview } from "../server/reviews";

function config() {
  const cfg = readConfig();
  return cfg.ok ? cfg.config : null;
}

export async function submitReview(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const parsed = parseReviewForm(formData);
  if (!parsed.ok) return invalidForm(parsed.message, parsed.fields);
  const cfg = config();
  if (cfg === null) return invalidForm("The dashboard server is not configured; nothing was stored.", []);
  const result = await createReview(cfg, parsed.value);
  if (!result.ok) return writeFailure(result.error, "the review");
  refresh();
  const masked = result.data.redaction.masked;
  return {
    status: "success",
    message: `Review #${result.data.id} recorded (open, decision pending). It is a human hypothesis, not a verified root cause.`,
    masked: { evidence: masked.evidence, hypothesis: masked.hypothesis },
    link: null,
  };
}

export async function submitDecision(_prev: ActionState, formData: FormData): Promise<ActionState> {
  const parsed = parseDecisionForm(formData);
  if (!parsed.ok) return invalidForm(parsed.message, parsed.fields);
  const cfg = config();
  if (cfg === null) return invalidForm("The dashboard server is not configured; nothing was stored.", []);
  const result = await updateReview(cfg, parsed.value);
  if (!result.ok) return writeFailure(result.error, "the decision");
  refresh();
  return { status: "success", message: `Review #${result.data.id} updated.`, masked: null, link: null };
}
