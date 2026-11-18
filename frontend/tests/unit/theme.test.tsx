/**
 * UI-1 (light/dark theme): the token blocks in app/globals.css stay consistent, charts read
 * the tokens instead of hex copies, status text uses the themed *-ink tokens, and the header
 * toggle renders an accessible group that only touches `data-theme` on the root element.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ThemeToggle, applyThemeMode } from "../../components/theme-toggle";

// Compiled to .test-build/tests/unit; the sources are three levels up.
const ROOT = path.resolve(__dirname, "..", "..", "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replaceAll("\r\n", "\n");
const css = read("app/globals.css");

// Status hues are fixed in both themes (text uses the themed *-ink variants instead).
const FIXED = new Set(["--color-good", "--color-warning", "--color-serious", "--color-critical"]);

type Decls = Map<string, string>;

function declarations(body: string): Decls {
  const out: Decls = new Map();
  for (const m of body.matchAll(/(--[\w-]+|color-scheme)\s*:\s*([^;]+);/g)) out.set(m[1]!, m[2]!.trim());
  return out;
}

/** Body of the first `{ ... }` block (no nesting) that follows `selector`. */
function blockAfter(source: string, selector: string): string {
  const at = source.indexOf(selector);
  assert.ok(at >= 0, `missing block ${selector}`);
  const open = source.indexOf("{", at + selector.length - 1);
  const close = source.indexOf("}", open);
  return source.slice(open + 1, close);
}

const themeTokens = [...declarations(blockAfter(css, "@theme {")).keys()].filter((k) => k.startsWith("--color-"));
const mediaLight = declarations(blockAfter(css, ':root:where(:not([data-theme="dark"])) {'));
const forcedLight = declarations(blockAfter(css, ':root[data-theme="light"] {'));

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : /\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

describe("theme tokens (app/globals.css)", () => {
  it("parsed the dark defaults", () => {
    assert.ok(themeTokens.includes("--color-surface"));
    assert.ok(themeTokens.includes("--color-warning-ink"));
  });

  it("the OS-light block and the forced-light block are identical", () => {
    assert.deepEqual([...mediaLight.entries()].sort(), [...forcedLight.entries()].sort());
    assert.equal(forcedLight.get("color-scheme"), "light");
  });

  it("every themed color token has a light value, and fixed status hues have none", () => {
    for (const t of themeTokens) {
      if (FIXED.has(t)) assert.equal(forcedLight.has(t), false, `${t} is fixed in both themes`);
      else assert.ok(forcedLight.has(t), `${t} has no light value`);
    }
  });

  it("the light block defines no token the dark defaults lack", () => {
    for (const k of forcedLight.keys()) if (k !== "color-scheme") assert.ok(themeTokens.includes(k), `${k} is not a @theme token`);
  });

  it("a forced dark theme opts out of the OS-light block", () => {
    assert.match(css, /@media \(prefers-color-scheme: light\) \{\s*:root:where\(:not\(\[data-theme="dark"\]\)\)/);
  });
});

describe("components follow the theme", () => {
  it("charts reference tokens, not hex copies", () => {
    for (const f of walk(path.join(ROOT, "components", "charts"))) {
      const src = readFileSync(f, "utf8").replace(/\/\/.*$|\/\*[\s\S]*?\*\//gm, "");
      assert.doesNotMatch(src, /#[0-9a-fA-F]{3,8}\b|rgba?\(/, path.basename(f));
    }
  });

  it("status text uses the themed warning ink (the warning hue is 1.8:1 on the light surface)", () => {
    for (const f of [...walk(path.join(ROOT, "components")), ...walk(path.join(ROOT, "app"))]) {
      assert.doesNotMatch(readFileSync(f, "utf8"), /\btext-warning(?!-ink)\b/, path.basename(f));
    }
  });
});

describe("ThemeToggle", () => {
  it("renders a labeled group of three named buttons with System pressed by default", () => {
    const m = renderToStaticMarkup(<ThemeToggle />);
    assert.match(m, /role="group" aria-label="Color theme"/);
    assert.equal((m.match(/<button /g) ?? []).length, 3);
    for (const label of ["System", "Light", "Dark"]) assert.match(m, new RegExp(`<span class="sr-only">${label}</span>`));
    assert.match(m, /aria-pressed="true"[^>]*data-theme-mode="system"/);
    assert.equal((m.match(/aria-pressed="true"/g) ?? []).length, 1);
  });

  it("applyThemeMode sets data-theme for light/dark and removes it for system", () => {
    const root = { dataset: {} as DOMStringMap };
    applyThemeMode(root, "light");
    assert.equal(root.dataset.theme, "light");
    applyThemeMode(root, "dark");
    assert.equal(root.dataset.theme, "dark");
    applyThemeMode(root, "system");
    assert.equal("theme" in root.dataset, false);
  });
});
