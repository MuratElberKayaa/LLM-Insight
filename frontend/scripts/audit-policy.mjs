// Full `npm audit` gate with narrow, expiring exceptions (R102, docs/reports/phase-6-ci-security-policy.md).
//
// Usage (from frontend/, after `npm audit --json > FILE`, which exits 1 when it finds anything):
//   node scripts/audit-policy.mjs --report FILE --npm-exit-code N
//
// Fails closed. PASS only when the report is a complete npm audit v2 report and either has no
// findings, or every finding resolves (through npm's transitive `via` references) to an advisory
// that security/npm-audit-policy.json accepts, in exactly the recorded dev-only chain, before its
// review deadline. Anything else exits 1: another advisory, an unresolvable finding, a non-dev
// node in package-lock.json, a changed severity/range/chain, an expired or invalid policy, a
// malformed report, or an npm execution failure (registry/network errors also exit 1, so the exit
// status alone never decides). Every finding is printed, accepted or not. The policy and the
// lockfile paths and the clock are fixed for the CLI; tests call evaluate() and run() directly.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SEVERITIES = ["info", "low", "moderate", "high", "critical"];
const GHSA_URL = /^https:\/\/github\.com\/advisories\/(GHSA(?:-[23456789cfghjmpqrvwx]{4}){3})$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;
export const MAX_EXCEPTION_DAYS = 31;

const POLICY_KEYS = ["schemaVersion", "description", "exceptions"];
const EXCEPTION_KEYS = [
  "riskId",
  "advisory",
  "advisoryUrl",
  "cve",
  "npmSource",
  "title",
  "package",
  "vulnerableRange",
  "severity",
  "cwe",
  "scope",
  "installed",
  "chain",
  "justification",
  "approvedOn",
  "approvedBy",
  "reviewDeadline",
  "remediation",
  "blockingConditions",
];

const isObject = (v) => typeof v === "object" && v !== null && !Array.isArray(v);
const isText = (v) => typeof v === "string" && v.trim().length > 0;
const isTextList = (v) => Array.isArray(v) && v.length > 0 && v.every(isText);
const sameSet = (a, b) => a.length === b.length && new Set(a).size === a.length && a.every((x) => b.includes(x));
const sameList = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i]);

function parseDate(value) {
  if (typeof value !== "string" || !DATE.test(value)) return null;
  const ms = Date.parse(`${value}T00:00:00Z`);
  return Number.isNaN(ms) || new Date(ms).toISOString().slice(0, 10) !== value ? null : ms;
}

function checkKeys(obj, allowed, label, errors) {
  for (const key of Object.keys(obj)) if (!allowed.includes(key)) errors.push(`${label}: unknown key "${key}"`);
  for (const key of allowed) if (!(key in obj)) errors.push(`${label}: missing "${key}"`);
}

