"""Offline tests of scripts/qa/bandit_baseline_check.py (R104-2). Stdlib only, no network.

Every case builds a synthetic project in a temporary directory (never production code).
`Reports` feeds hand-made Bandit reports; `RealBandit` runs Bandit 1.9.4 in-process on the
synthetic project and is skipped when Bandit is not importable, unless
LLMI_TEST_REQUIRE_BANDIT=1 (CI), which turns a missing Bandit into a failure.

Run: python -I -m unittest discover -s scripts/qa/tests -v
     LLMI_TEST_REQUIRE_BANDIT=1 <audit-venv>/bin/python -I -m unittest discover \
         -s scripts/qa/tests -p test_bandit_baseline_check.py -v
"""

import contextlib
import copy
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

QA = Path(__file__).resolve().parents[1]
REPO = QA.parents[1]
sys.path.insert(0, str(QA))

import bandit_baseline_check as check

try:
    from bandit.cli import main as bandit_main
except ImportError:
    bandit_main = None

REQUIRE_BANDIT = os.environ.get("LLMI_TEST_REQUIRE_BANDIT") == "1"
TARGETS = ["app", "tools"]
SOURCES = {
    "app/net.py": (
        "import urllib.request\n\n\ndef fetch(url):\n    return urllib.request.urlopen(url, timeout=5)\n"
    ),
    "app/checks.py": "def check(value):\n    assert value > 0\n    return value\n",
    "tools/run.py": (
        "import subprocess\n\n\ndef run():\n"
        '    return subprocess.run(["git", "status"], check=False)\n'
    ),
    "tools/clean.py": "def add(a, b):\n    return a + b\n",
}
CLASSES = {"B310": "F6", "B101": "F8", "B404": "F8", "B603": "F8", "B607": "F8"}
EXPECTED_TESTS = ["B101", "B310", "B404", "B603", "B607"]
NEW_ASSERT = "def guard(flag):\n    assert flag\n    return flag\n"
NEW_HIGH = (
    "import hashlib\n\n\ndef digest(data):\n    return hashlib.md5(data).hexdigest()\n"
)
# B106 (hardcoded password function argument), the class removed in R104-2A.
NEW_B106 = "def connect(client):\n    return client.login(user='u', password='x')\n"
# (path, line, test_id, test_name, severity, confidence, cwe, text) of the synthetic sources.
SPEC = [
    (
        "app/checks.py",
        2,
        "B101",
        "assert_used",
        "LOW",
        "HIGH",
        703,
        "Use of assert detected.",
    ),
    ("app/net.py", 5, "B310", "blacklist", "MEDIUM", "HIGH", 22, "Audit url open."),
    ("tools/run.py", 1, "B404", "blacklist", "LOW", "HIGH", 78, "Consider subprocess."),
    (
        "tools/run.py",
        5,
        "B603",
        "subprocess_without_shell_equals_true",
        "LOW",
        "HIGH",
        78,
        "x",
    ),
    (
        "tools/run.py",
        5,
        "B607",
        "start_process_with_partial_path",
        "LOW",
        "HIGH",
        78,
        "y",
    ),
]
EMPTY_METRICS = dict.fromkeys(check.METRIC_KEYS, 0)


def result(path, line, test_id, test_name, severity, confidence, cwe, text):
    return {
        "code": f"{line} synthetic\n",
        "col_offset": 4,
        "end_col_offset": 10,
        "filename": path,
        "issue_confidence": confidence,
        "issue_cwe": {"id": cwe, "link": "https://cwe.mitre.org/"},
        "issue_severity": severity,
        "issue_text": text,
        "line_number": line,
        "line_range": [line],
        "more_info": "https://bandit.readthedocs.io/",
        "test_id": test_id,
        "test_name": test_name,
    }


