// Fail-closed tests for scripts/audit-policy.mjs (R102 npm audit exception gate). No network:
// the reports are fixtures captured from real `npm audit --json` runs (tests/fixtures/npm-audit/):
// 2026-11-08 for the revised advisory, 2026-11-01 kept unchanged as historical evidence, and
// synthetic mutations of them.
// Run: npm run test:audit-policy
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { MAX_EXCEPTION_DAYS, evaluate, run } from "../../scripts/audit-policy.mjs";

const root = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel) => JSON.parse(readFileSync(path.join(root, rel), "utf8"));
const REAL_REPORT = read("tests/fixtures/npm-audit/full-r102-2026-11-08.json");
// A second real run on the same tree: npm's `effects` and `fixAvailable` differ between runs.
const REAL_REPORT_RUN2 = read("tests/fixtures/npm-audit/full-r102-2026-11-08-run2.json");
// Before the advisory revision of 2026-11-04: historical evidence, never rewritten.
const HISTORICAL_REPORTS = ["full-r102-2026-11-01.json", "full-r102-2026-11-01-run2.json"].map((f) => read(`tests/fixtures/npm-audit/${f}`));
const CLEAN_REPORT = read("tests/fixtures/npm-audit/omit-dev-clean-2026-11-01.json");
const REGISTRY_ERROR = read("tests/fixtures/npm-audit/registry-error-econnrefused.json");
const POLICY = read("security/npm-audit-policy.json");
const LOCKFILE = read("package-lock.json");
const R102 = POLICY.exceptions[0];
const VULNERABLE_NODE = "node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch";
const NOW = Date.parse("2026-11-01T12:00:00Z");
const END_OF_DEADLINE = Date.parse("2026-12-01T00:00:00Z");
const clone = structuredClone;

const tmp = mkdtempSync(path.join(os.tmpdir(), "llmi-audit-policy-"));
after(() => rmSync(tmp, { recursive: true, force: true }));
const writeTmp = (name, text) => {
  const file = path.join(tmp, name);
  writeFileSync(file, text);
  return file;
};

// A lockfile with exactly the fixture's vulnerable nodes, all dev-only, at the approved minimatch versions.
function syntheticLock(report = REAL_REPORT) {
  const packages = { "": { name: "fixture" } };
  for (const v of Object.values(report.vulnerabilities)) for (const node of v.nodes) packages[node] = { version: "1.0.0", dev: true };
  for (const [node, version] of Object.entries(R102.installed)) packages[node] = { version, dev: true };
  return { lockfileVersion: 3, packages };
}

// Keeps metadata.vulnerabilities consistent with the entries after a mutation.
function recount(report) {
  const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0 };
  for (const v of Object.values(report.vulnerabilities)) counts[v.severity] += 1;
  report.metadata.vulnerabilities = { ...counts, total: Object.keys(report.vulnerabilities).length };
  return report;
}

const advisory = (over = {}) => ({
  source: 1999999,
  name: "left-pad",
  dependency: "left-pad",
  title: "synthetic advisory",
  url: "https://github.com/advisories/GHSA-2222-3333-4444",
  severity: "low",
  cwe: ["CWE-400"],
  cvss: { score: 0, vectorString: null },
  range: "<2.0.0",
  ...over,
});
const finding = (name, over = {}) => ({
  name,
  severity: "low",
  isDirect: false,
  via: [advisory({ name, dependency: name })],
  effects: [],
  range: "<2.0.0",
  nodes: [`node_modules/${name}`],
  fixAvailable: true,
  ...over,
});

function input(over = {}) {
  return { report: clone(REAL_REPORT), npmExitCode: 1, lockfile: syntheticLock(), policy: clone(POLICY), now: NOW, ...over };
}
function mutated(fn, over = {}) {
  const i = input(over);
  fn(i);
  return i;
}
function assertFails(i, pattern) {
  const result = evaluate(i);
  assert.equal(result.verdict, "fail", "expected FAIL");
  assert.ok(
    result.errors.some((e) => pattern.test(e)),
    `no error matches ${pattern}:\n${result.errors.join("\n")}`,
  );
  return result;
}
const minimatchAdvisory = (report) => report.vulnerabilities.minimatch.via[0];

describe("1. no vulnerability", () => {
  test("clean report with no exceptions configured: PASS", () => {
    const result = evaluate(input({ report: clone(CLEAN_REPORT), npmExitCode: 0, policy: { ...clone(POLICY), exceptions: [] } }));
    assert.equal(result.verdict, "pass");
    assert.deepEqual(result.errors, []);
    assert.deepEqual(result.notices, []);
  });
  test("clean report with the R102 exception still configured: PASS with a removal notice", () => {
    const result = evaluate(input({ report: clone(CLEAN_REPORT), npmExitCode: 0 }));
    assert.equal(result.verdict, "pass");
    assert.match(result.notices.join("\n"), /R102 .*no longer appears.*remove the exception/);
  });
});

describe("2. only approved R102 dev-only findings", () => {
  test("CONDITIONAL PASS, every finding listed and resolved to GHSA-g3hv-75wg-m249", () => {
    const result = evaluate(input());
    assert.equal(result.verdict, "conditional-pass", result.errors.join("\n"));
    assert.equal(result.findings.length, 8);
    for (const f of result.findings) {
      assert.deepEqual(f.advisories, ["GHSA-g3hv-75wg-m249"], f.name);
      assert.deepEqual(f.problems, [], f.name);
    }
  });
  test("the printed result says the full audit is NOT clean", () => {
    const result = run({
      reportPath: path.join(root, "tests/fixtures/npm-audit/full-r102-2026-11-08.json"),
      npmExitCode: "1",
      policyPath: path.join(root, "security/npm-audit-policy.json"),
      lockfilePath: path.join(root, "package-lock.json"),
      now: NOW,
    });
    assert.equal(result.verdict, "conditional-pass");
    assert.match(result.lines.at(-1), /^CONDITIONAL PASS: the full npm audit is NOT clean; all 8 finding\(s\) are accepted .*R102 GHSA-g3hv-75wg-m249 \(review by 2026-11-30 UTC\)/);
    assert.equal(result.lines.filter((l) => l.includes("accepted by policy")).length, 8);
  });
});

