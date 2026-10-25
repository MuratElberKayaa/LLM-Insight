import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { dataNotice } from "../../lib/provenance";
import { buildCsp, isAllowedMethod, isLoopbackHost } from "../../lib/request-guard";

const t = (real: number, synthetic: number, mixed: number, unknown: number) => ({ real, synthetic, mixed, unknown });
const u = (real: number, synthetic: number, unknown: number) => ({ real, synthetic, unknown });

describe("dataNotice (T48: synthetic always identified, mixed/unknown never real)", () => {
  it("empty window", () => {
    assert.equal(dataNotice(t(0, 0, 0, 0), u(0, 0, 0)).kind, "empty");
  });
  it("synthetic only", () => {
    assert.equal(dataNotice(t(0, 4, 0, 0), u(0, 6, 0)).kind, "synthetic_only");
  });
  it("real with any synthetic or mixed is flagged", () => {
    assert.equal(dataNotice(t(2, 1, 0, 0), u(3, 1, 0)).kind, "contains_synthetic");
    assert.equal(dataNotice(t(2, 0, 1, 0), u(3, 0, 0)).kind, "contains_synthetic");
  });
  it("real plus unknown is not presented as real", () => {
    assert.equal(dataNotice(t(2, 0, 0, 1), u(3, 0, 0)).kind, "contains_unknown");
  });
  it("unknown only", () => {
    assert.equal(dataNotice(t(0, 0, 0, 3), u(0, 0, 2)).kind, "unlabeled_only");
  });
  it("real only is stated as an instrumentation claim", () => {
    const n = dataNotice(t(3, 0, 0, 0), u(4, 0, 0));
    assert.equal(n.kind, "real_only");
    assert.match(n.body, /not verified/);
  });
});

describe("isLoopbackHost (DNS-rebinding guard)", () => {
  it("accepts loopback names with or without a port", () => {
    for (const h of ["127.0.0.1:3001", "localhost:3001", "LOCALHOST:3001", "[::1]:3001", "127.0.0.1", "localhost"]) {
      assert.equal(isLoopbackHost(h), true, h);
    }
  });
  it("rejects every other host", () => {
    for (const h of [null, "", "evil.example:3001", "127.0.0.1.nip.io:3001", "localhost.evil.com", "0.0.0.0:3001", "192.168.1.5:3001", "[::2]:3001", "127.0.0.1:3001:1", "user@127.0.0.1:3001"]) {
      assert.equal(isLoopbackHost(h), false, String(h));
    }
  });
});

describe("isAllowedMethod", () => {
  it("allows only GET and HEAD", () => {
    assert.equal(isAllowedMethod("GET"), true);
    assert.equal(isAllowedMethod("HEAD"), true);
    for (const m of ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) assert.equal(isAllowedMethod(m), false, m);
  });
});

describe("buildCsp", () => {
  it("uses a nonce for scripts, forbids framing and objects, no eval in production", () => {
    const csp = buildCsp("abc123", false);
    assert.match(csp, /script-src 'self' 'nonce-abc123' 'strict-dynamic'(;|$)/);
    assert.match(csp, /frame-ancestors 'none'/);
    assert.match(csp, /object-src 'none'/);
    assert.doesNotMatch(csp, /unsafe-eval/);
    assert.doesNotMatch(csp, /script-src[^;]*unsafe-inline/);
  });
  it("adds dev-only allowances in development", () => {
    assert.match(buildCsp("n", true), /'unsafe-eval'/);
  });
});
