import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";
import { overviewDecoder } from "../../lib/api/schema";
import { traceDetailDecoder, traceListDecoder } from "../../lib/api/trace-schema";
import { MAX_DETAIL_RESPONSE_BYTES, MAX_RESPONSE_BYTES, getHealth, getJson, traceDetailPath, type BackendPath } from "../../lib/server/backend";
import type { DashboardConfig } from "../../lib/server/config";
import { loadOverview } from "../../lib/server/overview";
import { makeOverview } from "../fixtures/overview";
import { makeList, makeTrace, retryDetail, toWire } from "../fixtures/traces";

const TOKEN = "SYNTHETIC-test-token-0123456789abcdef0123456789";
const config: DashboardConfig = { baseUrl: new URL("http://127.0.0.1:8000"), token: TOKEN, projectId: "7", timeoutMs: 200 };

interface Call {
  url: URL;
  init: RequestInit;
}

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function fakeFetch(respond: (url: URL, init: RequestInit) => Response | Promise<Response>, calls: Call[] = []): typeof fetch {
  return (async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : input.toString());
    calls.push({ url, init: init ?? {} });
    return respond(url, init ?? {});
  }) as typeof fetch;
}

let warnings: string[] = [];
const originalWarn = console.warn;
beforeEach(() => {
  warnings = [];
  console.warn = (...args: unknown[]) => warnings.push(args.map(String).join(" "));
});
afterEach(() => {
  console.warn = originalWarn;
  // No log line may ever contain the token.
  for (const w of warnings) assert.ok(!w.includes(TOKEN), "token leaked into a log line");
});

const params = { from: "2026-10-24T15:00:00.000Z", to: "2026-10-25T15:00:00.000Z" };

describe("getJson: request", () => {
  it("calls the fixed path on the configured origin with the project, params and bearer token", async () => {
    const calls: Call[] = [];
    const r = await getJson(config, "/api/v1/overview", params, overviewDecoder, { fetch: fakeFetch(() => jsonResponse(makeOverview()), calls) });
    assert.equal(r.ok, true);
    assert.equal(calls.length, 1);
    const { url, init } = calls[0]!;
    assert.equal(url.origin, "http://127.0.0.1:8000");
    assert.equal(url.pathname, "/api/v1/overview");
    assert.equal(url.searchParams.get("project"), "7");
    assert.equal(url.searchParams.get("from"), params.from);
    assert.equal(url.searchParams.get("to"), params.to);
    assert.equal(url.search.includes(TOKEN), false, "the token is never in the URL");
    assert.equal((init.headers as Record<string, string>).Authorization, `Bearer ${TOKEN}`);
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "error");
    assert.ok(init.signal instanceof AbortSignal);
  });

  it("cannot be steered to another path or host by parameter values", async () => {
    const calls: Call[] = [];
    await getJson(config, "/api/v1/overview", { from: "../../x", to: "http://evil.example/" }, overviewDecoder, {
      fetch: fakeFetch(() => jsonResponse(makeOverview()), calls),
    });
    assert.equal(calls[0]!.url.origin, "http://127.0.0.1:8000");
    assert.equal(calls[0]!.url.pathname, "/api/v1/overview");
  });
});