describe("3. new unapproved dev-only advisory", () => {
  test("FAIL even though the package is dev-only", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities["left-pad"] = finding("left-pad");
      recount(x.report);
      x.lockfile.packages["node_modules/left-pad"] = { version: "1.0.0", dev: true };
    });
    assertFails(i, /"left-pad": GHSA-2222-3333-4444 is not approved/);
  });
  test("FAIL for a second advisory on minimatch itself (no blanket exception by package name)", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.minimatch.via.push(advisory({ name: "minimatch", dependency: "minimatch", severity: "moderate" }));
    });
    assertFails(i, /GHSA-2222-3333-4444 is not approved/);
  });
  test("FAIL for a new advisory reaching an approved chain package", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities["typescript-eslint"].via.push(advisory({ name: "typescript-eslint", dependency: "typescript-eslint", severity: "moderate" }));
    });
    assertFails(i, /"typescript-eslint": GHSA-2222-3333-4444 is not approved/);
  });
});

describe("4. production vulnerability", () => {
  test("FAIL when a vulnerable minimatch copy is not dev-only in the lockfile", () => {
    const i = mutated((x) => delete x.lockfile.packages[VULNERABLE_NODE].dev);
    assertFails(i, /typescript-estree\/node_modules\/minimatch is not dev-only \(production exposure\)/);
  });
  test("FAIL for devOptional (needed outside dev) and for a missing lockfile entry", () => {
    assertFails(
      mutated((x) => (x.lockfile.packages["node_modules/eslint-config-next"] = { version: "16.4.3", devOptional: true })),
      /node_modules\/eslint-config-next is not dev-only/,
    );
    assertFails(
      mutated((x) => delete x.lockfile.packages["node_modules/typescript-eslint"]),
      /node_modules\/typescript-eslint not in the lockfile/,
    );
  });
  test("FAIL for an unapproved production advisory", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.next = finding("next", { isDirect: true, severity: "low" });
      recount(x.report);
      x.lockfile.packages["node_modules/next"] = { version: "16.4.3" };
    });
    const result = assertFails(i, /node_modules\/next is not dev-only/);
    assert.ok(result.errors.some((e) => /"next": GHSA-2222-3333-4444 is not approved/.test(e)));
  });
  test("an exception cannot be scoped to production dependencies", () => {
    assertFails(mutated((x) => (x.policy.exceptions[0].scope = "prod")), /scope must be "dev"/);
  });
  test("FAIL without a usable lockfile", () => {
    assertFails(input({ lockfile: undefined }), /lockfile: missing/);
    assertFails(input({ lockfile: { lockfileVersion: 1, dependencies: {} } }), /lockfile: missing/);
  });
});

describe("5. unknown advisory", () => {
  test("FAIL for an advisory without a GitHub advisory URL", () => {
    assertFails(
      mutated((x) => delete minimatchAdvisory(x.report).url),
      /has no GitHub advisory ID; underlying advisory unknown/,
    );
    assertFails(
      mutated((x) => (minimatchAdvisory(x.report).url = "https://www.npmjs.com/advisories/1244623")),
      /underlying advisory unknown/,
    );
  });
  test("FAIL for findings whose references never reach an advisory (cycle)", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.foo = finding("foo", { via: ["bar"] });
      x.report.vulnerabilities.bar = finding("bar", { via: ["foo"] });
      recount(x.report);
      x.lockfile.packages["node_modules/foo"] = { version: "1.0.0", dev: true };
      x.lockfile.packages["node_modules/bar"] = { version: "1.0.0", dev: true };
    });
    assertFails(i, /"foo": underlying advisory unknown/);
  });
});

describe("6. expired exception", () => {
  test("valid through the last second of the deadline day (UTC)", () => {
    assert.equal(evaluate(input({ now: END_OF_DEADLINE - 1000 })).verdict, "conditional-pass");
  });
  test("FAIL from 2026-12-01T00:00:00Z", () => {
    assertFails(input({ now: END_OF_DEADLINE }), /EXPIRED \(review deadline 2026-11-30 UTC has passed\)/);
  });
  test("an expired exception fails even when the audit is clean", () => {
    assertFails(input({ report: clone(CLEAN_REPORT), npmExitCode: 0, now: END_OF_DEADLINE }), /EXPIRED/);
  });
});

describe("7. malformed audit JSON", () => {
  const paths = { policyPath: path.join(root, "security/npm-audit-policy.json"), lockfilePath: path.join(root, "package-lock.json"), now: NOW };
  test("FAIL for invalid JSON", () => {
    const result = run({ ...paths, reportPath: writeTmp("bad.json", '{"auditReportVersion": 2, "vulnerabilities": {'), npmExitCode: "1" });
    assert.equal(result.verdict, "fail");
    assert.match(result.errors.join("\n"), /bad\.json: malformed JSON/);
  });
  test("FAIL for JSON that is not an object", () => {
    assertFails(input({ report: [] }), /report: missing or not a JSON object/);
    assertFails(input({ report: "16 vulnerabilities" }), /report: missing or not a JSON object/);
  });
  test("CLI exits 1 for a malformed report and for a missing report argument", () => {
    const cli = (args) => spawnSync(process.execPath, ["scripts/audit-policy.mjs", ...args], { cwd: root, encoding: "utf8" });
    const bad = cli(["--report", writeTmp("bad-cli.json", "not json"), "--npm-exit-code", "1"]);
    assert.equal(bad.status, 1);
    assert.match(bad.stderr, /malformed JSON[\s\S]*FAIL:/);
    assert.equal(cli([]).status, 1);
  });
});

