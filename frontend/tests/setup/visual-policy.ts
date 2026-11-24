/**
 * Which visual-regression specs a Playwright run discovers (UI-R4 final stabilization).
 *
 * Baselines are per platform: `*-win32.png` (local msedge on Windows) and `*-linux.png` (the CI
 * runner, ubuntu-24.04 with Playwright's Chromium). Linux baselines are accepted by a person after
 * reviewing a capture from the CI runner (`.github/workflows/visual-baselines.yml`), and the
 * acceptance is recorded in `tests/e2e/visual-baselines.json`.
 *
 * - Locally (not CI), and in the capture run (`LLMI_VISUAL_LINUX=1`): every visual spec runs.
 * - In CI: an `accepted` spec must run, so every Windows baseline needs its Linux twin; a missing
 *   one is an error that stops the run (never a silent skip). A `pending` spec is not discovered
 *   and is reported as NOT RUN with its reason. A visual spec absent from the manifest is an error.
 */

export type LinuxStatus = "pending" | "accepted";

export interface ManifestEntry {
  linux: LinuxStatus;
  /** Who reviewed the captured Linux images, when, and from which CI run (set on acceptance). */
  reviewed_by?: string;
  reviewed_at?: string;
  capture_run?: string;
}

export type Manifest = Record<string, ManifestEntry>;

export interface SpecSnapshots {
  /** File name, e.g. `ui-r3-visual.spec.ts`. */
  spec: string;
  /** File names in `<spec>-snapshots/`. */
  snapshots: string[];
}

export interface VisualDecision {
  run: string[];
  notRun: { spec: string; reason: string }[];
  errors: string[];
}

const WIN = "-win32.png";
const LINUX = "-linux.png";

export function decideVisual(env: { ci: boolean; capture: boolean }, specs: SpecSnapshots[], manifest: Manifest): VisualDecision {
  if (!env.ci || env.capture) return { run: specs.map((s) => s.spec), notRun: [], errors: [] };
  const out: VisualDecision = { run: [], notRun: [], errors: [] };
  for (const s of specs) {
    const entry = manifest[s.spec];
    if (entry === undefined) {
      out.errors.push(`${s.spec} is not listed in tests/e2e/visual-baselines.json`);
      continue;
    }
    const linux = new Set(s.snapshots.filter((n) => n.endsWith(LINUX)));
    if (entry.linux === "pending") {
      out.notRun.push({ spec: s.spec, reason: linux.size > 0 ? "Linux baselines present but not accepted (pending review)" : "no accepted Linux baselines (pending)" });
      continue;
    }
    const missing = s.snapshots.filter((n) => n.endsWith(WIN)).map((n) => n.slice(0, -WIN.length) + LINUX).filter((n) => !linux.has(n));
    if (linux.size === 0 || missing.length > 0) {
      out.errors.push(`${s.spec} is accepted for Linux but lacks ${linux.size === 0 ? "every Linux baseline" : missing.join(", ")}`);
      continue;
    }
    out.run.push(s.spec);
  }
  return out;
}

export function parseManifest(text: string): Manifest {
  const raw: unknown = JSON.parse(text);
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) throw new Error("visual-baselines.json: an object of spec entries is expected");
  const out: Manifest = {};
  for (const [spec, v] of Object.entries(raw as Record<string, unknown>)) {
    if (spec.startsWith("$")) continue; // "$comment" and similar notes
    const linux = (v as { linux?: unknown } | null)?.linux;
    if (linux !== "pending" && linux !== "accepted") throw new Error(`visual-baselines.json: ${spec}.linux must be "pending" or "accepted"`);
    out[spec] = v as ManifestEntry;
  }
  return out;
}