// Validates the policy file and returns the usable exceptions (only when the whole policy is valid).
export function validatePolicy(policy, now) {
  const errors = [];
  if (!isObject(policy)) return { errors: ["policy: missing or not a JSON object"], exceptions: [] };
  checkKeys(policy, POLICY_KEYS, "policy", errors);
  if (policy.schemaVersion !== 1) errors.push("policy: unsupported schemaVersion (expected 1)");
  if (!Array.isArray(policy.exceptions)) return { errors: [...errors, "policy: exceptions must be an array"], exceptions: [] };
  const seen = new Set();
  for (const [i, exc] of policy.exceptions.entries()) {
    const label = `policy exception #${i + 1}${isObject(exc) && isText(exc.riskId) ? ` (${exc.riskId})` : ""}`;
    if (!isObject(exc)) {
      errors.push(`${label}: not an object`);
      continue;
    }
    checkKeys(exc, EXCEPTION_KEYS, label, errors);
    for (const key of ["riskId", "cve", "title", "package", "vulnerableRange", "justification", "approvedBy", "remediation"]) {
      if (!isText(exc[key])) errors.push(`${label}: "${key}" must be a non-empty string`);
    }
    const urlMatch = typeof exc.advisoryUrl === "string" ? GHSA_URL.exec(exc.advisoryUrl) : null;
    if (!urlMatch || urlMatch[1] !== exc.advisory) errors.push(`${label}: advisory must be a GHSA ID matching advisoryUrl`);
    if (seen.has(exc.advisory)) errors.push(`${label}: duplicate exception for ${exc.advisory}`);
    seen.add(exc.advisory);
    if (!Number.isInteger(exc.npmSource) || exc.npmSource <= 0) errors.push(`${label}: npmSource must be a positive integer`);
    if (!SEVERITIES.includes(exc.severity)) errors.push(`${label}: invalid severity`);
    if (!isTextList(exc.cwe)) errors.push(`${label}: cwe must be a non-empty list`);
    if (exc.scope !== "dev") errors.push(`${label}: scope must be "dev" (production dependencies take no exceptions)`);
    if (!isObject(exc.installed) || Object.keys(exc.installed).length === 0 || !Object.values(exc.installed).every(isText)) {
      errors.push(`${label}: installed must map node paths to versions`);
    }
    if (!isObject(exc.chain)) errors.push(`${label}: chain must be an object`);
    else {
      for (const [name, link] of Object.entries(exc.chain)) {
        if (name === exc.package || !isObject(link) || typeof link.isDirect !== "boolean" || !isTextList(link.nodes) || Object.keys(link).length !== 2) {
          errors.push(`${label}: chain entry "${name}" must be {isDirect, nodes}`);
        }
      }
    }
    if (!isTextList(exc.blockingConditions)) errors.push(`${label}: blockingConditions must be a non-empty list`);
    const approved = parseDate(exc.approvedOn);
    const deadline = parseDate(exc.reviewDeadline);
    if (approved === null || deadline === null) {
      errors.push(`${label}: approvedOn and reviewDeadline must be YYYY-MM-DD dates`);
      continue;
    }
    if (approved > now) errors.push(`${label}: approvedOn is in the future`);
    if (deadline < approved || deadline - approved > MAX_EXCEPTION_DAYS * DAY_MS) {
      errors.push(`${label}: reviewDeadline must be 0-${MAX_EXCEPTION_DAYS} days after approvedOn`);
    }
    // The deadline day is included: the exception is valid until the end of that UTC day.
    if (now >= deadline + DAY_MS) errors.push(`${label}: EXPIRED (review deadline ${exc.reviewDeadline} UTC has passed); re-review required`);
  }
  return { errors, exceptions: errors.length === 0 ? policy.exceptions : [] };
}

// Structural validation of an `npm audit --json` (auditReportVersion 2) report.
function validateReport(report, npmExitCode) {
  const errors = [];
  if (!isObject(report)) return ["report: missing or not a JSON object"];
  if ("error" in report || "message" in report) {
    const reason = isText(report.message) ? report.message : isObject(report.error) ? report.error.summary || report.error.code : "";
    return [`report: npm audit failed to execute (${reason || "no reason given"}); not an audit result`];
  }
  if (report.auditReportVersion !== 2) errors.push("report: unsupported auditReportVersion (expected 2)");
  if (!isObject(report.vulnerabilities)) errors.push("report: vulnerabilities must be an object");
  const counts = report.metadata?.vulnerabilities;
  if (!isObject(counts) || ![...SEVERITIES, "total"].every((s) => Number.isInteger(counts[s]) && counts[s] >= 0)) {
    errors.push("report: metadata.vulnerabilities counts are missing or invalid");
  }
  const deps = report.metadata?.dependencies;
  if (!isObject(deps) || !Number.isInteger(deps.total) || deps.total <= 0) errors.push("report: metadata.dependencies is missing or empty (incomplete report)");
  if (errors.length > 0) return errors;

  const entries = Object.values(report.vulnerabilities);
  if (SEVERITIES.reduce((sum, s) => sum + counts[s], 0) !== counts.total) errors.push("report: severity counts do not add up to the total");
  if (entries.length !== counts.total) errors.push(`report: ${entries.length} vulnerability entries but metadata total ${counts.total}`);
  for (const s of SEVERITIES) {
    const n = entries.filter((v) => v?.severity === s).length;
    if (n !== counts[s]) errors.push(`report: ${n} ${s} entries but metadata says ${counts[s]}`);
  }
  if (npmExitCode !== 0 && npmExitCode !== 1) errors.push(`npm audit exited ${npmExitCode}, which is neither 0 (clean) nor 1 (findings)`);
  else if ((npmExitCode === 0) !== (counts.total === 0)) errors.push(`npm audit exit status ${npmExitCode} contradicts ${counts.total} reported findings`);
  return errors;
}