describe("8. registry or network audit error", () => {
  test("FAIL for npm's real ECONNREFUSED output (npm also exits 1 there)", () => {
    assertFails(input({ report: clone(REGISTRY_ERROR), npmExitCode: 1 }), /npm audit failed to execute \(request to .* ECONNREFUSED/);
  });
  test("FAIL for an npm error object without vulnerabilities", () => {
    assertFails(input({ report: { error: { code: "E500", summary: "Internal Server Error", detail: "" } } }), /failed to execute \(Internal Server Error\)/);
  });
  test("FAIL when the npm exit status contradicts the report or is not 0/1", () => {
    assertFails(input({ npmExitCode: 0 }), /exit status 0 contradicts 8 reported findings/);
    assertFails(input({ report: clone(CLEAN_REPORT), npmExitCode: 1 }), /exit status 1 contradicts 0 reported findings/);
    assertFails(input({ npmExitCode: 2 }), /exited 2/);
    assertFails(input({ npmExitCode: Number.NaN }), /exited NaN/);
  });
  test("CLI: missing --npm-exit-code fails; GitHub Actions gets an error annotation", () => {
    const report = path.join(root, "tests/fixtures/npm-audit/registry-error-econnrefused.json");
    const noCode = spawnSync(process.execPath, ["scripts/audit-policy.mjs", "--report", report], { cwd: root, encoding: "utf8" });
    assert.equal(noCode.status, 1);
    const gha = spawnSync(process.execPath, ["scripts/audit-policy.mjs", "--report", report, "--npm-exit-code", "1"], {
      cwd: root,
      encoding: "utf8",
      env: { ...process.env, GITHUB_ACTIONS: "true" },
    });
    assert.equal(gha.status, 1);
    assert.match(gha.stdout, /^::error title=npm audit policy::FAIL: /m);
  });
});

describe("9. wrong dependency chain", () => {
  test("FAIL when a vulnerable copy moves under another package", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.minimatch.nodes = [VULNERABLE_NODE, "node_modules/next/node_modules/minimatch"];
      x.lockfile.packages["node_modules/next/node_modules/minimatch"] = { version: "10.2.6", dev: true };
    });
    assertFails(i, /vulnerable copies changed/);
  });
  test("FAIL when a new package depends on the vulnerable minimatch", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.next = finding("next", { severity: "moderate", via: ["minimatch"] });
      recount(x.report);
      x.lockfile.packages["node_modules/next"] = { version: "16.4.3", dev: true };
    });
    assertFails(i, /dependency chain changed \(new: next; gone: none\)/);
  });
  test("FAIL when a chain package becomes direct (or stops being direct)", () => {
    assertFails(mutated((x) => (x.report.vulnerabilities["typescript-eslint"].isDirect = true)), /"typescript-eslint" direct\/transitive status changed/);
    assertFails(mutated((x) => (x.report.vulnerabilities["eslint-config-next"].isDirect = false)), /"eslint-config-next" direct\/transitive status changed/);
    assertFails(mutated((x) => (x.report.vulnerabilities.minimatch.isDirect = true)), /became a direct dependency/);
  });
  test("FAIL when a dependent package carries the approved advisory itself", () => {
    const i = mutated((x) => x.report.vulnerabilities["typescript-eslint"].via.push({ ...clone(minimatchAdvisory(REAL_REPORT)), dependency: "typescript-eslint" }));
    assertFails(i, /"typescript-eslint" carries the advisory itself/);
  });
});

describe("10. changed R102 scope", () => {
  test("FAIL for a changed vulnerable range", () => {
    assertFails(mutated((x) => (minimatchAdvisory(x.report).range = "<12.0.0")), /vulnerable range changed/);
    assertFails(mutated((x) => (x.report.vulnerabilities.minimatch.range = "<12.0.0")), /npm reported range changed/);
  });
  test("FAIL for a changed installed version of a vulnerable copy", () => {
    assertFails(
      mutated((x) => (x.lockfile.packages[VULNERABLE_NODE].version = "10.2.5")),
      /typescript-estree\/node_modules\/minimatch is 10\.2\.5 in the lockfile, approved 10\.2\.6/,
    );
  });
  test("FAIL when the chain narrows (needs review, not silent acceptance)", () => {
    const i = mutated((x) => {
      delete x.report.vulnerabilities["eslint-config-next"];
      recount(x.report);
    });
    assertFails(i, /dependency chain changed \(new: none; gone: eslint-config-next\)/);
  });
  test("FAIL for a changed CWE, npm source or title", () => {
    assertFails(mutated((x) => (minimatchAdvisory(x.report).cwe = ["CWE-405", "CWE-1333"])), /CWE changed/);
    assertFails(mutated((x) => (minimatchAdvisory(x.report).source = 1244013)), /npm source 1244013, approved 1244623/);
    assertFails(mutated((x) => (minimatchAdvisory(x.report).title = "other")), /advisory title changed/);
  });
});

