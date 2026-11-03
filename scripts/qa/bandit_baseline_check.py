"""Fail-closed Bandit gate with a reviewed, per-finding baseline (R104-2).

The workflow runs Bandit 1.9.4 once over the approved scope and stores the raw JSON report
(`-f json -o`), its stderr and its exit status. This script only reads those files and the
checked-out sources; it runs nothing and does not use Bandit's own `-b` baseline, which in
1.9.4 matches issues by file, test and text only (a second issue of an already-baselined kind
in the same file is hidden), treats an unreadable baseline as empty after a warning, and
exits 0 when a file could not be parsed (docs/reports/r104.md, R104-2).

Every finding is identified by a fingerprint independent of line numbers:
  fingerprint      = sha256("<path>|<test_id>|<offending source line, stripped>")
  statement_sha256 = sha256 of the stripped lines of the finding's line range
  text_sha256      = sha256 of Bandit's issue text
plus an occurrence ordinal for identical fingerprints. The gate passes only if the full raw
finding set equals the baseline exactly (no new, missing or changed finding at any severity),
every baseline entry has a known classification and a matching row in the security document's
register, the report covers exactly the Python files under the approved targets, and Bandit
ran cleanly (exit 0/1 consistent with the findings, no stderr, no skipped file, no `nosec`,
no `.bandit` configuration inside a target).

Only a sanitized summary (paths, lines, test ids, severities, classifications, fingerprint
prefixes, verdict; never issue text or source code) is written to --out-dir. Exit 1 unless PASS.

Usage:
  python -I scripts/qa/bandit_baseline_check.py validate-baseline \
      --baseline security/bandit-baseline.json --security-doc docs/SECURITY.md
  python -I scripts/qa/bandit_baseline_check.py evaluate --report R --exit-file E \
      --stderr-file S --baseline security/bandit-baseline.json --security-doc docs/SECURITY.md \
      --root . --target backend/app --target sdk/src --target demo_app --target scripts --out-dir O
  python -I scripts/qa/bandit_baseline_check.py candidates --report R --root . --target ...
      (local review aid: unclassified entries for a validated report; never used in CI)
  python -I scripts/qa/bandit_baseline_check.py register --baseline B   (register table rows)
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SCHEMA = "llm-insight/bandit-baseline/v1"
TOOL = {"name": "bandit", "version": "1.9.4"}
LEVELS = ("HIGH", "MEDIUM", "LOW")
SUFFIXES = (".py", ".pyw")  # Bandit's default include globs
REPORT_KEYS = {"errors", "generated_at", "metrics", "results"}
RESULT_KEYS = {
    "code",
    "col_offset",
    "end_col_offset",
    "filename",
    "issue_confidence",
    "issue_cwe",
    "issue_severity",
    "issue_text",
    "line_number",
    "line_range",
    "more_info",
    "test_id",
    "test_name",
}
METRIC_KEYS = {
    *(
        f"{kind}.{level}"
        for kind in ("CONFIDENCE", "SEVERITY")
        for level in (*LEVELS, "UNDEFINED")
    ),
    "loc",
    "nosec",
    "skipped_tests",
}
PARTIAL_METRIC_KEYS = {"loc", "nosec", "skipped_tests"}
BASELINE_KEYS = {"schema", "tool", "scope", "generated", "review", "totals", "findings"}
ENTRY_KEYS = {
    "id",
    "path",
    "line",
    "test_id",
    "test_name",
    "severity",
    "confidence",
    "cwe",
    "fingerprint",
    "statement_sha256",
    "text_sha256",
    "occurrence",
    "classification",
    "origin",
}
COMPARED = (
    "test_name",
    "severity",
    "confidence",
    "cwe",
    "statement_sha256",
    "text_sha256",
)
ORIGINS = {"phase-5b", "phase-6a", "r104-1"}
# Closed set: a baseline cannot invent a class. (test ids, severities, required path prefix)
CLASSIFICATIONS = {
    "F5": ({"B202"}, {"HIGH"}, ""),
    "F6": ({"B310"}, {"MEDIUM"}, ""),
    "F7": ({"B608"}, {"MEDIUM"}, ""),
    "F8": ({"B101", "B105", "B404", "B603", "B607"}, {"LOW"}, ""),
    "R104-A": ({"B106"}, {"LOW"}, "scripts/qa/tests/"),
}
ENTRY_ID = re.compile(r"^BL-\d{2,3}$")
TEST_ID = re.compile(r"^B\d{3}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REGISTER_BEGIN = "<!-- bandit-baseline:begin -->"
REGISTER_END = "<!-- bandit-baseline:end -->"
REGISTER_ROW = re.compile(
    r"^\| (BL-\d{2,3}) \| `([^`]+)` \| (B\d{3}) \| (HIGH|MEDIUM|LOW) / (HIGH|MEDIUM|LOW) "
    r"\| `([0-9a-f]{12})` \| ([A-Z0-9-]+) \|$"
)
STDERR_LIMIT = 64 * 1024


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm_path(name):
    """Repository-relative POSIX path, or None for an absolute or escaping one."""
    if not isinstance(name, str):
        return None
    p = name.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
        return None
    return p


def problem(kind, detail, **where):
    return {"kind": kind, "detail": detail, **where}


# --- baseline -------------------------------------------------------------------------------


def load_json(path, missing_kind, malformed_kind, label):
    p = Path(path)
    if not p.is_file():
        return None, [problem(missing_kind, f"{label} not found: {path}")]
    try:
        return json.loads(p.read_text(encoding="utf-8")), []
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, [problem(malformed_kind, f"{label} is not valid JSON: {e.msg}")]


def validate_baseline(data):
    """Strict schema check; returns a list of problems (empty when valid)."""
    bad = []

    def err(detail, **where):
        bad.append(problem("BASELINE_INVALID", detail, **where))

    if not isinstance(data, dict) or set(data) != BASELINE_KEYS:
        keys = sorted(data) if isinstance(data, dict) else type(data).__name__
        err(f"top-level keys must be exactly {sorted(BASELINE_KEYS)}, got {keys}")
        return bad
    if data["schema"] != SCHEMA:
        err(f"schema must be {SCHEMA!r}")
    if data["tool"] != TOOL:
        err(f"tool must be {TOOL}")
    scope = data["scope"]
    if (
        not isinstance(scope, dict)
        or set(scope) != {"targets", "command"}
        or not isinstance(scope["targets"], list)
        or not scope["targets"]
        or any(norm_path(t) != t for t in scope["targets"])
        or len(set(scope["targets"])) != len(scope["targets"])
        or not isinstance(scope["command"], str)
    ):
        err("scope must be {targets: [unique relative paths], command: str}")
    for key in ("generated", "review"):
        value = data[key]
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            err(f"{key} must be an object of strings")
    findings = data["findings"]
    if not isinstance(findings, list):
        err("findings must be a list")
        return bad
    ids, identities = set(), set()
    counts = dict.fromkeys(LEVELS, 0)
    for n, e in enumerate(findings):
        if not isinstance(e, dict) or set(e) != ENTRY_KEYS:
            err(f"entry {n}: keys must be exactly {sorted(ENTRY_KEYS)}")
            continue
        where = {"id": e["id"]}
        if not isinstance(e["id"], str) or not ENTRY_ID.match(e["id"]):
            err(f"entry {n}: bad id", **where)
        elif e["id"] in ids:
            bad.append(
                problem("BASELINE_DUPLICATE", f"duplicate id {e['id']}", **where)
            )
        ids.add(e["id"])
        if norm_path(e["path"]) != e["path"]:
            err("path must be a normalized relative path", **where)
        if not isinstance(e["test_id"], str) or not TEST_ID.match(e["test_id"]):
            err("bad test_id", **where)
        if not isinstance(e["test_name"], str) or not e["test_name"]:
            err("bad test_name", **where)
        if e["severity"] not in LEVELS or e["confidence"] not in LEVELS:
            err("severity and confidence must be HIGH, MEDIUM or LOW", **where)
        else:
            counts[e["severity"]] += 1
        for key in ("line", "occurrence", "cwe"):
            if type(e[key]) is not int or e[key] < (0 if key == "cwe" else 1):
                err(f"{key} must be a positive integer", **where)
        for key in ("fingerprint", "statement_sha256", "text_sha256"):
            if not isinstance(e[key], str) or not SHA256.match(e[key]):
                err(f"{key} must be a lowercase SHA-256 hex digest", **where)
        if e["origin"] not in ORIGINS:
            err(f"unknown origin {e['origin']!r}", **where)
        cls = CLASSIFICATIONS.get(e["classification"])
        if cls is None:
            bad.append(
                problem(
                    "UNKNOWN_CLASSIFICATION",
                    f"classification {e['classification']!r} is not one of "
                    f"{sorted(CLASSIFICATIONS)}",
                    **where,
                )
            )
        elif (
            e["test_id"] not in cls[0]
            or e["severity"] not in cls[1]
            or not str(e["path"]).startswith(cls[2])
        ):
            bad.append(
                problem(
                    "UNKNOWN_CLASSIFICATION",
                    f"classification {e['classification']} does not cover "
                    f"{e['test_id']} {e['severity']} in {e['path']}",
                    **where,
                )
            )
        identity = (e["fingerprint"], e["occurrence"])
        if identity in identities:
            bad.append(
                problem(
                    "BASELINE_DUPLICATE",
                    f"duplicate fingerprint/occurrence {str(e['fingerprint'])[:12]}",
                    **where,
                )
            )
        identities.add(identity)
    totals = data["totals"]
    expected_totals = {**counts, "total": sum(counts.values())}
    if totals != expected_totals:
        err(f"totals {totals} do not match the entries {expected_totals}")
    return bad


def read_register(doc_path):
    """Rows of the security document's Bandit register, or (None, problems)."""
    p = Path(doc_path)
    if not p.is_file():
        return None, [
            problem("REGISTER_MISSING", f"security document not found: {doc_path}")
        ]
    text = p.read_text(encoding="utf-8")
    if text.count(REGISTER_BEGIN) != 1 or text.count(REGISTER_END) != 1:
        return None, [
            problem("REGISTER_MISSING", "register markers missing or repeated")
        ]
    body = text.split(REGISTER_BEGIN, 1)[1].split(REGISTER_END, 1)[0]
    rows, bad = [], []
    for line in body.strip().splitlines():
        if line.startswith("| ID ") or re.match(r"^\|(?:\s*-+\s*\|)+$", line):
            continue
        m = REGISTER_ROW.match(line)
        if not m:
            bad.append(
                problem("REGISTER_MALFORMED", f"unparsable register row: {line[:80]!r}")
            )
            continue
        rows.append(m.groups())
    return rows, bad