// Checks one vulnerability entry's shape; returns its own advisories (via objects) and references (via strings).
function readEntry(key, v, all, errors) {
  const own = [];
  const refs = [];
  const label = `finding "${key}"`;
  if (!isObject(v) || v.name !== key) {
    errors.push(`${label}: malformed entry (name mismatch)`);
    return { own, refs };
  }
  if (!SEVERITIES.includes(v.severity)) errors.push(`${label}: invalid severity`);
  if (typeof v.isDirect !== "boolean") errors.push(`${label}: isDirect must be a boolean`);
  if (!isTextList(v.nodes)) errors.push(`${label}: nodes must be a non-empty list`);
  if (!Array.isArray(v.via) || v.via.length === 0) {
    errors.push(`${label}: no "via"; underlying advisory unknown`);
    return { own, refs };
  }
  for (const via of v.via) {
    if (typeof via === "string") {
      if (via === key || !Object.hasOwn(all, via)) errors.push(`${label}: via "${via}" does not reference another finding`);
      else refs.push(via);
    } else if (isObject(via)) {
      const id = typeof via.url === "string" ? GHSA_URL.exec(via.url)?.[1] : undefined;
      if (!id) errors.push(`${label}: advisory ${JSON.stringify(via.source ?? null)} has no GitHub advisory ID; underlying advisory unknown`);
      else if (!Number.isInteger(via.source) || via.dependency !== key || !isText(via.name) || !SEVERITIES.includes(via.severity) || !isText(via.range)) {
        errors.push(`${label}: advisory ${id} is malformed`);
      } else own.push({ id, ...via });
    } else errors.push(`${label}: malformed via item`);
  }
  return { own, refs };
}

// The underlying advisory IDs of a finding, following npm's transitive references (cycles allowed).
function rootAdvisories(key, graph) {
  const ids = new Set();
  const seen = new Set();
  const stack = [key];
  while (stack.length > 0) {
    const k = stack.pop();
    if (seen.has(k) || !graph.has(k)) continue;
    seen.add(k);
    for (const adv of graph.get(k).own) ids.add(adv.id);
    stack.push(...graph.get(k).refs);
  }
  return ids;
}

function checkLockfile(lockfile, errors) {
  if (!isObject(lockfile) || ![2, 3].includes(lockfile.lockfileVersion) || !isObject(lockfile.packages)) {
    errors.push("lockfile: missing or not a package-lock.json v2/v3");
    return null;
  }
  return lockfile.packages;
}

