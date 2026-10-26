"""Raw-socket Server Action route probe (Phase 5B, Step 4; R68/R71 re-audit).

Sends POST requests carrying a real Server Action id (`Next-Action`) to many raw request
targets (no client-side normalization: the bytes go on the wire as written) under several
Host/Origin variants, against the production dashboard. A dispatched action answers with an
RSC body (`Content-Type: text/x-component`); the action is called with an empty argument list,
so even a dispatched `submitDataset` refuses the form and writes nothing.

Pass criteria:
  * no dispatch on any path that is not one of the four write pages;
  * no dispatch on a write page unless Host is loopback, Origin equals Host and
    Sec-Fetch-Site is same-origin (or absent);
  * a positive control (same-origin POST to /datasets) IS dispatched, so detection works.
Usage: python -I scripts/qa/route_probe.py <port> <action-id> <out.json>
"""

import json
import socket
import sys
from pathlib import Path

PORT = int(sys.argv[1])
ACTION = sys.argv[2]
OUT = sys.argv[3]
H = f"127.0.0.1:{PORT}"
TRACE = "0123456789abcdef0123456789abcdef"

WRITE_PAGES = {
    "/datasets",
    "/experiments/new",
    f"/traces/{TRACE}",
    f"/traces/{TRACE}/reviews/1/test-case",
}

PATHS = [
    # normal write pages (authorized UI entry points, ADR-27)
    "/datasets",
    "/experiments/new",
    f"/traces/{TRACE}",
    f"/traces/{TRACE}/reviews/1/test-case",
    # read-only pages and other app paths
    "/",
    "/traces",
    "/experiments",
    "/models",
    "/datasets/1",
    "/experiments/1",
    "/icon.svg",
    "/_not-found",
    "/no-such-page",
    "/favicon.ico",
    "/robots.txt",
    # static-asset look-alikes (R71)
    "/_next/static",
    "/_next/static/",
    "/_next/static/x",
    "/_next/staticx",
    "/_next/static-x",
    "/_next/static/chunks/does-not-exist.js",
    "/_next/static/../datasets",
    "/_next/static/..%2fdatasets",
    "/_next/static/%2e%2e/datasets",
    "/_next/static/%2e%2e%2fdatasets",
    "/_next/static/x/../../datasets",
    "/_next/static%2f..%2fdatasets",
    "/_next/static/..\\datasets",
    "/_next/static\\..\\datasets",
    "/_next//static/x",
    "//_next/static/x",
    "/_next/static/./x",
    "/_next/%73tatic/x",
    "/%5fnext/static/x",
    "/_NEXT/static/x",
    "/_next/Static/x",
    "/_next/static%2fx",
    "/_next/static;/x",
    "/_next/static/%00",
    # image optimizer look-alikes (R71)
    "/_next/image",
    "/_next/image/",
    "/_next/image/x",
    "/_next/imagex",
    "/_next/images",
    "/_next/image?url=%2Fdatasets&w=64&q=75",
    "/_next/image/../datasets",
    "/_next/image%2f..%2fdatasets",
    "/_next/image;x",
    "/_next/image.rsc",
    "/_next/image%3fx",
    "/_next/IMAGE",
    "/_next/%69mage",
    # other Next.js internal prefixes
    "/_next",
    "/_next/",
    "/_next/data/x/datasets.json",
    "/_next/webpack-hmr",
    "/__nextjs_original-stack-frame",
    "/_next/postponed/resume/datasets",
    # write-page normalization variants
    "/datasets/",
    "/datasets//",
    "//datasets",
    "/./datasets",
    "/x/../datasets",
    "/%64atasets",
    "/DATASETS",
    "/datasets.rsc",
    "/datasets?_rsc=1",
    "/datasets%2f",
    "/datasets;x",
    "/datasets%00",
    "/datasets/.",
    "/datasets/x/..",
    "/experiments/new/",
    "/experiments/%6eew",
    "/experiments/new.rsc",
    # absolute-form request targets
    f"http://{H}/_next/static/../datasets",
    f"http://{H}/favicon.ico",
    "http://evil.example/datasets",
]


