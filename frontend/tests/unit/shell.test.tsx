/**
 * UI-2 (application shell): the grouped navigation keeps every page link and its accessible
 * name, the icons are decorative, and the shell keeps its landmarks and notices.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, it } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { BrandMark, DatasetsIcon, ExperimentsIcon, LockIcon, ModelsIcon, OverviewIcon, TracesIcon } from "../../components/icons";
import { NAV_GROUPS } from "../../components/side-nav";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replaceAll("\r\n", "\n");

describe("navigation groups", () => {
  it("lists every built page once, grouped Observe, Analyze, Evaluate", () => {
    assert.deepEqual(
      NAV_GROUPS.map((g) => [g.label, g.items.map((i) => [i.label, i.href])]),
      [
        // UI-R1: Models moved to its own Analyze group; Sessions (Phase 10C) stays under Observe.
        ["Observe", [["Overview", "/"], ["Traces", "/traces"], ["Sessions", "/sessions"]]],
        ["Analyze", [["Models", "/models"]]],
        ["Evaluate", [["Datasets", "/datasets"], ["Experiments", "/experiments"]]],
      ],
    );
  });

  it("keeps the link text as the accessible name (the icon adds no text)", () => {
    for (const item of NAV_GROUPS.flatMap((g) => g.items)) {
      const m = renderToStaticMarkup(<>{item.icon}</>);
      assert.match(m, /^<svg aria-hidden="true" focusable="false"/, item.label);
      assert.equal(m.replace(/<[^>]+>/g, ""), "", item.label);
    }
  });
});

describe("icons", () => {
  it("every icon is decorative", () => {
    for (const Icon of [OverviewIcon, TracesIcon, ModelsIcon, DatasetsIcon, ExperimentsIcon, LockIcon, BrandMark]) {
      const m = renderToStaticMarkup(<Icon />);
      assert.match(m, /aria-hidden="true"/);
      assert.doesNotMatch(m, /<title|role="img"/);
    }
  });
});

describe("app shell source", () => {
  // UI-R3: the skip link and the main landmark moved into the client ShellFrame (collapsible sidebar).
  const src = read("components/app-shell.tsx") + read("components/shell-frame.tsx");

  it("keeps the skip link, the main landmark and the primary navigation", () => {
    assert.match(src, /href="#main"[\s\S]*Skip to content/);
    assert.match(src, /<main id="main"/);
    assert.match(src, /<SideNav \/>/);
  });

  it("still states that the dashboard is local only and not for public deployment", () => {
    assert.match(src, /Local only/);
    assert.match(src, /Not for public deployment\./);
  });

  it("hosts the backend status slot and the theme toggle in the sidebar", () => {
    const aside = src.slice(src.indexOf("<aside"), src.indexOf("</aside>"));
    assert.match(aside, /\{status\}/);
    assert.match(aside, /<ThemeToggle \/>/);
  });
});