describe("11. changed or escalated severity", () => {
  for (const severity of ["high", "critical", "low"]) {
    test(`FAIL when R102 is reported as ${severity}`, () => {
      const i = mutated((x) => {
        minimatchAdvisory(x.report).severity = severity;
        for (const v of Object.values(x.report.vulnerabilities)) v.severity = severity;
        recount(x.report);
      });
      assertFails(i, new RegExp(`advisory severity ${severity}, approved moderate`));
    });
  }
  test("FAIL when one dependent's severity changes", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities["eslint-config-next"].severity = "high";
      recount(x.report);
    });
    assertFails(i, /"eslint-config-next" severity high, approved moderate/);
  });
});

describe("12. missing or invalid exception configuration", () => {
  test("FAIL without a policy", () => {
    assertFails(input({ policy: undefined }), /policy: missing/);
    const result = run({
      reportPath: path.join(root, "tests/fixtures/npm-audit/full-r102-2026-11-08.json"),
      npmExitCode: "1",
      policyPath: path.join(tmp, "does-not-exist.json"),
      lockfilePath: path.join(root, "package-lock.json"),
      now: NOW,
    });
    assert.equal(result.verdict, "fail");
    assert.match(result.errors.join("\n"), /does-not-exist\.json: cannot be read/);
  });
  test("FAIL for every removed required field", () => {
    for (const key of Object.keys(R102)) {
      assertFails(mutated((x) => delete x.policy.exceptions[0][key]), new RegExp(`missing "${key}"`));
    }
    assertFails(mutated((x) => delete x.policy.exceptions), /exceptions must be an array/);
    assertFails(mutated((x) => (x.policy.schemaVersion = 1)), /unsupported schemaVersion \(expected 2\)/);
    assertFails(mutated((x) => (x.policy.schemaVersion = 3)), /unsupported schemaVersion \(expected 2\)/);
  });
  test("FAIL for unknown keys (no hidden switches such as allowAll)", () => {
    assertFails(mutated((x) => (x.policy.allowAll = true)), /unknown key "allowAll"/);
    assertFails(mutated((x) => (x.policy.exceptions[0].severities = ["moderate", "high"])), /unknown key "severities"/);
  });
  test("FAIL for invalid dates and windows", () => {
    assertFails(mutated((x) => (x.policy.exceptions[0].reviewDeadline = "2026-11-31")), /must be YYYY-MM-DD dates/);
    assertFails(mutated((x) => (x.policy.exceptions[0].reviewDeadline = "2099-12-31")), new RegExp(`0-${MAX_EXCEPTION_DAYS} days`));
    assertFails(mutated((x) => (x.policy.exceptions[0].reviewDeadline = "2026-10-31")), new RegExp(`0-${MAX_EXCEPTION_DAYS} days`));
    assertFails(mutated((x) => (x.policy.exceptions[0].approvedOn = "2026-11-02")), /approvedOn is in the future/);
  });
  test("FAIL for an inconsistent advisory ID, duplicates or a malformed chain", () => {
    assertFails(mutated((x) => (x.policy.exceptions[0].advisory = "GHSA-2222-3333-4444")), /GHSA ID matching advisoryUrl/);
    assertFails(mutated((x) => x.policy.exceptions.push(clone(R102))), /duplicate exception/);
    assertFails(mutated((x) => (x.policy.exceptions[0].chain["typescript-eslint"] = { isDirect: "yes", nodes: [] })), /chain entry "typescript-eslint"/);
    assertFails(mutated((x) => (x.policy.exceptions[0].chain.minimatch = { isDirect: false, nodes: ["node_modules/minimatch"] })), /chain entry "minimatch"/);
    assertFails(mutated((x) => (x.policy.exceptions[0].installed = {})), /installed must map/);
    assertFails(mutated((x) => x.policy.exceptions.push(null)), /exception #2: not an object/);
  });
  test("with an invalid policy, R102 findings are not accepted", () => {
    const result = assertFails(mutated((x) => delete x.policy.exceptions[0].justification), /missing "justification"/);
    assert.ok(result.findings.every((f) => f.problems.some((p) => /is not approved/.test(p))));
  });
});

describe("13. malformed or transitive advisory mappings", () => {
  test("FAIL for a reference to a finding that does not exist", () => {
    assertFails(mutated((x) => x.report.vulnerabilities["typescript-eslint"].via.push("ghost-package")), /via "ghost-package" does not reference another finding/);
  });
  test("FAIL for a self reference, a non-string/non-object via item and an empty via", () => {
    assertFails(mutated((x) => x.report.vulnerabilities["typescript-eslint"].via.push("typescript-eslint")), /via "typescript-eslint" does not reference/);
    assertFails(mutated((x) => x.report.vulnerabilities["typescript-eslint"].via.push(42)), /malformed via item/);
    assertFails(mutated((x) => (x.report.vulnerabilities["typescript-eslint"].via = [])), /no "via"; underlying advisory unknown/);
  });
  test("FAIL for an advisory attached to the wrong dependency or missing fields", () => {
    assertFails(mutated((x) => (minimatchAdvisory(x.report).dependency = "typescript-eslint")), /advisory GHSA-g3hv-75wg-m249 is malformed/);
    assertFails(mutated((x) => delete minimatchAdvisory(x.report).source), /is malformed/);
    assertFails(mutated((x) => delete minimatchAdvisory(x.report).range), /is malformed/);
  });
  test("FAIL for an entry whose name does not match its key, or with invalid fields", () => {
    assertFails(mutated((x) => (x.report.vulnerabilities["typescript-eslint"].name = "typescript-eslint9")), /"typescript-eslint": malformed entry/);
    assertFails(mutated((x) => (x.report.vulnerabilities["typescript-eslint"].nodes = "node_modules/typescript-eslint")), /nodes must be a non-empty list/);
    assertFails(mutated((x) => (x.report.vulnerabilities["typescript-eslint"].isDirect = "true")), /isDirect must be a boolean/);
  });
  test("transitive findings resolve to the real advisory, also through a reference cycle", () => {
    // The revised report has no cycle (the 2026-11-01 report had one), so one is added: typescript-eslint <-> @typescript-eslint/utils.
    assert.deepEqual(REAL_REPORT.vulnerabilities["@typescript-eslint/parser"].via, ["@typescript-eslint/typescript-estree"]);
    assert.ok(HISTORICAL_REPORTS[0].vulnerabilities["@eslint-community/eslint-utils"].via.includes("eslint"));
    const result = evaluate(mutated((x) => x.report.vulnerabilities["@typescript-eslint/utils"].via.push("typescript-eslint")));
    assert.equal(result.verdict, "conditional-pass", result.errors.join("\n"));
    const utils = result.findings.find((f) => f.name === "@typescript-eslint/utils");
    assert.deepEqual(utils.advisories, ["GHSA-g3hv-75wg-m249"]);
  });
});

describe("14. empty or incomplete report", () => {
  test("FAIL for an empty file and an empty object", () => {
    const result = run({
      reportPath: writeTmp("empty.json", ""),
      npmExitCode: "0",
      policyPath: path.join(root, "security/npm-audit-policy.json"),
      lockfilePath: path.join(root, "package-lock.json"),
      now: NOW,
    });
    assert.equal(result.verdict, "fail");
    assertFails(input({ report: {}, npmExitCode: 0 }), /unsupported auditReportVersion/);
  });
  test("FAIL for missing metadata, dependency totals or counts", () => {
    assertFails(mutated((x) => delete x.report.metadata), /metadata.vulnerabilities counts are missing/);
    assertFails(mutated((x) => (x.report.metadata.dependencies.total = 0)), /incomplete report/);
    assertFails(mutated((x) => delete x.report.metadata.vulnerabilities.critical), /counts are missing or invalid/);
    assertFails(mutated((x) => (x.report.auditReportVersion = 1)), /unsupported auditReportVersion/);
    assertFails(mutated((x) => delete x.report.vulnerabilities), /vulnerabilities must be an object/);
  });
  test("FAIL when entries and metadata disagree (truncated report)", () => {
    assertFails(mutated((x) => delete x.report.vulnerabilities["eslint-config-next"]), /7 vulnerability entries but metadata total 8/);
    assertFails(mutated((x) => (x.report.metadata.vulnerabilities.total = 9)), /do not add up to the total/);
    assertFails(input({ report: { ...clone(CLEAN_REPORT), vulnerabilities: {} }, npmExitCode: 1 }), /exit status 1 contradicts 0/);
  });
});

describe("15. approved findings mixed with an unapproved one", () => {
  test("FAIL; the approved findings stay listed as accepted and only the new one is rejected", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities["left-pad"] = finding("left-pad", { severity: "info" });
      recount(x.report);
      x.lockfile.packages["node_modules/left-pad"] = { version: "1.0.0", dev: true };
    });
    const result = assertFails(i, /"left-pad": GHSA-2222-3333-4444 is not approved/);
    assert.equal(result.findings.length, 9);
    assert.deepEqual(
      result.findings.filter((f) => f.problems.length > 0).map((f) => f.name),
      ["left-pad"],
    );
  });
});