def register_rows(findings):
    return [
        (
            e["id"],
            e["path"],
            e["test_id"],
            e["severity"],
            e["confidence"],
            e["fingerprint"][:12],
            e["classification"],
        )
        for e in findings
    ]


def check_register(baseline, doc_path):
    rows, bad = read_register(doc_path)
    if rows is None:
        return bad
    documented, recorded = set(rows), set(register_rows(baseline["findings"]))
    if len(rows) != len(documented):
        bad.append(problem("REGISTER_MISMATCH", "duplicate register rows"))
    for row in sorted(recorded - documented):
        bad.append(
            problem(
                "REGISTER_MISMATCH", "baseline entry not in the register", id=row[0]
            )
        )
    for row in sorted(documented - recorded):
        bad.append(
            problem("REGISTER_MISMATCH", "register row not in the baseline", id=row[0])
        )
    return bad


def load_baseline(baseline_path, doc_path):
    data, bad = load_json(
        baseline_path, "BASELINE_MISSING", "BASELINE_MALFORMED", "baseline"
    )
    if data is None:
        return None, bad
    bad = validate_baseline(data)
    if bad:
        return None, bad
    return data, check_register(data, doc_path)


# --- report ---------------------------------------------------------------------------------


def validate_report(report):
    bad = []
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        return [
            problem(
                "MALFORMED_REPORT", f"report keys must be exactly {sorted(REPORT_KEYS)}"
            )
        ]
    if not isinstance(report["generated_at"], str):
        bad.append(problem("MALFORMED_REPORT", "generated_at must be a string"))
    if not isinstance(report["errors"], list) or not all(
        isinstance(x, dict) and set(x) == {"filename", "reason"}
        for x in report["errors"]
    ):
        bad.append(
            problem("MALFORMED_REPORT", "errors must be a list of {filename, reason}")
        )
        return bad
    # A file Bandit could not parse keeps only partial metrics; it is reported as SKIPPED_FILE.
    unparsed = {x["filename"] for x in report["errors"]}
    metrics = report["metrics"]
    if not isinstance(metrics, dict) or "_totals" not in metrics:
        bad.append(problem("MALFORMED_REPORT", "metrics must contain _totals"))
    else:
        for name, m in metrics.items():
            if not isinstance(m, dict) or (
                set(m) != METRIC_KEYS
                and not (name in unparsed and set(m) == PARTIAL_METRIC_KEYS)
            ):
                bad.append(
                    problem("MALFORMED_REPORT", "unexpected metrics shape", path=name)
                )
            elif not all(type(v) is int and v >= 0 for v in m.values()):
                bad.append(
                    problem("MALFORMED_REPORT", "metrics must be counts", path=name)
                )
            elif name != "_totals" and norm_path(name) is None:
                bad.append(
                    problem("MALFORMED_REPORT", "metrics path not relative", path=name)
                )
    results = report["results"]
    if not isinstance(results, list):
        return [*bad, problem("MALFORMED_REPORT", "results must be a list")]
    for n, r in enumerate(results):
        if not isinstance(r, dict) or set(r) != RESULT_KEYS:
            bad.append(problem("MALFORMED_REPORT", f"result {n}: unexpected keys"))
            continue
        lr = r["line_range"]
        if (
            norm_path(r["filename"]) is None
            or not isinstance(r["test_id"], str)
            or not TEST_ID.match(r["test_id"])
            or not isinstance(r["test_name"], str)
            or r["issue_severity"] not in LEVELS
            or r["issue_confidence"] not in LEVELS
            or not isinstance(r["issue_text"], str)
            or not isinstance(r["issue_cwe"], dict)
            or type(r["issue_cwe"].get("id")) is not int
            or type(r["line_number"]) is not int
            or not isinstance(lr, list)
            or not lr
            or not all(type(x) is int and x >= 1 for x in lr)
            or lr != list(range(lr[0], lr[0] + len(lr)))
        ):
            bad.append(
                problem(
                    "MALFORMED_REPORT", f"result {n}: invalid field types or values"
                )
            )
    return bad


