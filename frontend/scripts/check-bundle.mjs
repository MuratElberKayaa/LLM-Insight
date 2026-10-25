// T42 (build half): the admin token must not appear in the build output.
//
// Reads the real token from frontend/.env.local (and the backend's LLMI_ADMIN_TOKEN from the
// root .env, when present) and searches every file under .next/ except the compiler cache:
// - the token VALUE must appear nowhere (client bundle, server bundle, manifests);
// - the token variable NAMES must not appear in the browser bundle (.next/static).
// Prints counts and file paths only, never a secret value. Exit 1 on any finding.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const nextDir = path.join(root, ".next");

function readEnv(file) {
  const out = {};
  if (!existsSync(file)) return out;
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    const i = line.indexOf("=");
    if (i > 0 && !line.trimStart().startsWith("#")) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return out;
}

const secrets = new Set(
  [readEnv(path.join(root, ".env.local")).LLMI_API_TOKEN, readEnv(path.join(root, "..", ".env")).LLMI_ADMIN_TOKEN, process.env.LLMI_API_TOKEN].filter(
    (v) => typeof v === "string" && v.length >= 16 && v !== "CHANGE_ME",
  ),
);
const NAMES = ["LLMI_API_TOKEN", "LLMI_ADMIN_TOKEN", "LLMI_KEY_PEPPER", "LLMI_DATABASE_URL", "POSTGRES_PASSWORD"];

if (!existsSync(nextDir)) {
  console.error("No .next build output; run `npm run build` first.");
  process.exit(1);
}
if (secrets.size === 0) {
  console.error("No token found in frontend/.env.local, ../.env or LLMI_API_TOKEN; nothing to search for.");
  process.exit(1);
}

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === "cache" && dir === nextDir ? [] : walk(full);
    return [full];
  });
}

const files = walk(nextDir);
const clientFiles = files.filter((f) => f.startsWith(path.join(nextDir, "static")));
let findings = 0;
let scannedBytes = 0;
for (const file of files) {
  const buf = readFileSync(file);
  scannedBytes += buf.length;
  const text = buf.toString("latin1");
  for (const s of secrets) {
    if (text.includes(s)) {
      findings += 1;
      console.error(`FINDING: token value in ${path.relative(root, file)}`);
    }
  }
}
for (const file of clientFiles) {
  const text = readFileSync(file, "latin1");
  for (const name of NAMES) {
    if (text.includes(name)) {
      findings += 1;
      console.error(`FINDING: ${name} referenced in browser bundle ${path.relative(root, file)}`);
    }
  }
}
console.log(
  `check-bundle: ${files.length} files (${(scannedBytes / 1024 / 1024).toFixed(1)} MiB) scanned, ${clientFiles.length} in the browser bundle; ` +
    `${secrets.size} secret value(s) and ${NAMES.length} variable names searched; findings: ${findings}`,
);
process.exit(findings === 0 ? 0 : 1);
