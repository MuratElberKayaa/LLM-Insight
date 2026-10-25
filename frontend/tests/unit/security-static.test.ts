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

  it("has no route handlers (the UI cannot proxy the backend)", () => {
    const appFiles = walk(path.join(ROOT, "app")).map(rel);
    assert.deepEqual(appFiles.filter((f) => /\/route\.(ts|tsx)$/.test(f)), []);
  });

  it("Server Actions exist only in the two Phase 4B action modules, each a fixed write", () => {
    // ARCHITECTURE.md §5.8: every "use server" module is listed here; every export of one is
    // a public POST endpoint, so each must be an async function that validates its input and
    // calls one lib/server write. Nothing else may declare a Server Function, inline or not.
    const actionFiles = files.filter((f) => /["']use server["']/.test(read(f))).map(rel).sort();
    assert.deepEqual(actionFiles, ["lib/actions/datasets.ts", "lib/actions/reviews.ts"]);
    for (const f of actionFiles) {
      const src = read(path.join(ROOT, f));
      assert.match(src, /^"use server";\n/, `${f}: the directive must be the first statement`);
      assert.match(src, /^import "server-only";$/m, f);
      const exports = src.match(/^export .*$/gm) ?? [];
      assert.ok(exports.length >= 2, f);
      for (const e of exports) assert.match(e, /^export async function submit\w+\(_prev: ActionState, formData: FormData\): Promise<ActionState> \{$/, `${f}: ${e}`);
      assert.match(src, /parse\w+Form\(formData\)/, `${f}: every action re-validates its form`);
      assert.doesNotMatch(src, /\b(getJson|sendJson|fetch)\(/, `${f}: actions call lib/server, never the transport`);
    }
  });

  it("only lib/server modules call the backend client, and the client keeps path allowlists", () => {
    const readers = files.filter((f) => /\bgetJson\(/.test(read(f))).map(rel).sort();
    assert.deepEqual(readers, ["lib/server/context.ts", "lib/server/datasets.ts", "lib/server/models.ts", "lib/server/overview.ts", "lib/server/reviews.ts", "lib/server/traces.ts"]);
    const writers = files.filter((f) => /\bsendJson\(/.test(read(f))).map(rel).sort();
    assert.deepEqual(writers, ["lib/server/datasets.ts", "lib/server/reviews.ts"]);
    const backend = read(path.join(ROOT, "lib/server/backend.ts"));
    assert.match(backend, /const PATH_RE =/);
    assert.match(backend, /const WRITE_ROUTES: /);
    // Writes happen only through sendJson: no other module sets a mutating fetch method.
    for (const f of files) {
      if (rel(f) === "lib/server/backend.ts") continue;
      assert.doesNotMatch(read(f), /method:\s*["'`](POST|PUT|PATCH|DELETE)/, rel(f));
    }
  });

  it("the request guard limits POST to the form pages and checks the origin", () => {
    const proxy = read(path.join(ROOT, "proxy.ts"));
    assert.match(proxy, /isWritePage\(request\.nextUrl\.pathname\)/);
    assert.match(proxy, /actionRequestProblem\(/);
    assert.doesNotMatch(read(path.join(ROOT, "next.config.ts")), /allowedOrigins:/, "no extra origins may invoke actions");
  });

  it("source files contain no invisible or bidirectional control characters (Trojan Source)", () => {
    for (const f of [...files, ...walk(path.join(ROOT, "tests"))]) {
      assert.doesNotMatch(read(f), /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff\ue000-\uf8ff]/, rel(f));
    }
  });

  it("production code never imports test fixtures", () => {
    for (const f of files) assert.doesNotMatch(read(f), /tests\/fixtures|fixtures\/overview/, rel(f));
  });
});
