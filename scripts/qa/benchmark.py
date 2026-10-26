"""Phase 5B local benchmark (master plan section I; TEST_MATRIX "Benchmark"; no pre-set targets).

Workloads (SYNTHETIC spans only, loopback only, no provider call):
  A. ingest: OTLP/HTTP protobuf batches of 100, 500 and 1000 spans (10-span traces: one SERVER
     root + 9 CLIENT inference children with provider-reported usage), fresh and re-ingested
     (identical payload again: the idempotent duplicate path); row counts checked after re-ingest.
  B. trace open on a dataset of exactly 10,000 spans (1x2000, 1x1000, 10x100, 600x10 spans):
     GET /api/v1/traces/{id} per trace size, the trace list and the overview through the API, and
     the dashboard's server-rendered trace page (production build) for the same traces.
Method: client wall time per request (send -> last body byte, persistent HTTP/1.1 connection,
time.perf_counter_ns), warm-up requests discarded, several runs; p50/p95 by linear interpolation
(NumPy "linear", the same definition as T24). Client and server share the machine.

Everything runs in the test-owned database named on the command line, through a separate
backend (and dashboard) started here and stopped at the end. The ingest key and the admin token
are read/created in memory and never printed.
Usage: backend/.venv/Scripts/python -I scripts/qa/benchmark.py --repo <repo> --db llmi_bench5b_test --out <dir>
"""

import argparse
import http.client
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--db", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--api-port", type=int, default=8012)
ap.add_argument("--dash-port", type=int, default=3006)
ap.add_argument("--runs", type=int, default=3)
args = ap.parse_args()
if not args.db.endswith("_test"):
    sys.exit("refusing: the benchmark database name must end in _test (test-owned)")

REPO = Path(args.repo)
BACKEND = REPO / "backend"
PY = str(BACKEND / ".venv" / "Scripts" / "python.exe")
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
import psycopg
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    InstrumentationScope,
    KeyValue,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

env_file = dict(
    ln.split("=", 1)
    for ln in (REPO / ".env").read_text(encoding="utf-8").splitlines()
    if "=" in ln and not ln.lstrip().startswith("#")
)
TOKEN = env_file["LLMI_ADMIN_TOKEN"].strip()
base_url = env_file["LLMI_DATABASE_URL"].strip()
DB_URL = re.sub(r"/[^/?]+(\?|$)", "/" + args.db + r"\1", base_url, count=1)
PG_URL = DB_URL.replace("postgresql+psycopg://", "postgresql://")
assert DB_URL.rsplit("/", 1)[1].startswith(args.db)

CHILD_ENV = {
    **os.environ,
    "LLMI_DATABASE_URL": DB_URL,
    "LLMI_PORT": str(args.api_port),
    # Benchmark only: raise the per-process buckets so the measurement is not the limiter.
    "LLMI_RATE_LIMIT_INGEST_RPS": "100000",
    "LLMI_RATE_LIMIT_INGEST_BURST": "100000",
    "LLMI_RATE_LIMIT_API_RPS": "100000",
    "LLMI_RATE_LIMIT_API_BURST": "100000",
}


