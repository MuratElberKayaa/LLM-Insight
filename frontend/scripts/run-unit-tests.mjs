// Unit and component tests without an extra test framework: the pinned TypeScript compiles
// lib/, components/ and tests/unit/ to CommonJS in .test-build/, then Node's built-in test
// runner executes them. tests/setup/register.cjs stubs `server-only` (which throws outside
// the React Server Components environment by design).
import { spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const require = createRequire(import.meta.url);
const run = (args) => spawnSync(process.execPath, args, { cwd: root, stdio: "inherit", env: { ...process.env, NODE_ENV: "test" } });

rmSync(new URL("../.test-build", import.meta.url), { recursive: true, force: true });
const tsc = run([require.resolve("typescript/bin/tsc"), "-p", "tsconfig.test.json"]);
if (tsc.status !== 0) process.exit(tsc.status ?? 1);

const tests = run([
  "--enable-source-maps",
  "--require",
  "./tests/setup/register.cjs",
  "--test",
  "--test-reporter=spec",
  ...process.argv.slice(2),
  ".test-build/tests/unit/**/*.test.js",
]);
process.exit(tests.status ?? 1);