describe("16. current real npm audit report", () => {
  test("the 2026-11-08 report with the real lockfile and policy is a CONDITIONAL PASS", () => {
    const result = evaluate({ report: clone(REAL_REPORT), npmExitCode: 1, lockfile: clone(LOCKFILE), policy: clone(POLICY), now: NOW });
    assert.equal(result.verdict, "conditional-pass", result.errors.join("\n"));
    assert.equal(result.findings.length, REAL_REPORT.metadata.vulnerabilities.total);
  });
  test("the policy records exactly the report's chain and vulnerable copies", () => {
    const { minimatch, ...rest } = REAL_REPORT.vulnerabilities;
    assert.deepEqual(Object.keys(R102.chain).sort(), Object.keys(rest).sort());
    for (const [name, v] of Object.entries(rest)) assert.deepEqual({ isDirect: v.isDirect, nodes: [...v.nodes].sort() }, { isDirect: R102.chain[name].isDirect, nodes: [...R102.chain[name].nodes].sort() }, name);
    assert.deepEqual(Object.keys(R102.installed).sort(), [...minimatch.nodes].sort());
    for (const [node, version] of Object.entries(R102.installed)) assert.equal(LOCKFILE.packages[node].version, version);
  });
  test("npm's run-to-run variation (effects, fixAvailable) does not change the classification", () => {
    const strip = (r) => {
      const vulnerabilities = clone(r.vulnerabilities);
      for (const v of Object.values(vulnerabilities)) {
        delete v.effects;
        delete v.fixAvailable;
      }
      return vulnerabilities;
    };
    assert.notDeepEqual(REAL_REPORT_RUN2.vulnerabilities, REAL_REPORT.vulnerabilities);
    assert.deepEqual(strip(REAL_REPORT_RUN2), strip(REAL_REPORT));
    const result = evaluate({ report: clone(REAL_REPORT_RUN2), npmExitCode: 1, lockfile: clone(LOCKFILE), policy: clone(POLICY), now: NOW });
    assert.equal(result.verdict, "conditional-pass", result.errors.join("\n"));
  });
  test("the production audit fixture is clean", () => {
    assert.equal(CLEAN_REPORT.metadata.vulnerabilities.total, 0);
    assert.deepEqual(CLEAN_REPORT.vulnerabilities, {});
  });
  test("CLI on the real report: exit 0 before the deadline, 1 after it", () => {
    const report = path.join(root, "tests/fixtures/npm-audit/full-r102-2026-11-08.json");
    const cli = spawnSync(process.execPath, ["scripts/audit-policy.mjs", "--report", report, "--npm-exit-code", "1"], { cwd: root, encoding: "utf8" });
    assert.equal(cli.status, Date.now() < END_OF_DEADLINE ? 0 : 1, cli.stdout + cli.stderr);
  });
});