def variants():
    so = {"Host": H, "Origin": f"http://{H}", "Sec-Fetch-Site": "same-origin"}
    return {
        "same-origin": so,
        "no-origin": {"Host": H},
        "cross-origin": {
            "Host": H,
            "Origin": "http://evil.example",
            "Sec-Fetch-Site": "cross-site",
        },
        "origin-null": {"Host": H, "Origin": "null"},
        "origin-lookalike": {"Host": H, "Origin": f"http://{H}.evil.example"},
        "origin-other-loopback-name": {"Host": H, "Origin": f"http://localhost:{PORT}"},
        "same-origin-but-sfs-cross-site": {**so, "Sec-Fetch-Site": "cross-site"},
        "foreign-host+matching-origin": {
            "Host": "evil.example",
            "Origin": "http://evil.example",
        },
        "host-lookalike": {
            "Host": f"127.0.0.1.evil.example:{PORT}",
            "Origin": f"http://127.0.0.1.evil.example:{PORT}",
        },
        "host-trailing-dot": {
            "Host": f"localhost.:{PORT}",
            "Origin": f"http://localhost.:{PORT}",
        },
        "host-userinfo": {
            "Host": f"127.0.0.1:{PORT}@evil.example",
            "Origin": f"http://{H}",
        },
        "host-ipv4-short": {"Host": f"127.1:{PORT}", "Origin": f"http://127.1:{PORT}"},
        "host-zero": {"Host": f"0.0.0.0:{PORT}", "Origin": f"http://0.0.0.0:{PORT}"},
        "host-upper-same-origin": {
            "Host": f"LOCALHOST:{PORT}",
            "Origin": f"http://localhost:{PORT}",
        },
        "duplicate-host": {"Host": [H, "evil.example"], "Origin": f"http://{H}"},
        "missing-host": {"Origin": f"http://{H}"},
        "x-forwarded-host": {
            **so,
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https",
        },
    }


def send(target, headers, method="POST", action=ACTION):
    body = b"[]"
    lines = [f"{method} {target} HTTP/1.1"]
    for k, v in headers.items():
        for one in v if isinstance(v, list) else [v]:
            lines.append(f"{k}: {one}")
    lines += [
        f"Next-Action: {action}",
        "Content-Type: text/plain;charset=UTF-8",
        "Accept: text/x-component",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    with socket.create_connection(("127.0.0.1", PORT), timeout=20) as s:
        s.sendall(raw)
        chunks = []
        while True:
            try:
                c = s.recv(65536)
            except TimeoutError:
                break
            if not c:
                break
            chunks.append(c)
    resp = b"".join(chunks)
    head = resp.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
    status_line = head.split("\r\n", 1)[0]
    status = (
        int(status_line.split()[1])
        if len(status_line.split()) > 1 and status_line.split()[1].isdigit()
        else 0
    )
    hdrs = {}
    for ln in head.split("\r\n")[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    ctype = hdrs.get("content-type", "")
    return {
        "status": status,
        "ctype": ctype,
        "dispatched": "text/x-component" in ctype,
        "location": hdrs.get("location"),
    }


# Same-origin loopback requests: Host compared case-insensitively; X-Forwarded-Host cannot be set
# by a cross-site browser page (it is a forbidden header for fetch only in the sense that a
# cross-origin request with it needs a CORS preflight the dashboard never answers).
SAME_ORIGIN_VARIANTS = {"same-origin", "host-upper-same-origin", "x-forwarded-host"}


def authorized(variant, target):
    return variant in SAME_ORIGIN_VARIANTS and target in WRITE_PAGES


results, violations = [], []
for target in PATHS:
    rows = {}
    for name, hdrs in variants().items():
        r = send(target, hdrs)
        r.update(target=target, variant=name)
        results.append(r)
        rows[name] = r
    # A non-canonical target that Next.js normalizes to a write page (`/./datasets`,
    # `/datasets.rsc`, `/_next/data/<id>/datasets.json`) is acceptable only if the proxy treated
    # it as that write page: it must refuse the same request without Origin (403) and with a
    # foreign Host (421). Duplicate Host headers: Node keeps the first one (loopback here).
    proxy_guarded = (
        rows["no-origin"]["status"] == 403
        and rows["foreign-host+matching-origin"]["status"] == 421
    )
    for name, r in rows.items():
        effective_same_origin = name in SAME_ORIGIN_VARIANTS | {"duplicate-host"}
        ok = authorized(name, target) or (effective_same_origin and proxy_guarded)
        if r["dispatched"] and not ok:
            violations.append(r)

# Method allowlist: other verbs (and a method-override header) never dispatch.
for method in ["PUT", "PATCH", "DELETE", "OPTIONS"]:
    for target in ["/datasets", "/favicon.ico", "/_next/static/x"]:
        r = send(target, variants()["same-origin"], method=method)
        r.update(target=target, variant=f"same-origin {method}")
        results.append(r)
        if r["dispatched"]:
            violations.append(r)
r = send(
    "/", {**variants()["same-origin"], "X-HTTP-Method-Override": "POST"}, method="GET"
)
r.update(target="/", variant="GET + X-HTTP-Method-Override: POST")
results.append(r)
violations += [r] if r["dispatched"] else []

control = next(
    x for x in results if x["target"] == "/datasets" and x["variant"] == "same-origin"
)
summary = {
    "requests": len(results),
    "paths": len(PATHS),
    "variants": len(variants()),
    "dispatched_total": sum(x["dispatched"] for x in results),
    "dispatched_authorized": sorted(
        {
            (x["target"], x["variant"])
            for x in results
            if x["dispatched"] and authorized(x["variant"], x["target"])
        }
    ),
    "positive_control_dispatched": control["dispatched"],
    "violations": violations,
}
Path(OUT).write_text(json.dumps({"summary": summary, "results": results}, indent=1))
print(json.dumps({k: v for k, v in summary.items()}, indent=1))
sys.exit(1 if violations or not control["dispatched"] else 0)
