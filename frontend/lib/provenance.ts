/**
 * Provenance presentation (METRICS_CATALOG.md §Provenance, ADR-21, T48). Pure functions over
 * the backend's own buckets: the frontend never re-classifies a row, never counts mixed or
 * unknown as real, and never labels an unfiltered headline "real".
 */

import type { TraceProvenance, UsageProvenance } from "./api/types";

export const PROVENANCE_LABEL: Record<TraceProvenance, string> = {
  real: "Real",
  synthetic: "Synthetic",
  mixed: "Mixed",
  unknown: "Unknown",
};

export const PROVENANCE_DESCRIPTION: Record<TraceProvenance, string> = {
  real: "Labeled real by the instrumentation (asserted, not verified).",
  synthetic: "Mock provider or offline fixtures; not a real provider call.",
  mixed: "A trace containing both real and synthetic, or labeled and unknown, inference spans.",
  unknown: "No provenance claim, or a real claim the server downgraded.",
};

export type DataNoticeKind =
  | "empty"
  | "synthetic_only"
  | "contains_synthetic"
  | "unlabeled_only"
  | "contains_unknown"
  | "real_only";

export interface DataNotice {
  kind: DataNoticeKind;
  title: string;
  body: string;
}

/**
 * The banner above the KPIs, from the trace and usage provenance counts of the window.
 * Synthetic data is always called out; "real" is only ever stated as an instrumentation claim.
 */
export function dataNotice(traces: Record<TraceProvenance, number>, attempts: Record<UsageProvenance, number>): DataNotice {
  const synthetic = traces.synthetic + attempts.synthetic;
  const real = traces.real + attempts.real;
  const mixed = traces.mixed;
  const unknown = traces.unknown + attempts.unknown;
  const total = synthetic + real + mixed + unknown;

  if (total === 0) {
    return {
      kind: "empty",
      title: "No telemetry in this window",
      body: "The backend returned no traces and no inference attempts for this project and time range.",
    };
  }
  if (synthetic > 0 && real === 0 && mixed === 0 && unknown === 0) {
    return {
      kind: "synthetic_only",
      title: "Synthetic telemetry only",
      body: "Every trace and inference attempt in this window is labeled synthetic (mock provider or offline fixtures). None of these numbers describe real provider traffic.",
    };
  }
  if (synthetic > 0 || mixed > 0) {
    return {
      kind: "contains_synthetic",
      title: "Window includes synthetic telemetry",
      body: "Totals below are across all provenance. Use the provenance filter or the breakdown table to separate real, synthetic, mixed and unknown data.",
    };
  }
  if (real === 0) {
    return {
      kind: "unlabeled_only",
      title: "Provenance unknown",
      body: "No trace or attempt in this window carries a provenance claim the server accepted. Treat these numbers as unlabeled.",
    };
  }
  if (unknown > 0) {
    return {
      kind: "contains_unknown",
      title: "Window includes unlabeled telemetry",
      body: "Part of this window has unknown provenance. Totals below are across all provenance; unknown data is never counted as real.",
    };
  }
  return {
    kind: "real_only",
    title: "Labeled real by the instrumentation",
    body: "The instrumentation labeled this data real. That label is an assertion and is not verified by the server.",
  };
}
