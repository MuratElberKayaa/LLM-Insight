"""Backend alternate-path probe (Phase 5B, Step 4). Raw sockets, no client normalization.

1. Without credentials: every registered (method, path) of /api/v1 and /v1/traces, in many
   alternate spellings, under every method -> never 2xx (expected 401/404/405/307/308).
2. With the admin token (read from .env, never printed): alternate spellings of the write
   routes with an empty JSON object -> never 2xx/201 (nothing can be stored: the body is invalid).
3. Foreign Host header with the token on a read route: recorded (the backend has no Host check;
   its boundary is the loopback bind plus the token, which a DNS-rebinding page cannot send).
Usage: python -I scripts/qa/backend_probe.py <port> <.env> <out.json>
"""

import json
import socket
import sys
import urllib.request
from pathlib import Path

PORT, ENV, OUT = int(sys.argv[1]), sys.argv[2], sys.argv[3]
env = dict(
    ln.split("=", 1)
    for ln in Path(ENV).read_text(encoding="utf-8").splitlines()
    if "=" in ln and not ln.startswith("#")
)
TOKEN = env["LLMI_ADMIN_TOKEN"].strip()
H = f"127.0.0.1:{PORT}"
spec = json.loads(urllib.request.urlopen(f"http://{H}/openapi.json", timeout=10).read())
routes = sorted((m.upper(), p) for p, ops in spec["paths"].items() for m in ops)
TRACE = "0123456789abcdef0123456789abcdef"


def concrete(p):
    return (
        p.replace("{trace_id}", TRACE)
        .replace("{review_id}", "1")
        .replace("{dataset_id}", "1")
        .replace("{experiment_id}", "1")
        .replace("{case_id}", "1")
        .replace("{id}", "1")
    )


def spellings(p):
    head, _, tail = p.rpartition("/")
    out = {
        p,
        p + "/",
        "/" + p,
        p.replace("/api/v1/", "/api/v1//"),
        p.replace("/api/", "/api/./"),
        p.replace("/api/v1/", "/api/x/../v1/"),
        p.upper(),
        p + "%2f",
        p + ";x",
        p + "%00",
        p + "?x=1",
        p.replace("/api/", "/%61pi/"),
        p.replace("/v1/", "/v1%2f"),
        head + "/" + tail.replace(tail[:1], "%" + format(ord(tail[0]), "02x"), 1)
        if tail
        else p,
        p.replace("/", "\\", 2),
        f"http://{H}{p}",
        f"http://evil.example{p}",
    }
    return sorted(out)


def send(method, target, headers, body=b""):
    lines = [
        f"{method} {target} HTTP/1.1",
        *[f"{k}: {v}" for k, v in headers.items()],
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    with socket.create_connection(("127.0.0.1", PORT), timeout=20) as s:
        s.sendall(raw)
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    line = data.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split()
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0


results, violations = [], []
paths = sorted({concrete(p) for _, p in routes})
for p in paths:
    for target in spellings(p):
        for method in ["GET", "POST", "PATCH", "PUT", "DELETE"]:
            st = send(
                method, target, {"Host": H, "Content-Type": "application/json"}, b"{}"
            )
            results.append(
                {"auth": "none", "method": method, "target": target, "status": st}
            )
            # /health/live and /health/ready are public by design (no data, ARCHITECTURE.md section 5).
            if 200 <= st < 300 and not p.startswith("/health/"):
                violations.append(results[-1])

writes = sorted(
    {(m, concrete(p)) for m, p in routes if m in ("POST", "PATCH", "PUT", "DELETE")}
)
auth = {
    "Host": H,
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}
for m, p in writes:
    for target in spellings(p):
        st = send(m, target, auth, b"{}")
        results.append({"auth": "token", "method": m, "target": target, "status": st})
        if 200 <= st < 300:
            violations.append(results[-1])

st = send("GET", "/api/v1/projects", {**auth, "Host": "evil.example"})
results.append(
    {
        "auth": "token",
        "method": "GET",
        "target": "/api/v1/projects",
        "host": "evil.example",
        "status": st,
    }
)

by = {}
for r in results:
    by.setdefault(r["auth"], {}).setdefault(str(r["status"]), 0)
    by[r["auth"]][str(r["status"])] += 1
summary = {
    "routes": len(routes),
    "concrete_paths": len(paths),
    "write_routes": len(writes),
    "requests": len(results),
    "status_counts": by,
    "foreign_host_with_token": st,
    "violations": violations,
}
Path(OUT).write_text(
    json.dumps({"summary": summary, "routes": routes, "results": results}, indent=1)
)
print(json.dumps(summary, indent=1))
sys.exit(1 if violations else 0)