def check_totals(report):
    """The metrics must agree with the findings; nosec and skipped tests are refused."""
    bad = []
    metrics = report["metrics"]
    totals = metrics["_totals"]
    for kind, field in (
        ("SEVERITY", "issue_severity"),
        ("CONFIDENCE", "issue_confidence"),
    ):
        for level in LEVELS:
            n = sum(1 for r in report["results"] if r[field] == level)
            if totals[f"{kind}.{level}"] != n:
                bad.append(
                    problem("MALFORMED_REPORT", f"_totals {kind}.{level} != findings")
                )
    for key in METRIC_KEYS:
        if (
            sum(m.get(key, 0) for name, m in metrics.items() if name != "_totals")
            != totals[key]
        ):
            bad.append(
                problem("MALFORMED_REPORT", f"per-file metrics do not add up to {key}")
            )
    if totals["nosec"] or totals["skipped_tests"]:
        files = sorted(
            name
            for name, m in metrics.items()
            if name != "_totals" and (m["nosec"] or m["skipped_tests"])
        )
        bad.append(
            problem(
                "NOSEC",
                f"{totals['nosec']} nosec / {totals['skipped_tests']} skipped tests "
                f"(suppressions are not allowed): {', '.join(files)}",
            )
        )
    return bad


def expected_files(root, targets):
    """Python files Bandit -r must scan under the targets (independent walk), or problems."""
    files, bad = set(), []
    for target in targets:
        base = Path(root) / target
        if not base.is_dir():
            bad.append(
                problem("TARGET_MISSING", f"scan target is not a directory: {target}")
            )
            continue
        for dirpath, _, names in os.walk(base):
            for name in names:
                rel = Path(dirpath, name).relative_to(root).as_posix()
                if name == ".bandit":
                    bad.append(
                        problem(
                            "CONFIG_FILE",
                            "a .bandit file inside a target alters the scan",
                            path=rel,
                        )
                    )
                elif name.endswith(SUFFIXES):
                    files.add(rel)
    return files, bad


