// Test-only: replace `server-only` with an empty module. In Next.js the package resolves to
// an empty file under the `react-server` condition and throws everywhere else; unit tests
// run server modules directly in Node, so the guard is stubbed here. The guard itself is
// covered by tests/unit/security-static.test.ts and the production build.
const Module = require("node:module");
const path = require("node:path");

const resolved = require.resolve("server-only", { paths: [path.resolve(__dirname, "..", "..")] });
const stub = new Module(resolved);
stub.filename = resolved;
stub.loaded = true;
stub.exports = {};
require.cache[resolved] = stub;
