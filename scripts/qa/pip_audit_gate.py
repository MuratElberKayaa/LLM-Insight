"""Fail-closed gate for the CI Python dependency audits (R104-1).

The workflow runs pip-audit once per (lockfile, vulnerability service) and stores, per run id
`<target>-<service>`: `<id>.json` (pip-audit `--format json -o`), `<id>.stderr.txt` and
`<id>.exit` (exit status). This script only reads those files; it runs nothing.

Every run must be PASS: a JSON report of the expected shape that lists exactly the lockfile's
pins, none skipped, no vulnerability, and exit status 0. Anything else fails, classified as
VULNERABLE, SERVICE_UNAVAILABLE, INVALID_LOCKFILE, TIMEOUT, NOT_AUDITED, MALFORMED_REPORT or
TOOL_FAILURE. pip-audit exits 1 for findings and for errors alike, so the class comes from the
report and stderr, never from the exit status alone. A network error is never a clean audit.

Validated reports (and stderr, truncated) are copied to --upload-dir with summary.json and
summary.md; a malformed report is not copied. Exit 1 unless every run is PASS.

Usage:
  python -I scripts/qa/pip_audit_gate.py evaluate --dir D --upload-dir U \
      --target backend=backend/requirements.lock --target sdk=sdk/requirements-dev.lock \
      --service pypi --service osv
  <venv>/bin/python -I scripts/qa/pip_audit_gate.py check-env --lock security/tools/requirements-audit.lock
"""

import argparse
import importlib.metadata
import json
import os
import re
import sys
from pathlib import Path

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
TOP_KEYS = {"dependencies", "fixes"}
DEP_KEYS = {"name", "version", "vulns", "skip_reason"}
VULN_KEYS = {"id", "fix_versions", "aliases", "description"}
INVALID_LOCKFILE = re.compile(
    r"ERROR:pip_audit\._cli:(?:requirement file .* contains invalid"
    r"|invalid requirements input)"
)
SERVICE_UNAVAILABLE = re.compile(
    r"requests\.exceptions\.|urllib3\.exceptions\.|ServiceError|ConnectionError"
    r"|Max retries exceeded|Temporary failure in name resolution|Read timed out"
)
STDERR_LIMIT = 64 * 1024


def canon(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(path):
    """`{name: version}` of a `name==version` lockfile; None if it is missing or not one."""
    p = Path(path)
    if not p.is_file():
        return None
    pins = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "--hash=")):
            continue
        m = PIN.match(s)
        if not m or canon(m.group(1)) in pins:
            return None
        pins[canon(m.group(1))] = m.group(2)
    return pins or None


def classify(exit_text, report_text, stderr_text, pins):
    """Return (status, detail, findings) for one pip-audit run."""
    if pins is None:
        return "INVALID_LOCKFILE", "lockfile missing or not a list of exact pins", []
    if exit_text is None or not exit_text.strip().lstrip("-").isdigit():
        return "TOOL_FAILURE", "exit status not recorded", []
    code = int(exit_text.strip())
    if code == 124:
        return "TIMEOUT", "pip-audit timed out", []
    if report_text is None or not report_text.strip():
        stderr = stderr_text or ""
        if INVALID_LOCKFILE.search(stderr):
            return "INVALID_LOCKFILE", "pip-audit rejected the requirements file", []
        if SERVICE_UNAVAILABLE.search(stderr):
            return "SERVICE_UNAVAILABLE", "vulnerability service unreachable", []
        return "TOOL_FAILURE", f"no report (exit {code})", []
    try:
        report = json.loads(report_text)
    except ValueError:
        return "MALFORMED_REPORT", "report is not JSON", []
    problem = shape_problem(report)
    if problem:
        return "MALFORMED_REPORT", problem, []
    deps = report["dependencies"]
    skipped = [d["name"] for d in deps if "skip_reason" in d]
    if skipped:
        return "NOT_AUDITED", f"skipped by pip-audit: {sorted(skipped)}", []
    got = {canon(d["name"]): d["version"] for d in deps}
    if len(got) != len(deps) or got != pins:
        missing = sorted(set(pins) - set(got))
        extra = sorted(set(got) - set(pins))
        changed = sorted(k for k in set(pins) & set(got) if pins[k] != got[k])
        detail = (
            f"report does not match the lockfile (missing={missing}, extra={extra}, "
            f"version differs={changed})"
        )
        return "MALFORMED_REPORT", detail, []
    findings = sorted(
        {
            (
                canon(d["name"]),
                d["version"],
                v["id"],
                tuple(sorted(v.get("aliases", []))),
            )
            for d in deps
            for v in d["vulns"]
        }
    )
    if findings:
        return "VULNERABLE", f"{len(findings)} advisory(ies)", findings
    if code != 0:
        return "TOOL_FAILURE", f"clean report but exit {code}", []
    return "PASS", f"{len(deps)} pinned packages, no known vulnerabilities", []