describe("getJson: failures map to fixed kinds without credentials", () => {
  const cases: [string, () => Response, Record<string, unknown>][] = [
    ["401", () => jsonResponse({ error: { code: "unauthorized", message: "x" } }, 401), { kind: "unauthorized", status: 401 }],
    ["404", () => jsonResponse({ error: { code: "not_found", message: "x" } }, 404), { kind: "not_found", status: 404 }],
    ["422", () => jsonResponse({ error: { code: "invalid_request", message: "x", fields: ["from", "<b>bad</b>"] } }, 422), { kind: "invalid_request", status: 422, fields: ["from"] }],
    ["429", () => jsonResponse({ error: { code: "rate_limited" } }, 429, { "retry-after": "3" }), { kind: "rate_limited", status: 429, retryAfterSeconds: 3 }],
    ["503", () => jsonResponse({ error: { code: "storage_unavailable" } }, 503, { "retry-after": "5" }), { kind: "storage_unavailable", status: 503, retryAfterSeconds: 5 }],
    ["500", () => jsonResponse({ error: { code: "internal_error" } }, 500), { kind: "server_error", status: 500 }],
    ["418", () => new Response("teapot", { status: 418 }), { kind: "unexpected_status", status: 418 }],
    ["HTML body", () => new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }), { kind: "malformed", detail: "not JSON" }],
    ["invalid JSON", () => new Response("{", { status: 200, headers: { "content-type": "application/json" } }), { kind: "malformed", detail: "invalid JSON" }],
    ["schema mismatch", () => jsonResponse({ ...makeOverview(), provenance_verified: true }), { kind: "malformed", detail: "schema mismatch at $.provenance_verified" }],
  ];
  for (const [name, respond, expected] of cases) {
    it(name, async () => {
      const r = await getJson(config, "/api/v1/overview", params, overviewDecoder, { fetch: fakeFetch(respond) });
      assert.equal(r.ok, false);
      if (!r.ok) {
        assert.deepEqual(r.error, expected);
        assert.ok(!JSON.stringify(r.error).includes(TOKEN));
      }
      assert.equal(warnings.length, 1);
    });
  }

  it("connection refused → unreachable", async () => {
    const r = await getJson(config, "/api/v1/overview", params, overviewDecoder, {
      fetch: (async () => {
        throw new TypeError("fetch failed");
      }) as typeof fetch,
    });
    assert.deepEqual(r, { ok: false, error: { kind: "unreachable" } });
  });

  it("a backend that never answers → timeout after the configured deadline", async () => {
    const started = Date.now();
    const hanging = ((_: unknown, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      })) as typeof fetch;
    // AbortSignal.timeout() uses an unref'd timer; in a server the listening socket keeps the
    // event loop alive, in this test a ref'd interval does.
    const keepAlive = setInterval(() => {}, 1000);
    let r;
    try {
      r = await getJson(config, "/api/v1/overview", params, overviewDecoder, { fetch: hanging });
    } finally {
      clearInterval(keepAlive);
    }
    assert.deepEqual(r, { ok: false, error: { kind: "timeout" } });
    const elapsed = Date.now() - started;
    assert.ok(elapsed >= 150 && elapsed < 3000, `elapsed ${elapsed} ms`);
  });

  it("an aborted request → timeout kind (never a fake success)", async () => {
    const r = await getJson(config, "/api/v1/overview", params, overviewDecoder, {
      fetch: (async () => {
        throw new DOMException("aborted", "AbortError");
      }) as typeof fetch,
    });
    assert.deepEqual(r, { ok: false, error: { kind: "timeout" } });
  });

  it("a response larger than the cap is rejected (declared and streamed)", async () => {
    const big = "x".repeat(MAX_RESPONSE_BYTES + 1);
    const declared = await getJson(config, "/api/v1/overview", params, overviewDecoder, {
      fetch: fakeFetch(() => new Response(big, { headers: { "content-type": "application/json", "content-length": String(big.length) } })),
    });
    assert.deepEqual(declared, { ok: false, error: { kind: "malformed", detail: "response too large" } });
    const streamed = await getJson(config, "/api/v1/overview", params, overviewDecoder, {
      fetch: fakeFetch(() => {
        const chunk = new TextEncoder().encode("x".repeat(64 * 1024));
        let sent = 0;
        const body = new ReadableStream<Uint8Array>({
          pull(controller) {
            if (sent > MAX_RESPONSE_BYTES) controller.close();
            else {
              controller.enqueue(chunk);
              sent += chunk.byteLength;
            }
          },
        });
        return new Response(body, { headers: { "content-type": "application/json" } });
      }),
    });
    assert.deepEqual(streamed, { ok: false, error: { kind: "malformed", detail: "response too large" } });
  });
});

describe("getHealth", () => {
  it("maps 200 / 503 / network error", async () => {
    assert.equal(await getHealth(config, { fetch: fakeFetch(() => jsonResponse({ status: "ready" })) }), "ready");
    assert.equal(await getHealth(config, { fetch: fakeFetch(() => jsonResponse({ status: "not_ready" }, 503)) }), "not_ready");
    assert.equal(
      await getHealth(config, {
        fetch: (async () => {
          throw new TypeError("fetch failed");
        }) as typeof fetch,
      }),
      "unreachable",
    );
  });
  it("never sends the admin token to the unauthenticated probe", async () => {
    const calls: Call[] = [];
    await getHealth(config, { fetch: fakeFetch(() => jsonResponse({ status: "ready" }), calls) });
    assert.equal(calls[0]!.url.pathname, "/health/ready");
    assert.equal(JSON.stringify(calls[0]!.init).includes(TOKEN), false);
  });
});

describe("loadOverview", () => {
  const saved = { ...process.env };
  afterEach(() => {
    for (const k of ["LLMI_API_BASE_URL", "LLMI_API_TOKEN", "LLMI_DASHBOARD_PROJECT_ID", "LLMI_API_TIMEOUT_MS"]) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  });

  it("reports a configuration failure without calling the backend", async () => {
    delete process.env.LLMI_API_TOKEN;
    process.env.LLMI_API_BASE_URL = "http://127.0.0.1:8000";
    process.env.LLMI_DASHBOARD_PROJECT_ID = "1";
    const calls: Call[] = [];
    const data = await loadOverview({ range: "24h", provenance: "all", ignoredInvalid: false }, new Date(), { fetch: fakeFetch(() => jsonResponse({}), calls) });
    assert.equal(calls.length, 0);
    assert.equal(data.overview.ok, false);
    if (!data.overview.ok) assert.deepEqual(data.overview.error, { kind: "config", detail: "LLMI_API_TOKEN is not set" });
  });

  it("queries both endpoints with the same window and filter, and degrades independently", async () => {
    process.env.LLMI_API_BASE_URL = "http://127.0.0.1:8000";
    process.env.LLMI_API_TOKEN = TOKEN;
    process.env.LLMI_DASHBOARD_PROJECT_ID = "3";
    const calls: Call[] = [];
    const data = await loadOverview({ range: "7d", provenance: "synthetic", ignoredInvalid: false }, new Date("2026-10-25T14:37:00Z"), {
      fetch: fakeFetch((url) => (url.pathname.endsWith("/overview") ? jsonResponse(makeOverview()) : jsonResponse({}, 500)), calls),
    });
    assert.equal(data.overview.ok, true);
    assert.equal(data.compare.ok, false);
    const [a, b] = calls;
    for (const c of [a!, b!]) {
      assert.equal(c.url.searchParams.get("project"), "3");
      assert.equal(c.url.searchParams.get("from"), "2026-10-19T00:00:00.000Z");
      assert.equal(c.url.searchParams.get("to"), "2026-10-26T00:00:00.000Z");
      assert.equal(c.url.searchParams.get("provenance"), "synthetic");
    }
    assert.equal(calls.find((c) => c.url.pathname.endsWith("/compare"))?.url.searchParams.get("limit"), "10");
  });
});

