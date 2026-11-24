/**
 * Frontend final stabilization: the visual-regression policy (no silent skip, no unreviewed pass,
 * an accepted spec must run), focus moving to the chosen test case, and the experiment table's
 * compact start time. SYNTHETIC fixtures only.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { CaseList } from "../../components/datasets/case-list";
import type { TestCase } from "../../lib/api/review-types";
import { decideVisual, parseManifest } from "../setup/visual-policy";

const ROOT = path.resolve(__dirname, "..", "..", "..");
// Working-tree files may have CRLF on Windows (git normalizes them to LF on commit).
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replaceAll("\r\n", "\n");

const R3 = { spec: "ui-r3-visual.spec.ts", snapshots: ["a-win32.png", "b-win32.png"] };
const R3_LINUX = { spec: "ui-r3-visual.spec.ts", snapshots: ["a-win32.png", "b-win32.png", "a-linux.png", "b-linux.png"] };
const R3_HALF = { spec: "ui-r3-visual.spec.ts", snapshots: ["a-win32.png", "b-win32.png", "a-linux.png"] };
const CI = { ci: true, capture: false };

describe("visual-regression policy", () => {
  it("locally and in the capture run every visual spec runs", () => {
    for (const env of [{ ci: false, capture: false }, { ci: true, capture: true }]) {
      assert.deepEqual(decideVisual(env, [R3], { "ui-r3-visual.spec.ts": { linux: "pending" } }), { run: ["ui-r3-visual.spec.ts"], notRun: [], errors: [] });
    }
  });

  it("in CI a pending spec is NOT RUN with a reason, even when unreviewed Linux images exist", () => {
    const d = decideVisual(CI, [R3], { "ui-r3-visual.spec.ts": { linux: "pending" } });
    assert.deepEqual(d.run, []);
    assert.deepEqual(d.notRun, [{ spec: "ui-r3-visual.spec.ts", reason: "no accepted Linux baselines (pending)" }]);
    const e = decideVisual(CI, [R3_LINUX], { "ui-r3-visual.spec.ts": { linux: "pending" } });
    assert.match(e.notRun[0]!.reason, /present but not accepted/);
    assert.deepEqual(e.run, []);
  });

  it("in CI an accepted spec runs, and stops the run when any Linux twin is missing", () => {
    assert.deepEqual(decideVisual(CI, [R3_LINUX], { "ui-r3-visual.spec.ts": { linux: "accepted" } }).run, ["ui-r3-visual.spec.ts"]);
    assert.match(decideVisual(CI, [R3], { "ui-r3-visual.spec.ts": { linux: "accepted" } }).errors[0]!, /lacks every Linux baseline/);
    assert.match(decideVisual(CI, [R3_HALF], { "ui-r3-visual.spec.ts": { linux: "accepted" } }).errors[0]!, /lacks b-linux\.png/);
  });

  it("a visual spec missing from the manifest is an error, never a skip", () => {
    const d = decideVisual(CI, [R3], {});
    assert.equal(d.run.length + d.notRun.length, 0);
    assert.match(d.errors[0]!, /not listed/);
  });

  it("the manifest accepts only pending or accepted, and ignores $-notes", () => {
    assert.deepEqual(parseManifest('{"$comment":"x","a.spec.ts":{"linux":"pending"}}'), { "a.spec.ts": { linux: "pending" } });
    assert.throws(() => parseManifest('{"a.spec.ts":{"linux":"yes"}}'), /must be "pending" or "accepted"/);
    assert.throws(() => parseManifest("[]"), /object of spec entries/);
  });

  it("the shipped manifest lists every visual spec as pending (no Linux baseline is committed)", () => {
    const m = parseManifest(read("tests/e2e/visual-baselines.json"));
    assert.deepEqual(Object.keys(m).sort(), ["ui-r3-visual.spec.ts", "ui-r4-visual.spec.ts"]);
    for (const e of Object.values(m)) assert.equal(e.linux, "pending");
    const config = read("playwright.config.ts");
    assert.match(config, /throw new Error\(`\[visual-regression\] refused/);
    assert.match(config, /updateSnapshots: process\.env\.LLMI_E2E_CI === "1" && process\.env\.LLMI_VISUAL_LINUX !== "1" \? "none"/);
  });

  it("the capture workflow runs on PRs to main or by hand, never on push, read-only, and only uploads images", () => {
    const wf = read("../.github/workflows/visual-baselines.yml");
    // workflow_dispatch works only once the file is on main; until then the PR trigger captures.
    assert.match(wf, /^on:\n {2}workflow_dispatch:\n {2}pull_request:\n {4}branches: \[main\]\n {4}paths:/m);
    assert.doesNotMatch(wf, /^ {2}push:/m);
    assert.doesNotMatch(wf, /pull_request_target|secrets\.|write-all|contents: write/);
    assert.match(wf, /name: Linux visual baseline capture \(no comparison, not a test\)/);
    // It never edits the acceptance manifest (only a reviewed commit may set "accepted").
    assert.doesNotMatch(wf, /visual-baselines\.json["']?\s*$|sed .*visual-baselines|> *frontend\/tests\/e2e\/visual-baselines/m);
    assert.match(wf, /permissions:\n {2}contents: read/);
    assert.match(wf, /--update-snapshots=all/);
    assert.match(wf, /e2e_check\.py redact/);
    assert.match(wf, /\*-linux\.png/);
  });
});

describe("focus follows the chosen test case", () => {
  const tc = (id: number): TestCase => ({
    id,
    dataset_id: 3,
    origin_trace_id: null,
    origin_review_id: null,
    sanitized_input: "SYNTHETIC",
    expected_output: "x",
    rule: { version: 1, type: "exact" },
    redaction_applied: true,
    approved_by: "r",
    approved_at: "2026-10-25T18:05:00.000001Z",
    created_at: "2026-10-25T18:05:00.000001Z",
  });

  it("each card can take focus (tabIndex -1), has a name and a visible focus ring", () => {
    const m = renderToStaticMarkup(<CaseList project="7" cases={[tc(5), tc(6)]} />);
    assert.match(m, /<li id="case-5" tabindex="-1" aria-label="Test case #5"[^>]*ring-focus/);
  });

  it("the dataset page mounts the focus handler, which only targets case cards", () => {
    assert.match(read("app/datasets/[datasetId]/page.tsx"), /<CaseFocus \/>/);
    const src = read("components/datasets/case-focus.tsx");
    assert.match(src, /\^#case-\[1-9\]/);
    assert.match(src, /dataset\.testid === "test-case"/);
    assert.match(src, /hashchange/);
    assert.doesNotMatch(src.replace(/\/\*[\s\S]*?\*\//g, ""), /localStorage|sessionStorage|document\.cookie/);
  });
});

describe("experiment table at laptop width", () => {
  it("dataset names wrap and the start time takes two lines", () => {
    const src = read("app/experiments/page.tsx");
    assert.match(src, /max-w-\[10rem\] whitespace-normal break-words/);
    assert.match(src, /<StateBadge state=\{e\.state\} compact \/>/);
    assert.match(src, /data-testid="experiment-started"/);
  });
});