describe("17. revised advisory of 2026-11-04: two independent, exact range fields", () => {
  const ADVISORY_RANGE = ">=7.0.0 <11.0.1";
  const ENTRY_RANGE = "7.0.0 - 11.0.0";
  const rangeErrors = (result) => result.errors.filter((e) => /range changed/.test(e));
  const CHAIN = [
    "@typescript-eslint/eslint-plugin",
    "@typescript-eslint/parser",
    "@typescript-eslint/type-utils",
    "@typescript-eslint/typescript-estree",
    "@typescript-eslint/utils",
    "eslint-config-next",
    "typescript-eslint",
  ];

  test("the policy records exactly the verified advisory (literal values, not derived from the fixture)", () => {
    assert.equal(POLICY.schemaVersion, 2);
    assert.equal(POLICY.exceptions.length, 1);
    assert.equal(R102.riskId, "R102");
    assert.equal(R102.advisory, "GHSA-g3hv-75wg-m249");
    assert.equal(R102.cve, "CVE-2026-118764");
    assert.equal(R102.npmSource, 1244623);
    assert.equal(R102.package, "minimatch");
    assert.equal(R102.severity, "moderate");
    assert.deepEqual(R102.cwe, ["CWE-405"]);
    assert.equal(R102.scope, "dev");
    assert.equal(R102.vulnerableRange, ADVISORY_RANGE);
    assert.equal(R102.npmReportedRange, ENTRY_RANGE);
  });
  test("exactly one vulnerable copy is accepted: minimatch 10.2.6, and 3.1.5 is not", () => {
    assert.deepEqual(R102.installed, { [VULNERABLE_NODE]: "10.2.6" });
    assert.equal(LOCKFILE.packages["node_modules/minimatch"].version, "3.1.5", "3.1.5 stays in the lockfile");
    assert.ok(!("node_modules/minimatch" in R102.installed));
  });
  test("exactly the verified dev-only chain is accepted; only eslint-config-next is direct", () => {
    assert.deepEqual(Object.keys(R102.chain).sort(), CHAIN);
    assert.deepEqual(Object.entries(R102.chain).filter(([, l]) => l.isDirect).map(([n]) => n), ["eslint-config-next"]);
    for (const name of CHAIN) assert.deepEqual(R102.chain[name].nodes, [`node_modules/${name}`], name);
  });
  test("approval date and review deadline are unchanged", () => {
    assert.equal(R102.approvedOn, "2026-11-01");
    assert.equal(R102.reviewDeadline, "2026-11-30");
  });
  test("the two real reports of the revised advisory are CONDITIONAL PASS with the real lockfile", () => {
    for (const report of [REAL_REPORT, REAL_REPORT_RUN2]) {
      assert.equal(minimatchAdvisory(report).range, ADVISORY_RANGE);
      assert.equal(report.vulnerabilities.minimatch.range, ENTRY_RANGE);
      assert.equal(minimatchAdvisory(report).source, 1244623);
      const result = evaluate({ report: clone(report), npmExitCode: 1, lockfile: clone(LOCKFILE), policy: clone(POLICY), now: NOW });
      assert.equal(result.verdict, "conditional-pass", result.errors.join("\n"));
      assert.equal(result.findings.length, 8);
    }
  });
  test("historical reports from before the revision are rejected and were not rewritten", () => {
    for (const report of HISTORICAL_REPORTS) {
      assert.equal(minimatchAdvisory(report).source, 1244012);
      assert.equal(minimatchAdvisory(report).range, "<11.0.1");
      assert.equal(report.metadata.vulnerabilities.total, 16);
      const i = { report: clone(report), npmExitCode: 1, lockfile: clone(LOCKFILE), policy: clone(POLICY), now: NOW };
      for (const pattern of [/npm source 1244012, approved 1244623/, /vulnerable range changed/, /npm reported range changed/, /vulnerable copies changed/, /dependency chain changed/]) assertFails(i, pattern);
    }
  });

  test("A. a wrong advisory range fails, and only the advisory check reports it", () => {
    for (const range of [">=7.0.0 <11.0.2", "<11.0.1", ">=7.0.0", "*", ` ${ADVISORY_RANGE}`, `${ADVISORY_RANGE} || >=12.0.0`, ENTRY_RANGE]) {
      const result = assertFails(mutated((x) => (minimatchAdvisory(x.report).range = range)), /vulnerable range changed/);
      assert.equal(rangeErrors(result).length, 1, range);
    }
  });
  test("B. a wrong npm entry range fails, and only the entry check reports it", () => {
    for (const range of ["7.0.0 - 11.0.1", "<11.0.1", "*", `${ENTRY_RANGE} `, ADVISORY_RANGE]) {
      const result = assertFails(mutated((x) => (x.report.vulnerabilities.minimatch.range = range)), /npm reported range changed/);
      assert.equal(rangeErrors(result).length, 1, range);
    }
    const missing = assertFails(mutated((x) => delete x.report.vulnerabilities.minimatch.range), /npm reported range changed/);
    assert.equal(rangeErrors(missing).length, 1);
  });
  test("a second record of the approved advisory on minimatch fails (no first-match acceptance)", () => {
    const i = mutated((x) => x.report.vulnerabilities.minimatch.via.push({ ...clone(minimatchAdvisory(REAL_REPORT)), range: ">=1.0.0 <11.0.1", source: 1244012 }));
    assertFails(i, /"minimatch" must carry exactly the approved advisory/);
  });
  test("changing either approved range alone fails; swapping them fails on both checks", () => {
    assert.equal(rangeErrors(assertFails(mutated((x) => (x.policy.exceptions[0].vulnerableRange = "<11.0.1")), /vulnerable range changed/)).length, 1);
    assert.equal(rangeErrors(assertFails(mutated((x) => (x.policy.exceptions[0].npmReportedRange = "7.0.0 - 11.0.1")), /npm reported range changed/)).length, 1);
    const swapped = evaluate(mutated((x) => {
      x.policy.exceptions[0].vulnerableRange = ENTRY_RANGE;
      x.policy.exceptions[0].npmReportedRange = ADVISORY_RANGE;
    }));
    assert.equal(swapped.verdict, "fail");
    assert.equal(rangeErrors(swapped).length, 2);
  });
  test("C. a missing npmReportedRange fails, also under the previous schema version", () => {
    assertFails(mutated((x) => delete x.policy.exceptions[0].npmReportedRange), /missing "npmReportedRange"/);
    const previous = mutated((x) => {
      delete x.policy.exceptions[0].npmReportedRange;
      x.policy.schemaVersion = 1;
    });
    assertFails(previous, /unsupported schemaVersion/);
    assertFails(previous, /missing "npmReportedRange"/);
  });
  test("D. an empty npmReportedRange fails", () => {
    for (const value of ["", "   ", null, 7]) assertFails(mutated((x) => (x.policy.exceptions[0].npmReportedRange = value)), /"npmReportedRange" must be a non-empty string/);
  });
  test("wildcards and alternatives are refused in both approved ranges, before any comparison", () => {
    for (const key of ["vulnerableRange", "npmReportedRange"]) {
      for (const value of ["*", "x", "7.x", "7.*.*", ">=7.0.0 || <1.0.0", "latest", ">=", "- -"]) {
        assertFails(mutated((x) => (x.policy.exceptions[0][key] = value)), new RegExp(`"${key}" must be an exact version range`));
      }
    }
    const allStar = mutated((x) => {
      x.policy.exceptions[0].npmReportedRange = "*";
      x.report.vulnerabilities.minimatch.range = "*";
    });
    assertFails(allStar, /"npmReportedRange" must be an exact version range/);
  });
  test("E. unknown policy fields fail", () => {
    assertFails(mutated((x) => (x.policy.exceptions[0].npmReportedRanges = [ENTRY_RANGE])), /unknown key "npmReportedRanges"/);
    assertFails(mutated((x) => (x.policy.acceptAnyRange = true)), /unknown key "acceptAnyRange"/);
  });
  test("F. a changed npm source ID fails, including the pre-revision one", () => {
    for (const source of [1244012, 1244624]) assertFails(mutated((x) => (minimatchAdvisory(x.report).source = source)), new RegExp(`npm source ${source}, approved 1244623`));
  });
  test("G. a new GHSA advisory fails", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities["left-pad"] = finding("left-pad", { severity: "moderate" });
      recount(x.report);
      x.lockfile.packages["node_modules/left-pad"] = { version: "1.0.0", dev: true };
    });
    assertFails(i, /GHSA-2222-3333-4444 is not approved/);
  });
  test("H. a new production vulnerability fails, also when it resolves to the approved advisory", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.next = finding("next", { severity: "moderate", via: ["minimatch"] });
      recount(x.report);
      x.lockfile.packages["node_modules/next"] = { version: "16.4.3" };
    });
    assertFails(i, /node_modules\/next is not dev-only \(production exposure\)/);
  });
  test("I. an extra vulnerable node fails, including minimatch 3.1.5 coming back into this advisory", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.minimatch.nodes.push("node_modules/minimatch");
      x.lockfile.packages["node_modules/minimatch"] = { version: "3.1.5", dev: true };
    });
    assertFails(i, /vulnerable copies changed/);
  });
  test("J. a changed installed version fails", () => {
    for (const version of ["10.2.5", "10.2.7", "11.0.0"]) {
      assertFails(mutated((x) => (x.lockfile.packages[VULNERABLE_NODE].version = version)), new RegExp(`is ${version.replaceAll(".", "\\.")} in the lockfile, approved 10\\.2\\.6`));
    }
    assertFails(mutated((x) => delete x.lockfile.packages[VULNERABLE_NODE]), /not in the lockfile/);
  });
  test("K. an unexpected dependency chain fails: an old chain package returning, or an unexpected node path", () => {
    const i = mutated((x) => {
      x.report.vulnerabilities.eslint = finding("eslint", { severity: "moderate", isDirect: true, via: ["minimatch"] });
      recount(x.report);
      x.lockfile.packages["node_modules/eslint"] = { version: "9.39.5", dev: true };
    });
    assertFails(i, /dependency chain changed \(new: eslint; gone: none\)/);
    const nodes = mutated((x) => {
      x.report.vulnerabilities["typescript-eslint"].nodes.push("node_modules/x/node_modules/typescript-eslint");
      x.lockfile.packages["node_modules/x/node_modules/typescript-eslint"] = { version: "8.71.1", dev: true };
    });
    assertFails(nodes, /"typescript-eslint" node paths changed/);
  });
  test("L. a changed severity or CWE fails", () => {
    assertFails(mutated((x) => (minimatchAdvisory(x.report).severity = "high")), /advisory severity high, approved moderate/);
    assertFails(mutated((x) => (minimatchAdvisory(x.report).cwe = ["CWE-400"])), /CWE changed/);
    assertFails(mutated((x) => (minimatchAdvisory(x.report).cwe = ["CWE-405", "CWE-1333"])), /CWE changed/);
  });
  test("M. malformed audit JSON fails", () => {
    const result = run({
      reportPath: writeTmp("malformed-revised.json", '{"auditReportVersion": 2, "vulnerabilities": {"minimatch"'),
      npmExitCode: "1",
      policyPath: path.join(root, "security/npm-audit-policy.json"),
      lockfilePath: path.join(root, "package-lock.json"),
      now: NOW,
    });
    assert.equal(result.verdict, "fail");
    assert.match(result.errors.join("\n"), /malformed JSON/);
  });
  test("N. a failed npm audit execution fails", () => {
    assertFails(input({ report: clone(REGISTRY_ERROR), npmExitCode: 1 }), /npm audit failed to execute/);
    assertFails(input({ npmExitCode: 2 }), /exited 2/);
  });
  test("O. the R102 deadline expires at 2026-12-01T00:00:00Z", () => {
    assert.equal(evaluate(input({ now: END_OF_DEADLINE - 1 })).verdict, "conditional-pass");
    assertFails(input({ now: END_OF_DEADLINE }), /EXPIRED \(review deadline 2026-11-30 UTC has passed\)/);
  });
  test("P. a different advisory title fails", () => {
    assertFails(mutated((x) => (minimatchAdvisory(x.report).title = `${R102.title} (updated)`)), /advisory title changed/);
  });
  test("Q. an unresolvable advisory ID fails", () => {
    assertFails(mutated((x) => (minimatchAdvisory(x.report).url = "https://github.com/advisories/not-a-ghsa")), /underlying advisory unknown/);
    assertFails(mutated((x) => delete minimatchAdvisory(x.report).url), /underlying advisory unknown/);
  });
  test("R. an unapproved affected package fails, even carrying the approved advisory", () => {
    const i = mutated((x) => {
      const via = [{ ...clone(minimatchAdvisory(REAL_REPORT)), name: "minimatch-fork", dependency: "minimatch-fork" }];
      x.report.vulnerabilities["minimatch-fork"] = finding("minimatch-fork", { severity: "moderate", via });
      recount(x.report);
      x.lockfile.packages["node_modules/minimatch-fork"] = { version: "10.2.6", dev: true };
    });
    assertFails(i, /dependency chain changed \(new: minimatch-fork; gone: none\)/);
    assertFails(i, /"minimatch-fork" carries the advisory itself/);
  });
});