describe("trace endpoints (Phase 3B)", () => {
  it("builds detail paths only from a 32-hex trace id", () => {
    assert.equal(traceDetailPath("56d5c0b2144b3ec23fa58c9561fbbff0"), "/api/v1/traces/56d5c0b2144b3ec23fa58c9561fbbff0");
    for (const bad of ["../overview", "56D5C0B2144B3EC23FA58C9561FBBFF0", "56d5c0b2144b3ec23fa58c9561fbbff", "56d5c0b2144b3ec23fa58c9561fbbff0/../../x", "%2e%2e", ""]) {
      assert.equal(traceDetailPath(bad), null, bad);
    }
  });

  it("refuses any path outside the runtime allowlist without calling fetch", async () => {
    const calls: Call[] = [];
    for (const p of ["/api/v1/traces/../overview", "/api/v1/traces/abc", "/admin", "/api/v1/traces/56d5c0b2144b3ec23fa58c9561fbbff0/x"]) {
      const r = await getJson(config, p as BackendPath, {}, traceListDecoder, { fetch: fakeFetch(() => jsonResponse({}), calls) });
      assert.equal(r.ok, false);
      if (!r.ok) assert.equal(r.error.kind, "config");
    }
    assert.equal(calls.length, 0);
  });

  it("sends list filters as validated parameters with the project and token", async () => {
    const calls: Call[] = [];
    const r = await getJson(config, "/api/v1/traces", { from: "2026-10-24T09:00:00.000Z", to: "2026-10-25T09:00:00.000Z", limit: "10", status: "error", cursor: "abc" }, traceListDecoder, {
      fetch: fakeFetch(() => jsonResponse(toWire(makeList([makeTrace()], "next1"))), calls),
    });
    assert.equal(r.ok, true, r.ok ? "" : JSON.stringify(r.error));
    const url = calls[0]!.url;
    assert.equal(url.pathname, "/api/v1/traces");
    assert.equal(url.searchParams.get("project"), "7");
    assert.equal(url.searchParams.get("cursor"), "abc");
    assert.equal((calls[0]!.init.headers as Record<string, string>).Authorization, `Bearer ${TOKEN}`);
  });

  it("parses detail bodies losslessly (an int64 attribute keeps its digits)", async () => {
    const body = JSON.stringify(toWire(retryDetail())).replace('"attributes":{}', '"attributes":{"gen_ai.request.seed":4611686018427387905}');
    const r = await getJson(config, "/api/v1/traces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1", {}, traceDetailDecoder, {
      fetch: fakeFetch(() => new Response(body, { status: 200, headers: { "content-type": "application/json" } })),
    });
    assert.equal(r.ok, true, r.ok ? "" : JSON.stringify(r.error));
    if (r.ok) assert.equal(String(r.data.spans[0]!.attributes["gen_ai.request.seed"]), "4611686018427387905");
  });

  it("allows the larger detail cap only when asked, and never logs the trace id", async () => {
    const big = "x".repeat(MAX_RESPONSE_BYTES + 10);
    const respond = () => new Response(big, { status: 200, headers: { "content-type": "application/json" } });
    const small = await getJson(config, "/api/v1/traces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1", {}, traceDetailDecoder, { fetch: fakeFetch(respond) });
    assert.equal(!small.ok && small.error.detail, "response too large");
    const large = await getJson(config, "/api/v1/traces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1", {}, traceDetailDecoder, { fetch: fakeFetch(respond) }, { maxBytes: MAX_DETAIL_RESPONSE_BYTES });
    assert.equal(!large.ok && large.error.detail, "invalid JSON", "within the detail cap the body is read (and here rejected as JSON)");
    assert.ok(warnings.length >= 2);
    for (const w of warnings) assert.doesNotMatch(w, /a{31}1/, "trace id must not be logged");
  });

  it("a 404 for a trace (missing, other project, unknown project) is one not_found kind", async () => {
    const r = await getJson(config, "/api/v1/traces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1", {}, traceDetailDecoder, {
      fetch: fakeFetch(() => jsonResponse({ error: { code: "not_found", message: "not found" } }, 404)),
    });
    assert.deepEqual(!r.ok && r.error, { kind: "not_found", status: 404 });
  });
});