def pctl(values, q):
    v = sorted(values)
    if not v:
        return None
    pos = (len(v) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def stats(ms):
    return {
        "n": len(ms),
        "p50_ms": round(pctl(ms, 0.5), 2),
        "p95_ms": round(pctl(ms, 0.95), 2),
        "min_ms": round(min(ms), 2),
        "max_ms": round(max(ms), 2),
    }


def sh(cmd, **kw):
    return subprocess.run(
        cmd, capture_output=True, text=True, env=CHILD_ENV, check=False, **kw
    )


def reset_db():
    for c in (["dropdb", "--if-exists", "--force"], ["createdb"]):
        r = subprocess.run(
            [
                "docker",
                "exec",
                "llminsight-postgres-1",
                *c,
                "-U",
                env_file["POSTGRES_USER"].strip(),
                args.db,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
    r = sh([PY, "-m", "alembic", "upgrade", "head"], cwd=BACKEND)
    assert r.returncode == 0, "alembic failed"
    r = sh([PY, "-m", "app.cli", "create-key", "--project", "bench-5b"], cwd=BACKEND)
    assert r.returncode == 0, "create-key failed"
    key = re.search(r"lli_[A-Za-z0-9_-]+", r.stdout).group(0)
    return key


def start_backend():
    log = open(OUT / f"bench-backend-{int(time.time())}.log", "w")  # noqa: SIM115 - outlives the call (child stdout)
    p = subprocess.Popen(
        [PY, "-m", "app"],
        cwd=BACKEND,
        env=CHILD_ENV,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(200):
        try:
            c = http.client.HTTPConnection("127.0.0.1", args.api_port, timeout=2)
            c.request("GET", "/health/ready")
            if c.getresponse().status == 200:
                return p
        except OSError:
            pass
        time.sleep(0.1)
    p.kill()
    sys.exit("bench backend did not start")


def start_dashboard():
    node_bin = subprocess.run(
        [
            sys.executable,
            "-I",
            str(REPO / "scripts/dev/install_node.py"),
            "--print-bin",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    denv = {
        **os.environ,
        "PATH": node_bin + os.pathsep + os.environ["PATH"],
        "LLMI_API_BASE_URL": f"http://127.0.0.1:{args.api_port}",
        "LLMI_API_TOKEN": TOKEN,
        "LLMI_DASHBOARD_PROJECT_ID": "1",
    }
    log = open(OUT / "bench-dashboard.log", "w")  # noqa: SIM115 - outlives the call (child stdout)
    p = subprocess.Popen(
        [
            str(Path(node_bin) / "node.exe"),
            "scripts/next.mjs",
            "start",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(args.dash_port),
        ],
        cwd=REPO / "frontend",
        env=denv,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(300):
        try:
            c = http.client.HTTPConnection("127.0.0.1", args.dash_port, timeout=2)
            c.request("GET", "/no-such-page")
            c.getresponse().read()
            return p
        except OSError:
            time.sleep(0.1)
    p.kill()
    sys.exit("bench dashboard did not start")


def stop(p):
    """Kill the process tree (next.mjs spawns the Next.js server as a child)."""
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False
    )
    p.wait(30)


def kv(d):
    out = []
    for k, v in d.items():
        av = AnyValue(int_value=v) if isinstance(v, int) else AnyValue(string_value=v)
        out.append(KeyValue(key=k, value=av))
    return out


def trace_spans(n_spans, t0):
    """One trace: SERVER root + (n_spans - 1) CLIENT inference children, provider-reported usage."""
    tid = uuid.uuid4().bytes
    root = uuid.uuid4().bytes[:8]
    spans = [
        Span(
            trace_id=tid,
            span_id=root,
            name="POST /v1/summarize",
            kind=Span.SPAN_KIND_SERVER,
            start_time_unix_nano=t0,
            end_time_unix_nano=t0 + n_spans * 2_000_000,
            attributes=kv(
                {"http.request.method": "POST", "http.route": "/v1/summarize"}
            ),
        )
    ]
    for i in range(n_spans - 1):
        s = t0 + 1_000_000 + i * 2_000_000
        spans.append(
            Span(
                trace_id=tid,
                span_id=uuid.uuid4().bytes[:8],
                parent_span_id=root,
                name="chat gpt-4o-mini",
                kind=Span.SPAN_KIND_CLIENT,
                start_time_unix_nano=s,
                end_time_unix_nano=s + 1_500_000,
                attributes=kv(
                    {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.provider.name": "llminsight.mock",
                        "gen_ai.request.model": "gpt-4o-mini",
                        "llminsight.provenance": "synthetic",
                        "llminsight.call_id": str(uuid.uuid4()),
                        "llminsight.attempt": 1,
                        "gen_ai.usage.input_tokens": 120,
                        "gen_ai.usage.output_tokens": 48,
                        "llminsight.usage_source": "provider_reported",
                    }
                ),
            )
        )
    return tid.hex(), spans


def payload(trace_sizes, service="bench-svc"):
    t0 = time.time_ns() - 60_000_000_000
    ids, spans = [], []
    for n in trace_sizes:
        tid, sp = trace_spans(n, t0)
        ids.append(tid)
        spans += sp
    req = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=kv({"service.name": service})),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(name="phase5b.bench", version="1"),
                        spans=spans,
                    )
                ],
            )
        ]
    )
    return ids, req.SerializeToString()


class Client:
    def __init__(self, port):
        self.port = port
        self.c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)

    def req(self, method, path, body=None, headers=None):
        t = time.perf_counter_ns()
        try:
            self.c.request(method, path, body=body, headers=headers or {})
            r = self.c.getresponse()
            data = r.read()
        except (http.client.HTTPException, OSError):
            self.c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
            raise
        return (time.perf_counter_ns() - t) / 1e6, r.status, len(data), r


def counts():
    with psycopg.connect(PG_URL) as c:
        return {
            t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("traces", "spans", "usage_records")
        }


results = {
    "environment": {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "db": args.db,
        "api_port": args.api_port,
        "dash_port": args.dash_port,
        "runs": args.runs,
    }
}