def shape_problem(report):
    """Allowlist of the report shape: package, version and advisory metadata only."""
    if not isinstance(report, dict) or set(report) != TOP_KEYS:
        return "unexpected top-level keys"
    if report["fixes"] != []:
        return "unexpected fixes section"
    if not isinstance(report["dependencies"], list):
        return "dependencies is not a list"
    for d in report["dependencies"]:
        if not isinstance(d, dict) or not set(d) <= DEP_KEYS or "name" not in d:
            return "unexpected dependency entry"
        if not isinstance(d["name"], str):
            return "dependency name is not a string"
        if "skip_reason" in d:
            continue
        if not isinstance(d.get("version"), str) or not isinstance(
            d.get("vulns"), list
        ):
            return "dependency without version or vulns"
        for v in d["vulns"]:
            if not isinstance(v, dict) or not set(v) <= VULN_KEYS or "id" not in v:
                return "unexpected vulnerability entry"
            if not isinstance(v["id"], str) or not isinstance(
                v.get("aliases", []), list
            ):
                return "unexpected vulnerability field type"
    return None


def read_text(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def evaluate(args):
    src, dst = Path(args.dir), Path(args.upload_dir)
    dst.mkdir(parents=True, exist_ok=True)
    targets = dict(t.split("=", 1) for t in args.target)
    results = []
    for target, lock in targets.items():
        pins = read_pins(lock)
        for service in args.service:
            run_id = f"{target}-{service}"
            report = read_text(src / f"{run_id}.json")
            stderr = read_text(src / f"{run_id}.stderr.txt")
            status, detail, findings = classify(
                read_text(src / f"{run_id}.exit"), report, stderr, pins
            )
            if status in ("PASS", "VULNERABLE", "NOT_AUDITED"):
                (dst / f"{run_id}.json").write_text(report, encoding="utf-8")
            if stderr is not None:
                (dst / f"{run_id}.stderr.txt").write_text(
                    stderr[:STDERR_LIMIT], encoding="utf-8"
                )
            results.append(
                {
                    "run": run_id,
                    "target": lock,
                    "service": service,
                    "status": status,
                    "detail": detail,
                    "vulnerabilities": len(findings),
                    "findings": [
                        {"package": n, "version": ver, "id": i, "aliases": list(a)}
                        for n, ver, i, a in findings
                    ],
                }
            )
    ok = bool(results) and all(r["status"] == "PASS" for r in results)
    summary = {"tool": "pip-audit", "gate": "PASS" if ok else "FAIL", "runs": results}
    (dst / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"## Python dependency audit: {summary['gate']}",
        "",
        "| Run | Target | Service | Status | Vulnerabilities | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['run']} | `{r['target']}` | {r['service']} | {r['status']} "
            f"| {r['vulnerabilities']} | {r['detail']} |"
        )
    for r in results:
        for f in r["findings"]:
            lines.append(
                f"- {r['run']}: {f['package']}=={f['version']} {f['id']} "
                f"{' '.join(f['aliases'])}".rstrip()
            )
    md = "\n".join(lines) + "\n"
    (dst / "summary.md").write_text(md, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md)
    print(md)
    for r in results:
        if r["status"] != "PASS":
            print(f"::error::pip-audit {r['run']}: {r['status']}: {r['detail']}")
    return 0 if ok else 1


def check_env(args):
    """The running interpreter's installed distributions equal the lock exactly."""
    pins = read_pins(args.lock)
    if pins is None:
        print(f"lock {args.lock} missing or invalid")
        return 1
    installed = {
        canon(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()
    }
    missing = sorted(set(pins) - set(installed))
    extra = sorted(set(installed) - set(pins))
    changed = sorted(k for k in set(pins) & set(installed) if pins[k] != installed[k])
    print(
        f"lock {len(pins)} pins; installed {len(installed)}; missing={missing}; "
        f"extra={extra}; version differs={changed}"
    )
    return 1 if missing or extra or changed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--dir", required=True)
    ev.add_argument("--upload-dir", required=True)
    ev.add_argument("--target", action="append", required=True, help="name=lockfile")
    ev.add_argument("--service", action="append", required=True)
    ce = sub.add_parser("check-env")
    ce.add_argument("--lock", required=True)
    args = ap.parse_args()
    return evaluate(args) if args.cmd == "evaluate" else check_env(args)


if __name__ == "__main__":
    sys.exit(main())
