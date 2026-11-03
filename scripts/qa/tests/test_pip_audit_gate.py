"""Offline tests of scripts/qa/pip_audit_gate.py (R104-1). Stdlib only, no network.

Run: python -I -m unittest discover -s scripts/qa/tests -v
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

QA = Path(__file__).resolve().parents[1]
REPO = QA.parents[1]
sys.path.insert(0, str(QA))

import pip_audit_gate as gate

PINS = {"alpha": "1.0", "beta-pkg": "2.0"}
# stderr excerpts recorded from pip-audit 2.10.1 on Linux (R104-1 probe, docs/reports/r104.md).
WARN = (
    "WARNING:pip_audit._cli:--no-deps is supported, but users are encouraged to fully hash "
    "their pinned dependencies\n"
)
OUTAGE = WARN + (
    "requests.exceptions.ProxyError: HTTPSConnectionPool(host='pypi.org', port=443): "
    "Max retries exceeded with url: /pypi/alembic/1.20.0/json\n"
)
INVALID = WARN + (
    "ERROR:pip_audit._cli:requirement file /work/probe/invalid.txt contains invalid "
    "specifier at line 1: Invalid requirement\n"
)
MISSING = WARN + "ERROR:pip_audit._cli:invalid requirements input: /x/missing.txt\n"


def report(deps=None, **extra):
    deps = (
        deps
        if deps is not None
        else [
            {"name": "alpha", "version": "1.0", "vulns": []},
            {"name": "beta_pkg", "version": "2.0", "vulns": []},
        ]
    )
    return json.dumps({"dependencies": deps, "fixes": [], **extra})


VULN = {
    "id": "PYSEC-2018-28",
    "fix_versions": ["2.20.0"],
    "aliases": ["CVE-2018-18074", "GHSA-x84v-xcm2-53pg"],
    "description": "advisory text",
}


class Classify(unittest.TestCase):
    def check(self, expected, exit_text, report_text, stderr="", pins=PINS):
        status, detail, findings = gate.classify(exit_text, report_text, stderr, pins)
        self.assertEqual(status, expected, detail)
        return findings

    def test_clean_report_passes(self):
        self.check("PASS", "0", report())

    def test_vulnerability_fails(self):
        deps = [
            {"name": "alpha", "version": "1.0", "vulns": [VULN, VULN]},
            {"name": "beta-pkg", "version": "2.0", "vulns": []},
        ]
        findings = self.check("VULNERABLE", "1", report(deps))
        self.assertEqual(len(findings), 1)  # duplicate advisory entries collapse
        self.assertEqual(findings[0][2], "PYSEC-2018-28")

    def test_vulnerability_with_exit_zero_still_fails(self):
        deps = [
            {"name": "alpha", "version": "1.0", "vulns": [VULN]},
            {"name": "beta-pkg", "version": "2.0", "vulns": []},
        ]
        self.check("VULNERABLE", "0", report(deps))

    def test_service_outage_is_not_clean(self):
        self.check("SERVICE_UNAVAILABLE", "1", None, OUTAGE)
        self.check("SERVICE_UNAVAILABLE", "1", "", OUTAGE)

    def test_invalid_or_missing_lockfile(self):
        self.check("INVALID_LOCKFILE", "1", None, INVALID)
        self.check("INVALID_LOCKFILE", "1", None, MISSING)
        self.check("INVALID_LOCKFILE", "0", report(), "", pins=None)

    def test_timeout(self):
        self.check("TIMEOUT", "124", None, WARN)

    def test_unrecorded_exit_status(self):
        self.check("TOOL_FAILURE", None, report())
        self.check("TOOL_FAILURE", "x", report())

    def test_unknown_error_without_report(self):
        self.check("TOOL_FAILURE", "2", None, WARN + "Traceback: KeyError\n")

    def test_clean_report_with_nonzero_exit(self):
        self.check("TOOL_FAILURE", "1", report())

    def test_malformed_reports(self):
        self.check("MALFORMED_REPORT", "0", "{not json")
        self.check("MALFORMED_REPORT", "0", report(extra_key=1))
        self.check(
            "MALFORMED_REPORT",
            "0",
            json.dumps({"dependencies": [], "fixes": [{"x": 1}]}),
        )
        deps = [{"name": "alpha", "version": "1.0", "vulns": [], "env": "x"}]
        self.check("MALFORMED_REPORT", "0", report(deps))
        deps = [
            {"name": "alpha", "version": "1.0", "vulns": [{**VULN, "payload": "x"}]}
        ]
        self.check("MALFORMED_REPORT", "0", report(deps))

    def test_skipped_dependency_is_not_audited(self):
        deps = [
            {"name": "alpha", "skip_reason": "Dependency not found on PyPI"},
            {"name": "beta-pkg", "version": "2.0", "vulns": []},
        ]
        self.check("NOT_AUDITED", "0", report(deps))

    def test_report_must_cover_the_lockfile_exactly(self):
        self.check(
            "MALFORMED_REPORT",
            "0",
            report([{"name": "alpha", "version": "1.0", "vulns": []}]),
        )
        deps = [
            {"name": "alpha", "version": "1.1", "vulns": []},
            {"name": "beta-pkg", "version": "2.0", "vulns": []},
        ]
        self.check("MALFORMED_REPORT", "0", report(deps))
        deps = [
            *json.loads(report())["dependencies"],
            {"name": "gamma", "version": "3", "vulns": []},
        ]
        self.check("MALFORMED_REPORT", "0", report(deps))
        deps = [
            *json.loads(report())["dependencies"],
            {"name": "alpha", "version": "1.0", "vulns": []},
        ]
        self.check("MALFORMED_REPORT", "0", report(deps))


class ReadPins(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "lock.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_plain_and_hashed_locks(self):
        self.assertEqual(
            gate.read_pins(self.write("# c\nAlpha==1.0\nbeta_pkg==2.0\n")), PINS
        )
        hashed = "alpha==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
        self.assertEqual(gate.read_pins(self.write(hashed)), {"alpha": "1.0"})

    def test_rejects_non_pins(self):
        for text in (
            "alpha>=1.0\n",
            "-e .\n",
            "alpha==1.0\nAlpha==1.1\n",
            "# only comments\n",
            "git+https://x/y\n",
        ):
            self.assertIsNone(gate.read_pins(self.write(text)), text)
        self.assertIsNone(gate.read_pins(Path(tempfile.mkdtemp()) / "absent.txt"))

    def test_repository_locks_parse(self):
        for rel in (
            "backend/requirements.lock",
            "sdk/requirements-dev.lock",
            "security/tools/requirements-audit.lock",
        ):
            pins = gate.read_pins(REPO / rel)
            self.assertTrue(pins, rel)
        tool = gate.read_pins(REPO / "security/tools/requirements-audit.lock")
        self.assertEqual(tool["pip-audit"], "2.10.1")
        self.assertEqual(tool["bandit"], "1.9.4")


class Evaluate(unittest.TestCase):
    def run_gate(self, files):
        src, dst = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / "upload"
        lock = src / "a.lock"
        lock.write_text("alpha==1.0\nbeta-pkg==2.0\n", encoding="utf-8")
        for name, text in files.items():
            (src / name).write_text(text, encoding="utf-8")
        argv = [
            "gate",
            "evaluate",
            "--dir",
            str(src),
            "--upload-dir",
            str(dst),
            "--target",
            f"a={lock}",
            "--service",
            "pypi",
            "--service",
            "osv",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""}),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            code = gate.main()
        return (
            code,
            json.loads((dst / "summary.json").read_text(encoding="utf-8")),
            dst,
            out.getvalue(),
        )

    def test_all_runs_pass(self):
        files = {
            f"a-{s}.{k}": v
            for s in ("pypi", "osv")
            for k, v in (("json", report()), ("exit", "0\n"), ("stderr.txt", WARN))
        }
        code, summary, dst, _ = self.run_gate(files)
        self.assertEqual((code, summary["gate"]), (0, "PASS"))
        self.assertTrue((dst / "a-osv.json").is_file())

    def test_one_missing_run_fails_the_gate(self):
        files = {"a-pypi.json": report(), "a-pypi.exit": "0\n"}
        code, summary, _, out = self.run_gate(files)
        self.assertEqual((code, summary["gate"]), (1, "FAIL"))
        self.assertEqual(
            [r["status"] for r in summary["runs"]], ["PASS", "TOOL_FAILURE"]
        )
        self.assertIn("::error::pip-audit a-osv: TOOL_FAILURE", out)

    def test_malformed_report_is_not_uploaded(self):
        files = {
            "a-pypi.json": report(secret="x"),
            "a-pypi.exit": "0\n",
            "a-osv.json": report(),
            "a-osv.exit": "0\n",
        }
        code, _, dst, _ = self.run_gate(files)
        self.assertEqual(code, 1)
        self.assertFalse((dst / "a-pypi.json").exists())
        self.assertTrue((dst / "a-osv.json").is_file())


if __name__ == "__main__":
    unittest.main()