# ------------------------------------------------------------------ A. ingest
key = reset_db()
backend = start_backend()
try:
    cl = Client(args.api_port)
    H = {"Content-Type": "application/x-protobuf", "X-Project-Key": key}
    WARM, MEAS = 3, 15
    ingest = {}
    for size in (100, 500, 1000):
        per_run = []
        for run in range(args.runs):
            fresh, dup, wire = [], [], 0
            for _ in range(WARM):
                _, b = payload([10] * (size // 10))
                ms, st, _, _ = cl.req("POST", "/v1/traces", b, H)
                assert st == 200, st
            bodies = [payload([10] * (size // 10))[1] for _ in range(MEAS)]
            wire = len(bodies[0])
            for b in bodies:
                ms, st, _, _ = cl.req("POST", "/v1/traces", b, H)
                assert st == 200, st
                fresh.append(ms)
            before = counts()
            for b in bodies:
                ms, st, _, _ = cl.req("POST", "/v1/traces", b, H)
                assert st == 200, st
                dup.append(ms)
            after = counts()
            per_run.append(
                {
                    "run": run + 1,
                    "fresh": stats(fresh),
                    "reingest": stats(dup),
                    "fresh_spans_per_s_p50": round(size / (pctl(fresh, 0.5) / 1000), 1),
                    "rows_unchanged_after_reingest": before == after,
                    "wire_bytes": wire,
                }
            )
            assert before == after, (before, after)
        ingest[str(size)] = per_run
        print(
            f"ingest {size}: "
            + "; ".join(
                f"run{r['run']} fresh p50 {r['fresh']['p50_ms']} p95 {r['fresh']['p95_ms']} | re p50 {r['reingest']['p50_ms']} p95 {r['reingest']['p95_ms']}"
                for r in per_run
            ),
            flush=True,
        )
    results["ingest"] = {
        "warmup_per_run": WARM,
        "measured_per_run": MEAS,
        "trace_shape": "10 spans (1 SERVER + 9 CLIENT)",
        "sizes": ingest,
        "final_counts": counts(),
    }
finally:
    stop(backend)

# ------------------------------------------------------------------ B. trace open on 10k spans
key = reset_db()
backend = start_backend()
dash = None
try:
    cl = Client(args.api_port)
    H = {"Content-Type": "application/x-protobuf", "X-Project-Key": key}
    ids = {}
    for label, sizes in (("2000", [2000]), ("1000", [1000]), ("100", [100] * 10)):
        tids, b = payload(sizes, service=f"bench-{label}")
        assert cl.req("POST", "/v1/traces", b, H)[1] == 200
        ids[label] = tids[0]
    small = []
    for _ in range(6):  # 600 x 10 spans, 100 traces (1000 spans) per request
        tids, b = payload([10] * 100, service="bench-10")
        assert cl.req("POST", "/v1/traces", b, H)[1] == 200
        small += tids
    ids["10"] = small[0]
    ds = counts()
    assert ds["spans"] == 10_000, ds
    results["dataset"] = {
        "counts": ds,
        "composition": "1x2000 + 1x1000 + 10x100 + 600x10 spans",
        "trace_ids": ids,
    }
    print("dataset", ds, flush=True)

    A = {"Authorization": f"Bearer {TOKEN}"}
    targets = {
        f"api trace detail {n} spans": f"/api/v1/traces/{ids[n]}?project=1"
        for n in ("10", "100", "1000", "2000")
    }
    targets["api trace diagnostics 2000 spans"] = (
        f"/api/v1/traces/{ids['2000']}/diagnostics?project=1"
    )
    targets["api trace list limit=50"] = "/api/v1/traces?project=1&limit=50"
    targets["api overview"] = "/api/v1/overview?project=1"
    WARM, MEAS = 5, 50
    api = {}
    for name, path in targets.items():
        per_run = []
        for run in range(args.runs):
            for _ in range(WARM):
                assert cl.req("GET", path, headers=A)[1] == 200
            ms_all, size = [], 0
            for _ in range(MEAS):
                ms, st, n, _ = cl.req("GET", path, headers=A)
                assert st == 200, (name, st)
                ms_all.append(ms)
                size = n
            per_run.append({"run": run + 1, **stats(ms_all), "response_bytes": size})
        api[name] = per_run
        print(name, [(r["p50_ms"], r["p95_ms"]) for r in per_run], flush=True)
    results["api"] = {"warmup_per_run": WARM, "measured_per_run": MEAS, "targets": api}

    dash = start_dashboard()
    dc = Client(args.dash_port)
    DH = {"Host": f"127.0.0.1:{args.dash_port}"}
    dtargets = {
        f"dashboard trace page {n} spans": f"/traces/{ids[n]}?project=1"
        for n in ("10", "100", "1000", "2000")
    }
    dtargets["dashboard trace list"] = "/traces?project=1"
    dtargets["dashboard overview"] = "/?project=1"
    WARM, MEAS = 3, 20
    dres = {}
    for name, path in dtargets.items():
        per_run = []
        for run in range(args.runs):
            for _ in range(WARM):
                dc.req("GET", path, headers=DH)
            ms_all, size, statuses = [], 0, set()
            for _ in range(MEAS):
                ms, st, n, _ = dc.req("GET", path, headers=DH)
                statuses.add(st)
                ms_all.append(ms)
                size = n
            per_run.append(
                {
                    "run": run + 1,
                    **stats(ms_all),
                    "response_bytes": size,
                    "statuses": sorted(statuses),
                }
            )
        dres[name] = per_run
        print(
            name,
            [(r["p50_ms"], r["p95_ms"], r["statuses"]) for r in per_run],
            flush=True,
        )
    results["dashboard"] = {
        "warmup_per_run": WARM,
        "measured_per_run": MEAS,
        "targets": dres,
        "note": "server-rendered HTML, full streamed body; browser parse/paint not included",
    }
finally:
    if dash:
        stop(dash)
    stop(backend)

(OUT / "bench-5b.json").write_text(json.dumps(results, indent=1))
print("written", OUT / "bench-5b.json")
