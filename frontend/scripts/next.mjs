// Runs the Next.js CLI with telemetry disabled and the pinned Node.js version enforced.
// Telemetry is switched off per process instead of `next telemetry disable`, which would
// write to the user's global configuration.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const required = readFileSync(new URL("../.nvmrc", import.meta.url), "utf8").trim();
if (process.versions.node !== required) {
  console.error(`Node ${required} is required (found ${process.versions.node}). Run: python -I scripts/dev/install_node.py --print-bin and prepend it to PATH.`);
  process.exit(1);
}

const nextBin = createRequire(import.meta.url).resolve("next/dist/bin/next");
const result = spawnSync(process.execPath, [nextBin, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
});
process.exit(result.status ?? 1);