function checkException(exc, keys, report, graph, packages, errors) {
  const label = `${exc.riskId} (${exc.advisory})`;
  const expected = [exc.package, ...Object.keys(exc.chain)];
  if (!sameSet(keys, expected)) {
    const added = keys.filter((k) => !expected.includes(k));
    const missing = expected.filter((k) => !keys.includes(k));
    errors.push(`${label}: dependency chain changed (new: ${added.join(", ") || "none"}; gone: ${missing.join(", ") || "none"}); re-review required`);
  }
  for (const key of keys) {
    const v = report.vulnerabilities[key];
    const nodes = isTextList(v.nodes) ? v.nodes : [];
    if (v.severity !== exc.severity) errors.push(`${label}: "${key}" severity ${v.severity}, approved ${exc.severity}`);
    if (key === exc.package) {
      const advisories = graph.get(key).own;
      const adv = advisories.find((a) => a.id === exc.advisory);
      if (advisories.length !== 1 || !adv) errors.push(`${label}: "${key}" must carry exactly the approved advisory`);
      else {
        if (adv.source !== exc.npmSource) errors.push(`${label}: npm source ${adv.source}, approved ${exc.npmSource}`);
        if (adv.name !== exc.package) errors.push(`${label}: advisory package ${adv.name}, approved ${exc.package}`);
        if (adv.severity !== exc.severity) errors.push(`${label}: advisory severity ${adv.severity}, approved ${exc.severity}`);
        if (adv.range !== exc.vulnerableRange || v.range !== exc.vulnerableRange) errors.push(`${label}: vulnerable range changed (approved ${exc.vulnerableRange})`);
        if (!sameList(adv.cwe, exc.cwe)) errors.push(`${label}: CWE changed (approved ${exc.cwe.join(", ")})`);
        if (adv.title !== exc.title) errors.push(`${label}: advisory title changed`);
      }
      if (v.isDirect) errors.push(`${label}: "${key}" became a direct dependency`);
      if (!sameSet(nodes, Object.keys(exc.installed))) errors.push(`${label}: vulnerable copies changed (${nodes.join(", ")})`);
      for (const [node, version] of Object.entries(exc.installed)) {
        const found = packages?.[node]?.version;
        if (nodes.includes(node) && found !== version) errors.push(`${label}: ${node} is ${found ?? "missing"} in the lockfile, approved ${version}`);
      }
    } else {
      if (graph.get(key).own.some((a) => a.id === exc.advisory)) errors.push(`${label}: "${key}" carries the advisory itself`);
      const link = exc.chain[key];
      if (link && v.isDirect !== link.isDirect) errors.push(`${label}: "${key}" direct/transitive status changed`);
      if (link && !sameSet(nodes, link.nodes)) errors.push(`${label}: "${key}" node paths changed (${nodes.join(", ")})`);
    }
  }
}

export function evaluate({ report, npmExitCode, lockfile, policy, now }) {
  const errors = [];
  const notices = [];
  const findings = [];
  const { errors: policyErrors, exceptions } = validatePolicy(policy, now);
  errors.push(...policyErrors);
  const reportErrors = validateReport(report, npmExitCode);
  errors.push(...reportErrors);
  const packages = checkLockfile(lockfile, errors);
  if (reportErrors.length > 0) return { verdict: "fail", errors, notices, findings };

  const graph = new Map();
  for (const [key, v] of Object.entries(report.vulnerabilities)) graph.set(key, readEntry(key, v, report.vulnerabilities, errors));
  const byAdvisory = new Map();
  for (const [key, v] of Object.entries(report.vulnerabilities)) {
    const roots = [...rootAdvisories(key, graph)].sort();
    const problems = [];
    if (roots.length === 0) problems.push("underlying advisory unknown");
    for (const id of roots) {
      if (!exceptions.some((e) => e.advisory === id)) problems.push(`${id} is not approved`);
      byAdvisory.set(id, [...(byAdvisory.get(id) ?? []), key]);
    }
    for (const node of isTextList(v?.nodes) ? v.nodes : []) {
      const entry = packages?.[node];
      if (!entry) problems.push(`${node} not in the lockfile`);
      else if (entry.dev !== true) problems.push(`${node} is not dev-only (production exposure)`);
    }
    for (const p of problems) errors.push(`finding "${key}": ${p}`);
    findings.push({ name: key, severity: v?.severity, isDirect: v?.isDirect, nodes: isTextList(v?.nodes) ? v.nodes : [], advisories: roots, problems });
  }
  for (const exc of exceptions) {
    const keys = byAdvisory.get(exc.advisory);
    if (keys) checkException(exc, keys, report, graph, packages, errors);
    else notices.push(`${exc.riskId} (${exc.advisory}) no longer appears in the audit: remove the exception`);
  }
  if (errors.length > 0) return { verdict: "fail", errors, notices, findings };
  return { verdict: findings.length === 0 ? "pass" : "conditional-pass", errors, notices, findings };
}