class Project:
    """A synthetic project, its reviewed baseline and register, and one recorded run."""

    def __init__(self, base):
        self.root = Path(base) / "proj"
        self.raw = Path(base) / "raw"
        self.out = Path(base) / "out"
        self.raw.mkdir(parents=True)
        for rel, text in SOURCES.items():
            self.write(rel, text)
        self.baseline = self.root / "baseline.json"
        self.doc = self.root / "SECURITY.md"

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def record(self, report=None, exit_code=None, stderr=""):
        if report is not None:
            text = report if isinstance(report, str) else json.dumps(report)
            (self.raw / "bandit.json").write_text(text, encoding="utf-8")
            if exit_code is None:
                exit_code = 1 if json.loads(text)["results"] else 0
        if exit_code is not None:
            (self.raw / "bandit.exit").write_text(f"{exit_code}\n", encoding="utf-8")
        (self.raw / "bandit.stderr.txt").write_text(stderr, encoding="utf-8")

    def fake_report(self, spec=SPEC, files=None):
        files = (
            files
            if files is not None
            else sorted(check.expected_files(self.root, TARGETS)[0])
        )
        metrics = {f: dict(EMPTY_METRICS, loc=1) for f in files}
        results = [result(*s) for s in spec]
        for r in results:
            m = metrics.setdefault(r["filename"], dict(EMPTY_METRICS, loc=1))
            m[f"SEVERITY.{r['issue_severity']}"] += 1
            m[f"CONFIDENCE.{r['issue_confidence']}"] += 1
        totals = dict(EMPTY_METRICS)
        for m in metrics.values():
            for k, v in m.items():
                totals[k] += v
        return {
            "errors": [],
            "generated_at": "2026-11-04T00:00:00Z",
            "metrics": {"_totals": totals, **metrics},
            "results": results,
        }

    def accept_current(self):
        """Write the baseline and register from the recorded run (the reviewer's step)."""
        _, found, bad = check.read_report(self.raw / "bandit.json", self.root, TARGETS)
        if bad:
            raise AssertionError(bad)
        entries = check.new_entries(found)
        for e in entries:
            e["classification"] = CLASSES[e["test_id"]]
            e["origin"] = "phase-5"
        self.save(entries)
        return entries

    def save(self, entries, register=None):
        totals = {
            level: sum(e["severity"] == level for e in entries)
            for level in check.LEVELS
        }
        self.baseline.write_text(
            json.dumps(
                {
                    "schema": check.SCHEMA,
                    "tool": dict(check.TOOL),
                    "scope": {
                        "targets": TARGETS,
                        "command": "bandit -r app tools -f json -q",
                    },
                    "generated": {"commit": "synthetic"},
                    "review": {"register": "SECURITY.md"},
                    "totals": {**totals, "total": len(entries)},
                    "findings": entries,
                }
            ),
            encoding="utf-8",
        )
        rows = check.render_register(entries if register is None else register)
        self.doc.write_text(
            f"# Doc\n\n{check.REGISTER_BEGIN}\n{rows}{check.REGISTER_END}\n",
            encoding="utf-8",
        )

    def entries(self):
        return json.loads(self.baseline.read_text(encoding="utf-8"))["findings"]

    def gate(self, targets=TARGETS):
        argv = [
            "evaluate",
            "--report",
            str(self.raw / "bandit.json"),
            "--exit-file",
            str(self.raw / "bandit.exit"),
            "--stderr-file",
            str(self.raw / "bandit.stderr.txt"),
            "--baseline",
            str(self.baseline),
            "--security-doc",
            str(self.doc),
            "--root",
            str(self.root),
            "--out-dir",
            str(self.out),
        ]
        for t in targets:
            argv += ["--target", t]
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
        ):
            code = check.main(argv)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        return code, summary, out.getvalue()


def kinds(summary):
    return sorted({p["kind"] for p in summary["problems"]})


class GateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.p = Project(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def assertFails(self, *expected):
        code, summary, out = self.p.gate()
        self.assertEqual(code, 1, summary["problems"])
        self.assertEqual(summary["verdict"], "FAIL")
        for kind in expected:
            self.assertIn(kind, kinds(summary))
            self.assertIn(f"::error::bandit {kind}", out)
        return summary

    def assertPasses(self):
        code, summary, _ = self.p.gate()
        self.assertEqual(
            (code, summary["verdict"], summary["problems"]), (0, "PASS", [])
        )
        return summary


class Reports(GateCase):
    """Hand-made reports: decision logic, schema, run integrity and privacy."""

    def setUp(self):
        super().setUp()
        self.p.record(self.p.fake_report())
        self.p.accept_current()

    def test_clean_run_matches_baseline(self):
        summary = self.assertPasses()
        self.assertEqual(summary["counts"], {"HIGH": 0, "MEDIUM": 1, "LOW": 4})
        self.assertEqual({r["status"] for r in summary["findings"]}, {"ACCEPTED"})

    def test_line_movement_alone_is_tolerated(self):
        self.p.write("app/net.py", "\n\n" + SOURCES["app/net.py"])
        spec = [s if s[0] != "app/net.py" else (s[0], s[1] + 2, *s[2:]) for s in SPEC]
        self.p.record(self.p.fake_report(spec))
        summary = self.assertPasses()
        moved = [r for r in summary["findings"] if r.get("baseline_line")]
        self.assertEqual([(r["line"], r["baseline_line"]) for r in moved], [(7, 5)])

    def test_a_new_low_finding_fails(self):
        self.p.write("tools/clean.py", NEW_ASSERT)
        spec = [
            *SPEC,
            ("tools/clean.py", 2, "B101", "assert_used", "LOW", "HIGH", 703, "a"),
        ]
        self.p.record(self.p.fake_report(spec))
        summary = self.assertFails("NEW_FINDING")
        self.assertEqual(kinds(summary), ["NEW_FINDING"])

    def test_b_swapped_finding_with_same_count_fails(self):
        self.p.write("app/checks.py", "def check(value):\n    return value\n")
        self.p.write("tools/clean.py", NEW_ASSERT)
        spec = [s for s in SPEC if s[0] != "app/checks.py"]
        spec.append(
            ("tools/clean.py", 2, "B101", "assert_used", "LOW", "HIGH", 703, "a")
        )
        self.assertEqual(len(spec), len(SPEC))
        self.p.record(self.p.fake_report(spec))
        self.assertFails("NEW_FINDING", "MISSING_FINDING")

    def test_c_new_high_finding_fails(self):
        self.p.write("tools/clean.py", NEW_HIGH)
        spec = [
            *SPEC,
            ("tools/clean.py", 5, "B324", "hashlib", "HIGH", "HIGH", 327, "md5"),
        ]
        self.p.record(self.p.fake_report(spec))
        summary = self.assertFails("NEW_FINDING")
        self.assertEqual(summary["counts"]["HIGH"], 1)
        self.assertIn("HIGH/HIGH B324", summary["problems"][0]["detail"])

    def test_d_malformed_or_missing_baseline_fails(self):
        self.p.baseline.write_text("{not json", encoding="utf-8")
        self.assertFails("BASELINE_MALFORMED")
        self.p.baseline.write_text(
            json.dumps({"schema": check.SCHEMA}), encoding="utf-8"
        )
        self.assertFails("BASELINE_INVALID")
        self.p.baseline.unlink()
        self.assertFails("BASELINE_MISSING")

    def test_d_baseline_schema_violations_fail(self):
        for mutate in (
            lambda d: d.update(extra=1),
            lambda d: d["findings"][0].update(note="x"),
            lambda d: d["findings"][0].update(line="2"),
            lambda d: d["findings"][0].update(fingerprint="ABC"),
            lambda d: d["findings"][0].update(path="../app/checks.py"),
            lambda d: d["totals"].update(LOW=3),
            lambda d: d["tool"].update(version="1.9.3"),
            lambda d: d["findings"][0].update(origin="unknown"),
        ):
            data = json.loads(self.p.baseline.read_text(encoding="utf-8"))
            mutate(data)
            self.p.baseline.write_text(json.dumps(data), encoding="utf-8")
            self.assertFails("BASELINE_INVALID")

    def test_e_modified_baseline_entry_fails(self):
        entries = self.p.entries()
        entries[0]["statement_sha256"] = "0" * 64
        self.p.save(entries)
        summary = self.assertFails("CHANGED_FINDING")
        self.assertIn("statement_sha256", summary["problems"][0]["detail"])

    def test_e_register_must_match_baseline(self):
        entries = self.p.entries()
        register = copy.deepcopy(entries)
        register[1]["classification"] = "F8"
        self.p.save(entries, register=register)
        self.assertFails("REGISTER_MISMATCH")
        self.p.save(entries, register=entries[:-1])
        self.assertFails("REGISTER_MISMATCH")
        self.p.doc.write_text("# Doc without register\n", encoding="utf-8")
        self.assertFails("REGISTER_MISSING")

    def test_expanded_baseline_fails(self):
        entries = self.p.entries()
        extra = dict(entries[0], id="BL-99", fingerprint="1" * 64, line=1)
        self.p.save([*entries, extra])
        summary = self.assertFails("MISSING_FINDING")
        self.assertEqual(kinds(summary), ["MISSING_FINDING"])

    def test_f_missing_target_or_unscanned_file_fails(self):
        files = sorted(check.expected_files(self.p.root, TARGETS)[0])
        files.remove("tools/clean.py")
        self.p.record(self.p.fake_report(files=files))
        self.assertFails("UNSCANNED_FILE")
        self.p.write("other/extra.py", "X = 1\n")
        self.p.record(
            self.p.fake_report(files=[*files, "tools/clean.py", "other/extra.py"])
        )
        self.assertFails("UNEXPECTED_SCOPE")
        code, summary, _ = self.p.gate(targets=["app"])
        self.assertEqual(code, 1)
        self.assertIn("UNEXPECTED_SCOPE", kinds(summary))
        (self.p.root / "tools" / "clean.py").unlink()
        (self.p.root / "tools" / "run.py").unlink()
        (self.p.root / "tools").rmdir()
        self.p.record(
            self.p.fake_report(spec=SPEC[:2], files=["app/checks.py", "app/net.py"])
        )
        self.assertFails("TARGET_MISSING", "MISSING_FINDING")

    def test_g_successful_looking_report_with_scanner_failure_fails(self):
        good = self.p.fake_report()
        for exit_code in (2, 124, 137):
            self.p.record(good, exit_code=exit_code)
            self.assertFails("TOOL_FAILURE")
        self.p.record(good, exit_code=0)
        self.assertFails("TOOL_FAILURE")
        self.p.record(self.p.fake_report(spec=[]), exit_code=1)
        self.assertFails("TOOL_FAILURE")
        self.p.record(good, stderr="[main]\tERROR\tException occurred\n")
        self.assertFails("TOOL_FAILURE")
        (self.p.raw / "bandit.exit").unlink()
        self.assertFails("TOOL_FAILURE")

    def test_missing_or_malformed_report_fails(self):
        (self.p.raw / "bandit.json").unlink()
        self.p.record(exit_code=1)
        self.assertFails("REPORT_MISSING")
        self.p.record("{", exit_code=1)
        self.assertFails("MALFORMED_REPORT")
        bad = self.p.fake_report()
        bad["results"][0]["extra"] = 1
        self.p.record(bad)
        self.assertFails("MALFORMED_REPORT")
        bad = self.p.fake_report()
        bad["metrics"]["_totals"]["SEVERITY.LOW"] = 0
        self.p.record(bad)
        self.assertFails("MALFORMED_REPORT")
        bad = self.p.fake_report()
        bad["results"][0]["filename"] = "/abs/app/checks.py"
        self.p.record(bad)
        self.assertFails("MALFORMED_REPORT")
        bad = self.p.fake_report()
        bad["results"][0]["line_number"] = 99
        bad["results"][0]["line_range"] = [99]
        self.p.record(bad)
        self.assertFails("MALFORMED_REPORT")

    def test_h_duplicate_baseline_entry_fails(self):
        entries = self.p.entries()
        self.p.save([*entries, dict(entries[0], id="BL-50")])
        self.assertFails("BASELINE_DUPLICATE")
        self.p.save([*entries, dict(entries[1], fingerprint="2" * 64)])
        self.assertFails("BASELINE_DUPLICATE")

    def test_i_changed_severity_or_fingerprint_fails(self):
        spec = [
            (
                "app/checks.py",
                2,
                "B101",
                "assert_used",
                "MEDIUM",
                "HIGH",
                703,
                "Use of assert detected.",
            ),
            *SPEC[1:],
        ]
        self.p.record(self.p.fake_report(spec))
        summary = self.assertFails("CHANGED_FINDING")
        self.assertIn("severity LOW->MEDIUM", summary["problems"][0]["detail"])
        self.p.record(self.p.fake_report())
        self.p.write(
            "app/net.py", SOURCES["app/net.py"].replace("timeout=5", "timeout=9")
        )
        self.assertFails("NEW_FINDING", "MISSING_FINDING")

    def test_unknown_classification_fails(self):
        for cls in ("F99", None, "F5", "F7", "R104-A"):
            entries = self.p.entries()
            entries[0]["classification"] = cls
            self.p.save(entries)
            self.assertFails("UNKNOWN_CLASSIFICATION")

    def test_skipped_file_nosec_and_config_file_fail(self):
        report = self.p.fake_report()
        report["errors"] = [{"filename": "app/checks.py", "reason": "syntax error"}]
        self.p.record(report)
        self.assertFails("SKIPPED_FILE")
        report = self.p.fake_report()
        report["metrics"]["app/net.py"]["nosec"] = 1
        report["metrics"]["_totals"]["nosec"] = 1
        self.p.record(report)
        self.assertFails("NOSEC")
        self.p.record(self.p.fake_report())
        self.p.write("app/.bandit", "[bandit]\nskips: B101\n")
        self.assertFails("CONFIG_FILE")

    def test_summary_contains_no_issue_text_or_code(self):
        marker = "PRIVATE-MARKER-7f3a"
        spec = [(*s[:7], f"{marker} text") for s in SPEC]
        report = self.p.fake_report(spec)
        for r in report["results"]:
            r["code"] = f"{marker} code"
        self.p.record(report)
        _, _, out = self.p.gate()
        for name in ("summary.json", "summary.md"):
            self.assertNotIn(marker, (self.p.out / name).read_text(encoding="utf-8"))
        self.assertNotIn(marker, out)
        self.assertEqual(
            sorted(p.name for p in self.p.out.iterdir()), ["summary.json", "summary.md"]
        )

    def test_repository_baseline_and_register_are_valid(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(
                [
                    "validate-baseline",
                    "--baseline",
                    str(REPO / "security" / "bandit-baseline.json"),
                    "--security-doc",
                    str(REPO / "docs" / "SECURITY.md"),
                ]
            )
        self.assertEqual(code, 0, out.getvalue())


class RealBandit(GateCase):
    """Bandit 1.9.4 itself on the synthetic project (in-process, no shell)."""

    def setUp(self):
        if bandit_main is None:
            if REQUIRE_BANDIT:
                self.fail("LLMI_TEST_REQUIRE_BANDIT=1 but bandit is not importable")
            self.skipTest("bandit not importable (run with the security tool venv)")
        super().setUp()
        self.scan()
        self.p.accept_current()

    def scan(self, targets=TARGETS, extra=()):
        """Run `bandit -r <targets> -f json -o <report> -q` and record it like CI does."""
        report = self.p.raw / "bandit.json"
        if report.exists():
            report.unlink()
        argv = ["bandit", "-r", *targets, "-f", "json", "-o", str(report), "-q", *extra]
        err, cwd = io.StringIO(), os.getcwd()
        root_logger = logging.getLogger()
        handlers = list(root_logger.handlers)
        os.chdir(self.p.root)
        try:
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(err):
                try:
                    bandit_main.main()
                    code = 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            os.chdir(cwd)
            root_logger.handlers[:] = handlers
        (self.p.raw / "bandit.exit").write_text(f"{code}\n", encoding="utf-8")
        (self.p.raw / "bandit.stderr.txt").write_text(err.getvalue(), encoding="utf-8")
        return code

    def test_control_scan_passes(self):
        summary = self.assertPasses()
        self.assertEqual(
            sorted(r["test_id"] for r in summary["findings"]), EXPECTED_TESTS
        )
        self.assertEqual(summary["bandit_exit"], 1)

    def test_line_movement_alone_passes(self):
        self.p.write("app/checks.py", "# moved\n\n" + SOURCES["app/checks.py"])
        self.scan()
        self.assertPasses()

    def test_a_new_issue_fails(self):
        self.p.write("tools/clean.py", NEW_ASSERT)
        self.scan()
        self.assertFails("NEW_FINDING")

    def test_b_removed_plus_new_issue_same_count_fails(self):
        self.p.write("app/checks.py", "def check(value):\n    return value\n")
        self.p.write("tools/clean.py", NEW_ASSERT)
        self.scan()
        summary = self.assertFails("NEW_FINDING", "MISSING_FINDING")
        self.assertEqual(sum(summary["counts"].values()), len(EXPECTED_TESTS))

    def test_c_new_high_issue_fails(self):
        self.p.write("tools/clean.py", NEW_HIGH)
        self.scan()
        summary = self.assertFails("NEW_FINDING")
        self.assertEqual(summary["counts"]["HIGH"], 1)

    def test_new_b106_issue_fails(self):
        self.p.write("tools/clean.py", NEW_B106)
        self.scan()
        summary = self.assertFails("NEW_FINDING")
        self.assertEqual(kinds(summary), ["NEW_FINDING"])
        self.assertEqual(summary["problems"][0]["test_id"], "B106")

    def test_stale_baseline_entry_fails(self):
        self.p.write("app/checks.py", "def check(value):\n    return value\n")
        self.scan()
        summary = self.assertFails("MISSING_FINDING")
        self.assertEqual(kinds(summary), ["MISSING_FINDING"])

    def test_altered_finding_fails(self):
        self.p.write("tools/run.py", SOURCES["tools/run.py"].replace("status", "log"))
        self.scan()
        self.assertFails("NEW_FINDING", "MISSING_FINDING")

    def test_f_missing_scan_target_fails(self):
        self.scan(targets=["app"])
        self.assertFails("UNSCANNED_FILE", "MISSING_FINDING")

    def test_g_fake_report_with_real_scanner_failure_fails(self):
        good = (self.p.raw / "bandit.json").read_text(encoding="utf-8")
        code = self.scan(extra=("-t", "B999"))
        self.assertEqual(code, 2)
        # Bandit opens (truncates) the -o file while parsing arguments, then fails.
        self.assertEqual((self.p.raw / "bandit.json").read_text(encoding="utf-8"), "")
        self.assertFails("TOOL_FAILURE", "REPORT_MISSING")
        (self.p.raw / "bandit.json").write_text(good, encoding="utf-8")
        summary = self.assertFails("TOOL_FAILURE")
        self.assertEqual(kinds(summary), ["TOOL_FAILURE"])

    def test_i_changed_fingerprint_fails(self):
        self.p.write(
            "app/net.py", SOURCES["app/net.py"].replace("timeout=5", "timeout=9")
        )
        self.scan()
        self.assertFails("NEW_FINDING", "MISSING_FINDING")

    def test_unparsable_file_fails(self):
        self.p.write("tools/broken.py", "def broken(:\n")
        self.scan()
        self.assertFails("SKIPPED_FILE")

    def test_nosec_suppression_fails(self):
        self.p.write(
            "app/checks.py", SOURCES["app/checks.py"].replace("> 0\n", "> 0  # nosec\n")
        )
        self.scan()
        self.assertFails("NOSEC", "MISSING_FINDING")

    def test_project_bandit_config_fails(self):
        self.p.write("app/.bandit", "[bandit]\nskips: B101\n")
        self.scan()
        self.assertFails("CONFIG_FILE", "MISSING_FINDING")

    def test_builtin_baseline_hides_what_the_gate_reports(self):
        raw = self.p.root.parent / "builtin-baseline.json"
        raw.write_text(
            (self.p.raw / "bandit.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        extra = "\n\ndef fetch_again(url):\n    return urllib.request.urlopen(url, timeout=7)\n"
        self.p.write("app/net.py", SOURCES["app/net.py"] + extra)
        self.assertEqual(
            self.scan(extra=("-b", str(raw))), 0
        )  # Bandit -b: exit 0, hidden
        self.assertEqual(
            json.loads((self.p.raw / "bandit.json").read_text())["results"], []
        )
        self.scan()
        self.assertFails("NEW_FINDING")


if __name__ == "__main__":
    unittest.main()