def source_lines(root, path, cache):
    if path not in cache:
        try:
            cache[path] = (Path(root) / path).read_bytes().decode("utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            cache[path] = None
    return cache[path]


def fingerprints(report, root):
    """Current findings with fingerprints and occurrence ordinals, or (None, problems)."""
    cache, found, bad = {}, [], []
    for r in report["results"]:
        path = norm_path(r["filename"])
        lines = source_lines(root, path, cache)
        rng = r["line_range"]
        if (
            lines is None
            or not 1 <= r["line_number"] <= len(lines)
            or rng[-1] > len(lines)
            or r["line_number"] not in rng
        ):
            bad.append(
                problem(
                    "MALFORMED_REPORT",
                    "finding does not match the checked-out source",
                    path=path,
                    line=r["line_number"],
                )
            )
            continue
        found.append(
            {
                "path": path,
                "line": r["line_number"],
                "col": r["col_offset"] if type(r["col_offset"]) is int else 0,
                "test_id": r["test_id"],
                "test_name": r["test_name"],
                "severity": r["issue_severity"],
                "confidence": r["issue_confidence"],
                "cwe": r["issue_cwe"]["id"],
                "fingerprint": sha256(
                    f"{path}|{r['test_id']}|{lines[r['line_number'] - 1].strip()}"
                ),
                "statement_sha256": sha256(
                    "\n".join(lines[i - 1].strip() for i in range(rng[0], rng[-1] + 1))
                ),
                "text_sha256": sha256(r["issue_text"]),
            }
        )
    if bad:
        return None, bad
    found.sort(key=lambda f: (f["path"], f["line"], f["col"], f["test_id"]))
    seen = {}
    for f in found:
        seen[f["fingerprint"]] = seen.get(f["fingerprint"], 0) + 1
        f["occurrence"] = seen[f["fingerprint"]]
    return found, []


def read_report(report_path, root, targets):
    """Validated report plus fingerprinted findings and scope problems."""
    p = Path(report_path)
    if not p.is_file() or not p.read_bytes().strip():
        return None, None, [problem("REPORT_MISSING", "Bandit wrote no report")]
    try:
        report = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return (
            None,
            None,
            [problem("MALFORMED_REPORT", f"report is not valid JSON: {e.msg}")],
        )
    bad = validate_report(report)
    if bad:
        return None, None, bad
    bad = check_totals(report)
    for e in report["errors"]:
        bad.append(
            problem(
                "SKIPPED_FILE",
                f"Bandit could not scan the file: {str(e['reason'])[:120]}",
                path=norm_path(e["filename"]) or "?",
            )
        )
    expected, scope_bad = expected_files(root, targets)
    bad += scope_bad
    scanned = {norm_path(name) for name in report["metrics"] if name != "_totals"}
    for path in sorted(expected - scanned):
        bad.append(
            problem(
                "UNSCANNED_FILE",
                "Python file under a target was not scanned",
                path=path,
            )
        )
    for path in sorted(scanned - expected):
        bad.append(
            problem(
                "UNEXPECTED_SCOPE",
                "scanned file outside the approved targets",
                path=path,
            )
        )
    for r in report["results"]:
        if norm_path(r["filename"]) not in scanned:
            bad.append(problem("MALFORMED_REPORT", "finding in a file without metrics"))
    found, fp_bad = fingerprints(report, root)
    return report, found, bad + fp_bad


# --- comparison -----------------------------------------------------------------------------


def compare(found, entries):
    """Rows for the summary and problems; exact multiset equality on (fingerprint, occurrence)."""
    by_identity = {(e["fingerprint"], e["occurrence"]): e for e in entries}
    rows, bad, matched = [], [], set()
    for f in found:
        key = (f["fingerprint"], f["occurrence"])
        e = by_identity.get(key)
        row = {
            "id": e["id"] if e else None,
            "status": "ACCEPTED",
            "path": f["path"],
            "line": f["line"],
            "test_id": f["test_id"],
            "severity": f["severity"],
            "confidence": f["confidence"],
            "classification": e["classification"] if e else None,
            "fingerprint": f["fingerprint"][:12],
        }
        where = {"path": f["path"], "line": f["line"], "test_id": f["test_id"]}
        if e is None:
            row["status"] = "NEW"
            bad.append(
                problem(
                    "NEW_FINDING",
                    f"{f['severity']}/{f['confidence']} {f['test_id']} {f['test_name']} "
                    f"is not in the baseline",
                    **where,
                )
            )
        else:
            matched.add(key)
            diffs = [k for k in COMPARED if f[k] != e[k]]
            if diffs:
                row["status"] = "CHANGED"
                detail = ", ".join(
                    f"{k} {e[k]}->{f[k]}"
                    if k in ("severity", "confidence", "cwe", "test_name")
                    else k
                    for k in diffs
                )
                bad.append(
                    problem("CHANGED_FINDING", f"{e['id']} changed: {detail}", **where)
                )
            elif f["line"] != e["line"]:
                row["baseline_line"] = e["line"]
        rows.append(row)
    for key, e in by_identity.items():
        if key not in matched:
            rows.append(
                {
                    "id": e["id"],
                    "status": "MISSING",
                    "path": e["path"],
                    "line": None,
                    "baseline_line": e["line"],
                    "test_id": e["test_id"],
                    "severity": e["severity"],
                    "confidence": e["confidence"],
                    "classification": e["classification"],
                    "fingerprint": e["fingerprint"][:12],
                }
            )
            bad.append(
                problem(
                    "MISSING_FINDING",
                    f"{e['id']} ({e['test_id']}) no longer reported: remove it from the baseline "
                    f"and the register in a reviewed change",
                    path=e["path"],
                    test_id=e["test_id"],
                )
            )
    return rows, bad


def check_run(exit_text, stderr_text, report):
    bad = []
    if exit_text is None or not exit_text.strip().lstrip("-").isdigit():
        return [problem("TOOL_FAILURE", "Bandit exit status not recorded")], None
    code = int(exit_text.strip())
    if code == 124:
        bad.append(problem("TOOL_FAILURE", "Bandit timed out (exit 124)"))
    elif code not in (0, 1):
        bad.append(problem("TOOL_FAILURE", f"Bandit failed (exit {code})"))
    elif report is not None and (code == 1) != bool(report["results"]):
        bad.append(
            problem(
                "TOOL_FAILURE",
                f"exit {code} is inconsistent with {len(report['results'])} reported findings",
            )
        )
    if stderr_text is None:
        bad.append(problem("TOOL_FAILURE", "Bandit stderr not recorded"))
    elif stderr_text.strip():
        bad.append(
            problem("TOOL_FAILURE", "Bandit wrote to stderr (warnings or errors)")
        )
    return bad, code


def read_text(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def evaluate(args):
    targets = list(args.target)
    baseline, bad = load_baseline(args.baseline, args.security_doc)
    if baseline is not None and sorted(baseline["scope"]["targets"]) != sorted(targets):
        bad.append(
            problem(
                "UNEXPECTED_SCOPE",
                f"targets {sorted(targets)} differ from the baseline scope "
                f"{sorted(baseline['scope']['targets'])}",
            )
        )
    stderr_text = read_text(args.stderr_file)
    report, found, report_bad = read_report(args.report, args.root, targets)
    run_bad, code = check_run(read_text(args.exit_file), stderr_text, report)
    bad += run_bad + report_bad
    rows = []
    if baseline is not None and found is not None:
        rows, cmp_bad = compare(found, baseline["findings"])
        bad += cmp_bad
    summary = {
        "schema": "llm-insight/bandit-gate-summary/v1",
        "verdict": "FAIL" if bad else "PASS",
        "tool": f"{TOOL['name']} {TOOL['version']}",
        "targets": targets,
        "bandit_exit": code,
        "files_scanned": (len(report["metrics"]) - 1) if report else None,
        "loc": report["metrics"]["_totals"]["loc"] if report else None,
        "counts": (
            {level: sum(1 for f in found if f["severity"] == level) for level in LEVELS}
            if found is not None
            else None
        ),
        "baseline_entries": len(baseline["findings"]) if baseline else None,
        "problems": bad,
        "findings": rows,
    }
    if stderr_text and stderr_text.strip():
        print("::group::Bandit stderr (truncated)")
        print(stderr_text[:STDERR_LIMIT])
        print("::endgroup::")
    write_summary(summary, args.out_dir)
    return summary


def markdown(summary):
    counts = summary["counts"] or {}
    headline = (
        f"**Verdict: {summary['verdict']}** — {summary['tool']}, "
        f"exit {summary['bandit_exit']}, {summary['files_scanned']} files, "
        f"{summary['loc']} lines; findings High {counts.get('HIGH')} / "
        f"Medium {counts.get('MEDIUM')} / Low {counts.get('LOW')}; "
        f"baseline entries {summary['baseline_entries']}."
    )
    out = ["## Bandit security gate (R104-2)", "", headline, ""]
    if summary["problems"]:
        out += ["| Problem | Path | Line | Test | Detail |", "|---|---|---|---|---|"]
        for p in summary["problems"]:
            out.append(
                f"| {p['kind']} | {p.get('path', '')} | {p.get('line', '')} | "
                f"{p.get('test_id', '')} | {p['detail'].replace('|', '/')} |"
            )
        out.append("")
    out += [
        "| ID | Status | Path | Line | Test | Severity / confidence | Class | Fingerprint |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in summary["findings"]:
        out.append(
            f"| {r['id'] or ''} | {r['status']} | {r['path']} | {r['line'] or ''} | "
            f"{r['test_id']} | {r['severity']} / {r['confidence']} | "
            f"{r['classification'] or ''} | {r['fingerprint']} |"
        )
    return "\n".join(out) + "\n"


def write_summary(summary, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    md = markdown(summary)
    (out / "summary.md").write_text(md, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(md)


def report_problems(bad):
    for p in bad:
        where = " ".join(
            f"{k}={p[k]}" for k in ("id", "path", "line", "test_id") if p.get(k)
        )
        print(
            f"::error::bandit {p['kind']}: {p['detail']}"
            + (f" ({where})" if where else "")
        )


# --- review aids (local only) ---------------------------------------------------------------


def new_entries(found):
    """Unclassified baseline entries (classification and origin must be set by a reviewer)."""
    return [
        {
            "id": f"BL-{n:02d}",
            "path": f["path"],
            "line": f["line"],
            "test_id": f["test_id"],
            "test_name": f["test_name"],
            "severity": f["severity"],
            "confidence": f["confidence"],
            "cwe": f["cwe"],
            "fingerprint": f["fingerprint"],
            "statement_sha256": f["statement_sha256"],
            "text_sha256": f["text_sha256"],
            "occurrence": f["occurrence"],
            "classification": None,
            "origin": None,
        }
        for n, f in enumerate(found, 1)
    ]


def render_register(findings):
    out = [
        "| ID | File | Test | Severity / confidence | Fingerprint | Class |",
        "|---|---|---|---|---|---|",
    ]
    for row in register_rows(findings):
        out.append(
            f"| {row[0]} | `{row[1]}` | {row[2]} | {row[3]} / {row[4]} | `{row[5]}` | {row[6]} |"
        )
    return "\n".join(out) + "\n"


def candidates(args):
    report, found, bad = read_report(args.report, args.root, list(args.target))
    run_bad, _ = check_run(
        read_text(args.exit_file), read_text(args.stderr_file), report
    )
    bad += run_bad
    if bad:
        report_problems(bad)
        return 1
    print(json.dumps(new_entries(found), indent=2))
    return 0


def register(args):
    data, bad = load_json(
        args.baseline, "BASELINE_MISSING", "BASELINE_MALFORMED", "baseline"
    )
    bad = bad or validate_baseline(data)
    if bad:
        report_problems(bad)
        return 1
    print(render_register(data["findings"]), end="")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser(
        "validate-baseline", help="schema, classification and register check"
    )
    v.add_argument("--baseline", required=True)
    v.add_argument("--security-doc", required=True)
    e = sub.add_parser(
        "evaluate", help="evaluate a recorded Bandit run against the baseline"
    )
    c = sub.add_parser(
        "candidates", help="unclassified baseline entries for a clean report"
    )
    for p in (e, c):
        p.add_argument("--report", required=True)
        p.add_argument("--exit-file", required=True)
        p.add_argument("--stderr-file", required=True)
        p.add_argument("--root", default=".")
        p.add_argument("--target", action="append", required=True)
    e.add_argument("--baseline", required=True)
    e.add_argument("--security-doc", required=True)
    e.add_argument("--out-dir", required=True)
    r = sub.add_parser("register", help="print the register rows of a valid baseline")
    r.add_argument("--baseline", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "validate-baseline":
        data, bad = load_baseline(args.baseline, args.security_doc)
        report_problems(bad)
        if bad:
            return 1
        print(
            f"baseline valid: {len(data['findings'])} entries, totals {data['totals']}"
        )
        return 0
    if args.cmd == "candidates":
        return candidates(args)
    if args.cmd == "register":
        return register(args)
    summary = evaluate(args)
    report_problems(summary["problems"])
    counts = summary["counts"]
    print(
        f"bandit gate {summary['verdict']}: exit {summary['bandit_exit']}, "
        f"{len(summary['problems'])} problem(s), findings {counts}, "
        f"baseline entries {summary['baseline_entries']}"
    )
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