export function loadJson(file) {
  try {
    return { value: JSON.parse(readFileSync(file, "utf8")) };
  } catch (err) {
    return { error: `${path.basename(file)}: ${err instanceof SyntaxError ? "malformed JSON" : "cannot be read"} (${err.message})` };
  }
}

// Loads the three inputs and evaluates; returns the result plus the printable report lines.
export function run({ reportPath, npmExitCode, policyPath, lockfilePath, now }) {
  const loaded = [loadJson(reportPath), loadJson(lockfilePath), loadJson(policyPath)];
  const [report, lockfile, policy] = loaded.map((l) => l.value);
  const loadErrors = loaded.flatMap((l) => (l.error ? [l.error] : []));
  const exitCode = /^\d+$/.test(String(npmExitCode ?? "")) ? Number(npmExitCode) : NaN;
  const result = evaluate({ report, npmExitCode: exitCode, lockfile, policy, now });
  result.errors.unshift(...loadErrors);
  if (result.errors.length > 0) result.verdict = "fail";

  const lines = [`npm audit policy: ${result.findings.length} finding(s) in the full audit (npm exit status ${npmExitCode ?? "missing"})`];
  for (const f of result.findings) {
    const state = f.problems.length === 0 ? "accepted by policy" : `REJECTED: ${f.problems.join("; ")}`;
    lines.push(`  ${String(f.severity).padEnd(8)} ${f.name}${f.isDirect ? " (direct)" : ""} <- ${f.advisories.join(", ") || "?"} [${f.nodes.join(", ")}] ${state}`);
  }
  for (const n of result.notices) lines.push(`NOTICE: ${n}`);
  for (const e of result.errors) lines.push(`ERROR: ${e}`);
  if (result.verdict === "pass") lines.push("PASS: the full npm audit reports no vulnerabilities.");
  else if (result.verdict === "conditional-pass") {
    const risks = policy.exceptions.filter((e) => result.findings.some((f) => f.advisories.includes(e.advisory)));
    const ids = risks.map((e) => `${e.riskId} ${e.advisory} (review by ${e.reviewDeadline} UTC)`).join(", ");
    lines.push(`CONDITIONAL PASS: the full npm audit is NOT clean; all ${result.findings.length} finding(s) are accepted dev-only exceptions: ${ids}.`);
  } else lines.push(`FAIL: ${result.errors.length} policy violation(s); the full npm audit is not acceptable.`);
  return { ...result, lines };
}

function main(argv) {
  const arg = (name) => {
    const i = argv.indexOf(name);
    return i >= 0 && i + 1 < argv.length ? argv[i + 1] : undefined;
  };
  const root = fileURLToPath(new URL("..", import.meta.url));
  const reportPath = arg("--report");
  if (!reportPath) {
    console.error("usage: node scripts/audit-policy.mjs --report FILE --npm-exit-code N");
    return 1;
  }
  const result = run({
    reportPath,
    npmExitCode: arg("--npm-exit-code"),
    policyPath: path.join(root, "security", "npm-audit-policy.json"),
    lockfilePath: path.join(root, "package-lock.json"),
    now: Date.now(),
  });
  for (const line of result.lines) (result.verdict === "fail" ? console.error : console.log)(line);
  if (process.env.GITHUB_ACTIONS === "true") {
    const summary = result.lines.at(-1).replace(/[\r\n%]/g, " ");
    console.log(`::${result.verdict === "fail" ? "error" : result.verdict === "pass" ? "notice" : "warning"} title=npm audit policy::${summary}`);
  }
  return result.verdict === "fail" ? 1 : 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = main(process.argv.slice(2));
