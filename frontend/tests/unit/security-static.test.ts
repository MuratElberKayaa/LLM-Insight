/**
 * Static source checks for the server/client boundary (T42 source half; the build-output
 * half is scripts/check-bundle.mjs). Reads the source tree only.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";

// Compiled to .test-build/tests/unit; the sources are three levels up.
const ROOT = path.resolve(__dirname, "..", "..", "..");
const SOURCE_DIRS = ["app", "components", "lib"];
const ROOT_FILES = ["proxy.ts", "next.config.ts"];

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

const files = [...SOURCE_DIRS.flatMap((d) => walk(path.join(ROOT, d))), ...ROOT_FILES.map((f) => path.join(ROOT, f))];
const read = (f: string) => readFileSync(f, "utf8");
const rel = (f: string) => path.relative(ROOT, f).replaceAll("\\", "/");
const isClient = (src: string) => /^\s*["']use client["'];?/.test(src);

describe("server/client boundary", () => {
  it("found the source tree", () => {
    assert.ok(files.length > 15, `only ${files.length} files`);
  });

  it("every lib/server module starts with import 'server-only'", () => {
    const server = files.filter((f) => rel(f).startsWith("lib/server/"));
    assert.ok(server.length >= 3);
    for (const f of server) assert.match(read(f), /^import "server-only";/, rel(f));
  });

  it("no client component imports server modules or reads process.env", () => {
    for (const f of files) {
      const src = read(f);
      if (!isClient(src)) continue;
      assert.doesNotMatch(src, /lib\/server\//, `${rel(f)} imports a server module`);
      assert.doesNotMatch(src, /process\.env/, `${rel(f)} reads process.env`);
    }
  });

  it("the token variable is read in exactly one module", () => {
    const readers = files.filter((f) => /LLMI_API_TOKEN/.test(read(f)) && /process\.env|env\.LLMI_API_TOKEN/.test(read(f)));
    assert.deepEqual(readers.map(rel), ["lib/server/config.ts"]);
  });

  it("uses no NEXT_PUBLIC_ variable, no browser storage and no raw HTML injection", () => {
    for (const f of files) {
      const src = read(f);
      // Code access only; comments may name the prefix to forbid it.
      assert.doesNotMatch(src, /process\.env(\.|\[\s*["'`])NEXT_PUBLIC_|^\s*NEXT_PUBLIC_\w+\s*=/m, rel(f));
      assert.doesNotMatch(src, /localStorage|sessionStorage|indexedDB|document\.cookie/, rel(f));
      assert.doesNotMatch(src, /dangerouslySetInnerHTML|innerHTML/, rel(f));
    }
  });

  it("has no route handlers or server actions (the UI cannot proxy the backend)", () => {
    const appFiles = walk(path.join(ROOT, "app")).map(rel);
    assert.deepEqual(appFiles.filter((f) => /\/route\.(ts|tsx)$/.test(f)), []);
    for (const f of files) assert.doesNotMatch(read(f), /^\s*["']use server["']/m, rel(f));
  });

  it("only lib/server modules call the backend client, and the client keeps a path allowlist", () => {
    const callers = files.filter((f) => /\bgetJson\(/.test(read(f))).map(rel).sort();
    assert.deepEqual(callers, ["lib/server/context.ts", "lib/server/models.ts", "lib/server/overview.ts", "lib/server/traces.ts"]);
    assert.match(read(path.join(ROOT, "lib/server/backend.ts")), /const PATH_RE = /);
  });

  it("production code never imports test fixtures", () => {
    for (const f of files) assert.doesNotMatch(read(f), /tests\/fixtures|fixtures\/overview/, rel(f));
  });
});
