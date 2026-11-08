import assert from "node:assert/strict";
import { test } from "node:test";
import { e2eBrowser } from "../setup/e2e-browser";

test("the local default stays Microsoft Edge", () => {
  assert.deepEqual(e2eBrowser({}), { channel: "msedge" });
});

test("only LLMI_E2E_CI=1 selects the Playwright Chromium", () => {
  assert.deepEqual(e2eBrowser({ LLMI_E2E_CI: "1" }), { browserName: "chromium" });
});

test("any other value keeps the local default", () => {
  for (const value of ["", "0", "true", "yes", "1 "]) {
    assert.deepEqual(e2eBrowser({ LLMI_E2E_CI: value }), { channel: "msedge" });
  }
});
