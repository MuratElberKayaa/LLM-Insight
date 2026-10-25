import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { readConfig } from "../../lib/server/config";

const TOKEN = "t0k3n-0123456789abcdef0123456789abcdef-SYNTHETIC";
const base = { LLMI_API_BASE_URL: "http://127.0.0.1:8000", LLMI_API_TOKEN: TOKEN, LLMI_DASHBOARD_PROJECT_ID: "1" };

function problem(env: Record<string, string | undefined>): string {
  const r = readConfig(env);
  assert.equal(r.ok, false);
  return r.ok ? "" : r.problem;
}

describe("readConfig", () => {
  it("accepts a loopback backend with a token and a project", () => {
    const r = readConfig(base);
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.config.baseUrl.origin, "http://127.0.0.1:8000");
      assert.equal(r.config.defaultProjectId, "1");
      assert.equal(r.config.timeoutMs, 5000);
    }
    assert.equal(readConfig({ ...base, LLMI_API_BASE_URL: "http://localhost:8000/" }).ok, true);
    assert.equal(readConfig({ ...base, LLMI_API_BASE_URL: "http://[::1]:8000" }).ok, true);
  });

  it("refuses any non-loopback backend, so the token can only go to the local backend", () => {
    for (const url of ["http://example.com", "http://10.0.0.5:8000", "http://127.0.0.1.nip.io:8000", "http://0.0.0.0:8000"]) {
      assert.match(problem({ ...base, LLMI_API_BASE_URL: url }), /loopback/, url);
    }
  });

  it("refuses credentials, paths, queries and odd schemes in the base URL", () => {
    assert.match(problem({ ...base, LLMI_API_BASE_URL: "http://u:p@127.0.0.1:8000" }), /bare origin/);
    assert.match(problem({ ...base, LLMI_API_BASE_URL: "http://127.0.0.1:8000/api" }), /bare origin/);
    assert.match(problem({ ...base, LLMI_API_BASE_URL: "http://127.0.0.1:8000/?x=1" }), /bare origin/);
    assert.match(problem({ ...base, LLMI_API_BASE_URL: "file:///etc/passwd" }), /http or https/);
    assert.match(problem({ ...base, LLMI_API_BASE_URL: "not a url" }), /valid URL/);
    assert.match(problem({ ...base, LLMI_API_BASE_URL: undefined }), /not set/);
  });

  it("refuses missing, placeholder and malformed tokens without echoing them", () => {
    assert.match(problem({ ...base, LLMI_API_TOKEN: undefined }), /not set/);
    assert.match(problem({ ...base, LLMI_API_TOKEN: "CHANGE_ME" }), /placeholder/);
    for (const bad of ["short", `${TOKEN} with space`, `${TOKEN}\r\nX-Injected: 1`]) {
      const p = problem({ ...base, LLMI_API_TOKEN: bad });
      assert.match(p, /invalid format/);
      assert.ok(!p.includes(bad), "the token value must never appear in a problem message");
    }
  });

  it("requires a positive integer project id", () => {
    for (const id of ["0", "-1", "1.5", "abc", "9223372036854775808", "01"]) {
      assert.match(problem({ ...base, LLMI_DASHBOARD_PROJECT_ID: id }), /positive integer/, id);
    }
    assert.match(problem({ ...base, LLMI_DASHBOARD_PROJECT_ID: undefined }), /not set/);
    assert.equal(readConfig({ ...base, LLMI_DASHBOARD_PROJECT_ID: "9223372036854775807" }).ok, true);
  });

  it("bounds the request timeout", () => {
    assert.equal(readConfig({ ...base, LLMI_API_TIMEOUT_MS: "2500" }).ok, true);
    for (const v of ["50", "40000", "abc", "1.5"]) assert.match(problem({ ...base, LLMI_API_TIMEOUT_MS: v }), /between 100 and 30000/, v);
  });
});