describe("mutation sweep", () => {
  test("every approved policy value is load-bearing", () => {
    const changes = {
      npmSource: 1244012,
      vulnerableRange: "<11.0.1",
      npmReportedRange: "<11.0.1",
      title: "changed",
      package: "minimatch2",
      severity: "low",
      cwe: ["CWE-400"],
      installed: { [VULNERABLE_NODE]: "10.2.5" },
    };
    for (const [field, value] of Object.entries(changes)) {
      const result = evaluate(mutated((x) => (x.policy.exceptions[0][field] = value)));
      assert.equal(result.verdict, "fail", `mutating policy.${field} must fail`);
    }
  });
  test("the finding entry's range is load-bearing", () => {
    assert.equal(evaluate(mutated((x) => (x.report.vulnerabilities.minimatch.range = "<99"))).verdict, "fail");
  });
  test("every field of the approved advisory is load-bearing", () => {
    const changes = { source: 1, name: "minimatch2", title: "changed", url: "https://github.com/advisories/GHSA-2222-3333-4444", severity: "high", range: "<99", cwe: ["CWE-400"], dependency: "eslint" };
    for (const [field, value] of Object.entries(changes)) {
      const result = evaluate(mutated((x) => (minimatchAdvisory(x.report)[field] = value)));
      assert.equal(result.verdict, "fail", `mutating advisory.${field} must fail`);
    }
  });
  test("every finding's severity, nodes, direct flag and presence is load-bearing", () => {
    for (const name of Object.keys(REAL_REPORT.vulnerabilities)) {
      const mutations = {
        severity: (v) => (v.severity = "high"),
        extraNode: (v) => v.nodes.push(`node_modules/x/node_modules/${name}`),
        isDirect: (v) => (v.isDirect = !v.isDirect),
      };
      for (const [label, mutate] of Object.entries(mutations)) {
        const i = mutated((x) => {
          mutate(x.report.vulnerabilities[name]);
          recount(x.report);
          for (const node of x.report.vulnerabilities[name].nodes) x.lockfile.packages[node] ??= { version: "1.0.0", dev: true };
        });
        assert.equal(evaluate(i).verdict, "fail", `${name}: ${label}`);
      }
      const removed = mutated((x) => {
        delete x.report.vulnerabilities[name];
        recount(x.report);
      });
      assert.equal(evaluate(removed).verdict, "fail", `${name}: removed`);
    }
  });
  test("every vulnerable node's dev flag is load-bearing", () => {
    for (const v of Object.values(REAL_REPORT.vulnerabilities)) {
      for (const node of v.nodes) {
        assert.equal(evaluate(mutated((x) => delete x.lockfile.packages[node].dev)).verdict, "fail", node);
      }
    }
  });
});
