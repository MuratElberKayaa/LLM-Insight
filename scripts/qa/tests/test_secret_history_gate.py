"""Offline tests of scripts/qa/secret_history_gate.py (R104-3). Stdlib only, no network.

Every case builds isolated temporary Git repositories. All tokens are synthetic and assembled at
run time, so this file itself holds no detector match and adds no finding to the history scan.

Run: python -I -m unittest discover -s scripts/qa/tests -p test_secret_history_gate.py -v
"""

import contextlib
import copy
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

QA = Path(__file__).resolve().parents[1]
REPO_ROOT = QA.parents[1]
sys.path.insert(0, str(QA))

import secret_history_gate as gate

ss = gate.ss

SYNTH_OPENAI = "sk-" + "Zq7Xw2" * 5
SYNTH_AWS = "AKIA" + "QZ7X" * 4
SYNTH_GITHUB = "ghp_" + "x1Y2z3" * 7
SYNTH_GROQ = "gsk" + "_" + "x1Y2z3" * 9
SYNTH_URL = "postgresql" + "://" + "fixture:" + "Zq7Xk2Pw9L" + "@127.0.0.1:1/x"
SYNTH_URL_OTHER = "postgresql" + "://" + "fixture:" + "Bv4Nm8Qs3T" + "@127.0.0.1:1/x"
SYNTH_KEY_HEADER = "-----BEGIN " + "PRIVATE KEY-----"
ALL_SYNTH = [
    SYNTH_OPENAI,
    SYNTH_AWS,
    SYNTH_GITHUB,
    SYNTH_GROQ,
    SYNTH_URL,
    SYNTH_URL_OTHER,
    SYNTH_KEY_HEADER,
    "Zq7Xk2Pw9L",
    "Bv4Nm8Qs3T",
]
FIXTURE_PATH = "docs/SECURITY.md"
ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "synthetic",
    "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
    "GIT_COMMITTER_NAME": "synthetic",
    "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
}


class Repo:
    """One temporary Git repository driven only through secret_scan.git."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True)
        ss.git(self.path, "init", "-q", "-b", "main")

    def git(self, *args):
        return ss.git(self.path, *args)

    def commit(self, files, message="synthetic commit"):
        for rel, content in files.items():
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", message)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD").decode().strip()

    def blob(self, rel):
        return self.git("rev-parse", f"HEAD:{rel}").decode().strip()


class GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="llmi-history-gate-"))
        self.addCleanup(self._cleanup)
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = Repo(self.tmp / "repo")
        self.first = self.repo.commit(
            {FIXTURE_PATH: f"Example: {SYNTH_URL} end\n", "app/clean.py": "VALUE = 1\n"}
        )
        self.repo.git("tag", "-a", "-m", "synthetic release", "phase-0")
        self.baseline_path = self.tmp / "baseline.json"
        self.doc = self.tmp / "SECURITY.md"
        self.raw = self.tmp / "raw"
        self.out = self.tmp / "summary"
        self.baseline = self.make_baseline(self.repo)
        self.write_baseline(self.baseline)

    def _cleanup(self):
        def unlock(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(self.tmp, onerror=unlock)

    def make_baseline(self, repo):
        report = gate.scan_report(repo.path)
        self.assertEqual(report["status"], "COMPLETE", report["problems"])
        entries = []
        for i, finding in enumerate(sorted(report["findings"], key=gate.entry_key), 1):
            entries.append(
                {
                    "id": f"SH-{i:02d}",
                    **{k: finding[k] for k in gate.FINDING_KEYS},
                    "classification": "F9-DOC-EXAMPLE",
                    "reason": "Synthetic documentation placeholder for this test.",
                    "review_ref": gate.CLASSES["F9-DOC-EXAMPLE"]["review_ref"],
                }
            )
        tags = {
            line.split()[1].replace("refs/tags/", ""): line.split()[0]
            for line in repo.git(
                "for-each-ref", "refs/tags", "--format=%(objectname) %(refname)"
            )
            .decode()
            .splitlines()
        }
        roots = repo.git("rev-list", "--max-parents=0", "--all").decode().split()
        return {
            "schema": gate.SCHEMA_BASELINE,
            "tool": {
                "scanner": "scripts/qa/secret_scan.py",
                "detector_digest": ss.detector_digest(),
            },
            "scope": {
                "ref_prefixes": gate.ALLOWED_REF_PREFIXES,
                "required_refs": ["refs/heads/main"],
                "required_tags": tags,
                "root_commits": sorted(roots),
                "exclusions": [],
            },
            "generated": {"commit": repo.head(), "method": "synthetic test"},
            "review": {"register": "test", "evidence": "test", "change_rule": "test"},
            "totals": gate.compute_totals(entries),
            "findings": entries,
        }

    def write_baseline(self, baseline, register=None):
        self.baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        rows = gate.render_register(
            baseline["findings"] if register is None else register
        )
        self.doc.write_text(
            "\n".join(["# doc", gate.REGISTER_BEGIN, *rows, gate.REGISTER_END, ""]),
            encoding="utf-8",
        )
        self.reviewed(baseline)

    def reviewed(self, baseline):
        patcher = mock.patch.object(
            gate, "REVIEWED_BASELINE_SHA256", gate.digest(baseline)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main([str(a) for a in argv])
        return code, out.getvalue() + err.getvalue()

    def coverage(self, repo=None, extra=()):
        path = self.raw / "coverage.json"
        code, text = self.run_main(
            "coverage",
            "--repo",
            (repo or self.repo).path,
            "--baseline",
            self.baseline_path,
            "--security-doc",
            self.doc,
            "--out",
            path,
            *extra,
        )
        return code, text, json.loads(path.read_text(encoding="utf-8"))

    def scan(self, repo=None):
        path = self.raw / "scan.json"
        code, text = self.run_main(
            "scan", "--repo", (repo or self.repo).path, "--out", path
        )
        return code, text, json.loads(path.read_text(encoding="utf-8"))

    def evaluate(self):
        return self.run_main(
            "evaluate",
            "--coverage",
            self.raw / "coverage.json",
            "--scan",
            self.raw / "scan.json",
            "--baseline",
            self.baseline_path,
            "--security-doc",
            self.doc,
            "--out-dir",
            self.out,
        )

    def gate_all(self, repo=None, extra=()):
        logs = []
        for step in (self.coverage(repo, extra), self.scan(repo)):
            logs.append(step[1])
        code, text = self.evaluate()
        logs.append(text)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        return code, summary, "\n".join(logs)

    def kinds(self, summary):
        return {p["kind"] for p in summary["problems"]}

    def assert_fails(self, kind, repo=None, extra=()):
        code, summary, _ = self.gate_all(repo, extra)
        self.assertEqual(code, 1, summary["problems"])
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertIn(kind, self.kinds(summary))
        return summary


class AcceptedFixtures(GateCase):
    def test_accepted_fixture_passes(self):
        code, summary, _ = self.gate_all()
        self.assertEqual(code, 0, summary["problems"])
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["baseline_matching"], {"ACCEPTED": 1})
        self.assertEqual(summary["finding_count"], 1)

    def test_clean_new_commit_still_passes(self):
        self.repo.commit({"app/more.py": "OTHER = 2\n"}, "clean change")
        code, summary, _ = self.gate_all()
        self.assertEqual(code, 0, summary["problems"])

    def test_report_covers_every_object_type(self):
        _, summary, _ = self.gate_all()
        counts = summary["object_counts"]
        self.assertEqual(counts["commit"], 1)
        self.assertEqual(counts["tag"], 1)
        self.assertEqual(counts["blob"], 2)
        self.assertGreaterEqual(counts["tree"], 2)
        self.assertEqual(summary["scanned_ref_categories"], {"branch": 1, "tag": 1})


class NewFindings(GateCase):
    def test_a_new_secret_in_a_blob_fails(self):
        self.repo.commit({"app/leak.py": f"KEY = '{SYNTH_OPENAI}'\n"})
        summary = self.assert_fails("NEW_FINDING")
        self.assertEqual(summary["baseline_matching"], {"ACCEPTED": 1, "NEW": 1})

    def test_a_secret_only_in_a_deleted_file_still_fails(self):
        self.repo.commit({"app/leak.py": f"KEY = '{SYNTH_AWS}'\n"})
        (self.repo.path / "app/leak.py").unlink()
        self.repo.commit({}, "remove the file")
        self.assert_fails("NEW_FINDING")

    def test_a_groq_key_in_a_blob_fails_without_printing_it(self):
        self.repo.commit({"app/leak.py": f"KEY = '{SYNTH_GROQ}'\n"})
        summary = self.assert_fails("NEW_FINDING")
        self.assertEqual(
            [f["detector"] for f in summary["findings"] if f["status"] == "NEW"],
            ["groq_key"],
        )
        self.assertNotIn(SYNTH_GROQ, json.dumps(summary))

    def test_b_new_secret_in_a_commit_message_fails(self):
        self.repo.commit({"app/more.py": "OTHER = 2\n"}, f"message with {SYNTH_GITHUB}")
        summary = self.assert_fails("NEW_FINDING")
        self.assertEqual(
            [f["object_type"] for f in summary["findings"] if f["status"] == "NEW"],
            ["commit"],
        )

    def test_c_new_secret_in_an_annotated_tag_fails(self):
        self.repo.git("tag", "-a", "-m", f"release note {SYNTH_OPENAI}", "extra-tag")
        summary = self.assert_fails("NEW_FINDING")
        self.assertEqual(
            [f["object_type"] for f in summary["findings"] if f["status"] == "NEW"],
            ["tag"],
        )

    def test_nested_annotated_tag_is_scanned(self):
        self.repo.git("tag", "-a", "-m", f"inner {SYNTH_OPENAI}", "inner")
        self.repo.git("tag", "-a", "-m", "outer", "outer", "inner")
        summary = self.assert_fails("NEW_FINDING")
        self.assertEqual(
            [f["object_type"] for f in summary["findings"] if f["status"] == "NEW"],
            ["tag"],
        )

    def test_secret_in_a_file_name_is_detected_and_not_printed(self):
        name = "cfg-" + SYNTH_AWS + ".txt"
        self.repo.commit({f"app/{name}": "harmless\n"})
        summary = self.assert_fails("NEW_FINDING")
        self.assertEqual(
            [f["object_type"] for f in summary["findings"] if f["status"] == "NEW"],
            ["tree"],
        )
        self.assertNotIn(SYNTH_AWS, json.dumps(summary))

    def test_i_new_occurrence_resembling_a_fixture_fails(self):
        self.repo.commit(
            {FIXTURE_PATH: f"Example: {SYNTH_URL} end\nAgain: {SYNTH_URL} end\n"}
        )
        self.assert_fails("NEW_FINDING")

    def test_i_same_fixture_bytes_in_a_new_path_fail(self):
        self.repo.commit({"docs/copy.md": f"Example: {SYNTH_URL} end\n"})
        self.assert_fails("PATH_MISMATCH")

    def test_changed_existing_fixture_fails(self):
        self.repo.commit({FIXTURE_PATH: f"Example: {SYNTH_URL_OTHER} end\n"})
        self.assert_fails("NEW_FINDING")

    def test_d_same_count_replacement_fails(self):
        other = Repo(self.tmp / "other")
        other.commit(
            {
                FIXTURE_PATH: f"Example: {SYNTH_URL_OTHER} end\n",
                "app/clean.py": "VALUE = 1\n",
            }
        )
        other.git("tag", "-a", "-m", "synthetic release", "phase-0")
        baseline = copy.deepcopy(self.baseline)
        baseline["scope"]["required_tags"] = self.make_baseline(other)["scope"][
            "required_tags"
        ]
        baseline["scope"]["root_commits"] = self.make_baseline(other)["scope"][
            "root_commits"
        ]
        self.write_baseline(baseline)
        code, summary, _ = self.gate_all(other)
        self.assertEqual(code, 1)
        self.assertEqual(summary["finding_count"], summary["baseline_entries"])
        self.assertEqual(summary["baseline_matching"], {"MISSING": 1, "NEW": 1})
        self.assertTrue({"NEW_FINDING", "MISSING_FINDING"} <= self.kinds(summary))


class BaselineIntegrity(GateCase):
    def mutated(self, change):
        baseline = copy.deepcopy(self.baseline)
        change(baseline)
        return baseline

    def assert_baseline_rejected(self, baseline, kind):
        self.baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        code, text = self.run_main(
            "validate-baseline",
            "--baseline",
            self.baseline_path,
            "--security-doc",
            self.doc,
        )
        self.assertEqual(code, 1, text)
        self.assertIn(kind, text)

    def test_e_modified_entry_with_the_old_review_fails(self):
        changes = {
            "object_id": lambda e: e.update(object_id="a" * 40),
            "offset": lambda e: e.update(offset=e["offset"] + 1),
            "occurrence": lambda e: e.update(occurrence=1),
            "length": lambda e: e.update(length=e["length"] + 1),
            "detector": lambda e: e.update(detector="jwt"),
        }
        for name, change in changes.items():
            with self.subTest(name):
                baseline = copy.deepcopy(self.baseline)
                change(baseline["findings"][0])
                baseline["totals"] = gate.compute_totals(baseline["findings"])
                self.assert_baseline_rejected(baseline, "::error")

    def test_e_edited_entry_cannot_match_even_when_rereviewed(self):
        for field in ("offset", "length"):
            with self.subTest(field):
                baseline = copy.deepcopy(self.baseline)
                baseline["findings"][0][field] += 1
                self.write_baseline(baseline)
                self.assertTrue(
                    {"NEW_FINDING", "MISSING_FINDING"}
                    <= self.kinds(self.assert_fails("NEW_FINDING"))
                )

    def test_f_missing_baseline_fails(self):
        self.baseline_path.unlink()
        summary = self.assert_fails("BASELINE_MISSING")
        self.assertEqual(summary["baseline_entries"], None)

    def test_malformed_baseline_fails(self):
        self.baseline_path.write_text("{not json", encoding="utf-8")
        self.assert_fails("BASELINE_MALFORMED")

    def test_invalid_object_identity_fails(self):
        baseline = self.mutated(lambda b: b["findings"][0].update(object_id="XYZ"))
        self.assert_baseline_rejected(baseline, "BASELINE_INVALID")

    def test_duplicate_records_fail(self):
        def dup(b):
            twin = dict(b["findings"][0], id="SH-02")
            b["findings"].append(twin)
            b["totals"] = gate.compute_totals(b["findings"])

        baseline = self.mutated(dup)
        self.reviewed(baseline)
        self.doc.write_text(
            "\n".join(
                [
                    gate.REGISTER_BEGIN,
                    *gate.render_register(baseline["findings"]),
                    gate.REGISTER_END,
                ]
            ),
            encoding="utf-8",
        )
        self.assert_baseline_rejected(baseline, "BASELINE_DUPLICATE")

    def test_unknown_classification_fails(self):
        for value in ("F8", "ACCEPTED", None):
            with self.subTest(value):
                baseline = copy.deepcopy(self.baseline)
                baseline["findings"][0]["classification"] = value
                self.assert_baseline_rejected(baseline, "UNKNOWN_CLASSIFICATION")

    def test_class_is_limited_to_its_paths_and_detector(self):
        for change in (
            lambda e: e.update(paths=["app/other.py"]),
            lambda e: e.update(detector="openai_key"),
            lambda e: e.update(object_type="commit", paths=[]),
        ):
            baseline = copy.deepcopy(self.baseline)
            change(baseline["findings"][0])
            self.assert_baseline_rejected(baseline, "UNKNOWN_CLASSIFICATION")

    def test_expansion_without_review_fails(self):
        def expand(b):
            b["findings"].append(dict(b["findings"][0], id="SH-02", offset=999))
            b["totals"] = gate.compute_totals(b["findings"])

        baseline = self.mutated(expand)
        self.doc.write_text(
            "\n".join(
                [
                    gate.REGISTER_BEGIN,
                    *gate.render_register(baseline["findings"]),
                    gate.REGISTER_END,
                ]
            ),
            encoding="utf-8",
        )
        self.assert_baseline_rejected(baseline, "BASELINE_UNREVIEWED")

    def test_register_must_match_baseline(self):
        self.doc.write_text("# no register\n", encoding="utf-8")
        self.assert_fails("REGISTER_MISSING")
        self.write_baseline(self.baseline, register=[])
        self.assert_fails("REGISTER_MISMATCH")

    def test_changed_detector_fails(self):
        self.write_baseline(
            self.mutated(lambda b: b["tool"].update(detector_digest="0" * 64))
        )
        self.assert_fails("DETECTOR_CHANGED")

    def test_weakened_detector_pattern_fails(self):
        narrowed = dict(ss.PATTERNS)
        narrowed["url_userinfo"] = ss.re.compile(rb"never-matches-anything")
        with mock.patch.object(ss, "PATTERNS", narrowed):
            self.assert_fails("DETECTOR_CHANGED")

    def test_baseline_holding_a_value_fails(self):
        baseline = self.mutated(lambda b: b["generated"].update(note=SYNTH_OPENAI))
        self.assert_baseline_rejected(baseline, "BASELINE_INVALID")

    def test_scope_with_an_exclusion_fails(self):
        baseline = self.mutated(lambda b: b["scope"].update(exclusions=["docs/"]))
        self.assert_baseline_rejected(baseline, "BASELINE_INVALID")


class GroqDetector(unittest.TestCase):
    """ADR-41. The key length is not documented, so no single length may be assumed."""

    @staticmethod
    def key(length=52):
        return b"gsk" + b"_" + (b"x1Y" * length)[:length]

    def names(self, data):
        return [name for name, *_ in ss.iter_matches(data)]

    def test_every_reported_length_is_matched(self):
        for length in (20, 40, 48, 52, 56, 80):
            with self.subTest(length=length):
                self.assertEqual(self.names(self.key(length)), ["groq_key"])

    def test_embedded_keys_are_matched(self):
        for carrier in (b"KEY='%s'", b"GROQ_%s", b"model/%s", b"%s:free", b"a-%s"):
            with self.subTest(carrier=carrier):
                self.assertEqual(self.names(carrier % self.key()), ["groq_key"])

    def test_lookalikes_are_not_matched(self):
        for value in (
            b"gsk",
            b"gsk_",
            b"gsk_short",
            b"gsk" + b"_" + b"a" * 19,
            b"gsk-model-v1",
            b"gsk_test_model_name_version_1",
            b"GSK_" + b"A" * 24,
            b"bigsk" + b"_" + b"a" * 24,
            b"risk_" + b"a" * 24,
        ):
            with self.subTest(value=value):
                self.assertEqual(self.names(value), [])

    def test_the_local_scanner_names_the_detector_and_not_the_value(self):
        self.assertEqual(ss.scan_bytes(self.key(), {}), ["pattern groq_key"])

    def test_matches_carry_no_value(self):
        for item in ss.iter_matches(self.key()):
            self.assertFalse([part for part in item if isinstance(part, bytes)])

    def test_the_detector_is_part_of_the_reviewed_digest(self):
        without = {k: v for k, v in ss.PATTERNS.items() if k != "groq_key"}
        reviewed = ss.detector_digest()
        with mock.patch.object(ss, "PATTERNS", without):
            self.assertNotEqual(ss.detector_digest(), reviewed)


class RepositoryBaseline(unittest.TestCase):
    def test_repository_baseline_and_register_are_valid(self):
        baseline, problems = gate.load_baseline(
            REPO_ROOT / "security/secret-history-baseline.json",
            REPO_ROOT / "docs/SECURITY.md",
        )
        self.assertEqual(problems, [])
        self.assertEqual(baseline["totals"]["total"], 39)
        self.assertEqual(gate.digest(baseline), gate.REVIEWED_BASELINE_SHA256)
        self.assertEqual(
            baseline["totals"]["by_classification"],
            {
                "F9-CHANGE-ME-VALUE": 11,
                "F9-DOC-EXAMPLE": 20,
                "F9-TEST-FIXTURE": 8,
            },
        )

    def test_gate_does_not_apply_the_local_allow_list(self):
        self.assertEqual(
            [
                m[0]
                for m in ss.iter_matches(("x" + "://" + "u:" + "CHANGE_ME@h").encode())
            ],
            ["url_userinfo"],
        )


class Coverage(GateCase):
    def clone(self, name, *options):
        target = self.tmp / name
        url = self.repo.path.resolve().as_uri()
        self.repo.git("config", "uploadpack.allowFilter", "true")
        ss.git(self.tmp, "clone", "-q", *options, url, str(target))
        return target

    def as_repo(self, path):
        repo = Repo.__new__(Repo)
        repo.path = path
        return repo

    def test_g_shallow_clone_fails_the_coverage_requirement(self):
        self.repo.commit({"app/more.py": "OTHER = 2\n"})
        summary = self.assert_fails(
            "COVERAGE_SHALLOW_HISTORY",
            self.as_repo(self.clone("shallow", "--depth", "1")),
        )
        self.assertIn("COVERAGE_INCOMPLETE", self.kinds(summary))

    def test_g_partial_clone_fails(self):
        repo = self.as_repo(
            self.clone("partial", "--filter=blob:none", "--no-checkout")
        )
        self.assert_fails("COVERAGE_PARTIAL_CLONE", repo)

    def test_g_missing_required_tag_fails(self):
        self.repo.git("tag", "-d", "phase-0")
        self.assert_fails("COVERAGE_REQUIRED_TAG_MISSING")

    def test_g_moved_required_tag_fails(self):
        self.repo.commit({"app/more.py": "OTHER = 2\n"})
        self.repo.git("tag", "-f", "-a", "-m", "moved", "phase-0")
        self.assert_fails("COVERAGE_REQUIRED_TAG_MOVED")

    def test_g_missing_required_branch_fails(self):
        self.repo.git("branch", "-m", "main", "renamed")
        self.assert_fails("COVERAGE_REQUIRED_REF_MISSING")

    def test_g_unexpected_extra_root_fails(self):
        self.repo.git("checkout", "-q", "--orphan", "other-root")
        self.repo.commit({"app/orphan.py": "X = 1\n"}, "orphan")
        self.assert_fails("COVERAGE_ROOT_COMMITS_DIFFER")

    def test_hidden_ref_namespace_fails(self):
        self.repo.git("update-ref", "refs/internal/hidden", self.first)
        self.assert_fails("COVERAGE_UNEXPECTED_REF")

    def test_replace_ref_fails(self):
        tree = self.repo.git("rev-parse", "HEAD^{tree}").decode().strip()
        env = "synthetic"
        other = self.repo.git("commit-tree", tree, "-m", env).decode().strip()
        self.repo.git("update-ref", "refs/replace/" + self.first, other)
        self.assert_fails("COVERAGE_UNEXPECTED_REF")

    def test_commit_reachable_only_from_a_pull_ref_is_scanned(self):
        tip = self.repo.commit({"app/pr.py": f"KEY = '{SYNTH_OPENAI}'\n"}, "pr commit")
        self.repo.git("update-ref", "refs/remotes/pull/9/merge", tip)
        self.repo.git("reset", "-q", "--hard", self.first)
        self.repo.git("checkout", "-q", "--detach", "refs/remotes/pull/9/merge")
        summary = self.assert_fails(
            "NEW_FINDING", extra=["--expect-head", tip, "--expect-commit", tip]
        )
        self.assertEqual(summary["scanned_ref_categories"]["pull"], 1)

    def test_enumeration_difference_fails(self):
        real = ss.git
        blob = self.repo.blob("app/clean.py").encode()

        def dropping(repo, *args):
            out = real(repo, *args)
            if args[0] == "rev-list" and "--objects" in args:
                out = b"\n".join(x for x in out.splitlines() if x != blob) + b"\n"
            return out

        with mock.patch.object(ss, "git", side_effect=dropping):
            self.assert_fails("COVERAGE_ENUMERATION_MISMATCH")

    def test_expected_commits(self):
        cases = [
            (["--expect-commit", "a" * 40], "COVERAGE_EXPECTED_COMMIT_UNREACHABLE"),
            (["--expect-commit", ""], "COVERAGE_EXPECTED_COMMIT_INVALID"),
            (["--expect-head", "b" * 40], "COVERAGE_HEAD_MISMATCH"),
        ]
        for extra, kind in cases:
            with self.subTest(kind):
                self.assert_fails(kind, extra=extra)

    def test_expected_commit_present_passes(self):
        code, summary, _ = self.gate_all(
            extra=["--expect-commit", self.first, "--expect-head", self.first]
        )
        self.assertEqual(code, 0, summary["problems"])

    def test_submodule_entry_fails(self):
        self.repo.git(
            "update-index", "--add", "--cacheinfo", f"160000,{self.first},vendor/sub"
        )
        self.repo.git("commit", "-q", "--no-gpg-sign", "-m", "submodule")
        self.assert_fails("COVERAGE_GITLINK")


class GitFailures(GateCase):
    def test_h_directory_without_a_repository_fails(self):
        (self.tmp / "empty").mkdir()
        repo = Repo.__new__(Repo)
        repo.path = self.tmp / "empty"
        env = {"GIT_CEILING_DIRECTORIES": str(self.tmp)}
        with mock.patch.dict(os.environ, env):
            summary = self.assert_fails("COVERAGE_GIT_FAILURE", repo)
        self.assertIn("SCAN_GIT_FAILURE", self.kinds(summary))

    def test_h_git_error_text_is_never_reported(self):
        error = ss.subprocess.CalledProcessError(
            128,
            ["git", "rev-list"],
            output=b"",
            stderr=f"fatal: {SYNTH_OPENAI}".encode(),
        )
        with mock.patch.object(ss, "git", side_effect=error):
            code, summary, text = self.gate_all()
        self.assertEqual(code, 1)
        self.assertIn("COVERAGE_GIT_FAILURE", self.kinds(summary))
        self.assertNotIn(SYNTH_OPENAI, text + json.dumps(summary))

    def test_h_git_binary_missing_fails(self):
        with mock.patch.object(ss, "git", side_effect=FileNotFoundError("git")):
            code, summary, _ = self.gate_all()
        self.assertEqual(code, 1)
        self.assertIn("COVERAGE_GIT_FAILURE", self.kinds(summary))

    def test_h_corrupted_object_fails(self):
        blob = self.repo.blob("app/clean.py")
        path = self.repo.path / ".git/objects" / blob[:2] / blob[2:]
        os.chmod(path, stat.S_IWRITE)
        path.unlink()
        code, _, _ = self.gate_all()
        self.assertEqual(code, 1)

    def test_malformed_stream_is_a_failure(self):
        with (
            mock.patch.object(ss, "git", return_value=b"not a header\nrest"),
            self.assertRaises(gate.GitFailure),
        ):
            gate.read_objects(self.repo.path)

    def test_truncated_stream_is_a_failure(self):
        data = b"a" * 40 + b" blob 10\nshort"
        with (
            mock.patch.object(ss, "git", return_value=data),
            self.assertRaises(gate.GitFailure),
        ):
            gate.read_objects(self.repo.path)


class Reports(GateCase):
    def prepared(self):
        self.gate_all()
        return self.raw / "coverage.json", self.raw / "scan.json"

    def edit(self, path, change):
        report = json.loads(path.read_text(encoding="utf-8"))
        change(report)
        path.write_text(json.dumps(report), encoding="utf-8")

    def evaluated(self, kind):
        code, _ = self.evaluate()
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertIn(kind, self.kinds(summary))

    def test_missing_reports_fail(self):
        coverage, scan = self.prepared()
        scan.unlink()
        self.evaluated("SCAN_MISSING")
        coverage.unlink()
        self.evaluated("COVERAGE_MISSING")

    def test_malformed_reports_fail(self):
        coverage, scan = self.prepared()
        scan.write_text("[]", encoding="utf-8")
        self.evaluated("SCAN_MALFORMED")
        coverage.write_text("oops", encoding="utf-8")
        self.evaluated("COVERAGE_MALFORMED")

    def test_extra_report_key_fails(self):
        _, scan = self.prepared()
        self.edit(scan, lambda r: r.update(extra=1))
        self.evaluated("SCAN_MALFORMED")

    def test_skipped_scan_with_no_findings_fails(self):
        _, scan = self.prepared()
        self.edit(scan, lambda r: r.update(status="FAILED", findings=[]))
        self.evaluated("SCAN_INCOMPLETE")

    def test_partial_scan_counts_fail(self):
        _, scan = self.prepared()
        self.edit(scan, lambda r: r["scanned"].update(blob=1))
        self.evaluated("SCOPE_MISMATCH")

    def test_report_of_another_repository_state_fails(self):
        _, scan = self.prepared()
        self.edit(scan, lambda r: r.update(head="c" * 40))
        self.evaluated("SCOPE_MISMATCH")

    def test_removed_finding_fails(self):
        _, scan = self.prepared()
        self.edit(scan, lambda r: r.update(findings=[]))
        self.evaluated("MISSING_FINDING")

    def test_unexpected_exclusion_fails(self):
        _, scan = self.prepared()
        self.edit(scan, lambda r: r.update(exclusions=["docs/"]))
        self.evaluated("UNEXPECTED_EXCLUSION")

    def test_coverage_with_another_scope_fails(self):
        coverage, _ = self.prepared()
        self.edit(coverage, lambda r: r.update(scope_digest="d" * 64))
        self.evaluated("SCOPE_MISMATCH")

    def test_j_outputs_never_contain_synthetic_values(self):
        self.repo.commit(
            {
                "app/leak.py": f"A = '{SYNTH_OPENAI}'\nB = '{SYNTH_KEY_HEADER}'\n",
                "app/" + "n-" + SYNTH_AWS + ".txt": f"y = '{SYNTH_OPENAI}'\n",
            },
            f"message {SYNTH_GITHUB}",
        )
        self.repo.git("tag", "-a", "-m", f"tag {SYNTH_URL_OTHER}", "leaky")
        step_summary = self.tmp / "step-summary.md"
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(step_summary)}):
            code, summary, text = self.gate_all()
        self.assertEqual(code, 1)
        self.assertGreaterEqual(summary["baseline_matching"]["NEW"], 5)
        outputs = [
            text,
            step_summary.read_text(encoding="utf-8"),
            (self.raw / "coverage.json").read_text(encoding="utf-8"),
            (self.raw / "scan.json").read_text(encoding="utf-8"),
            (self.out / "summary.json").read_text(encoding="utf-8"),
            (self.out / "summary.md").read_text(encoding="utf-8"),
        ]
        for output in outputs:
            for value in ALL_SYNTH:
                self.assertNotIn(value, output)
        self.assertEqual(
            sorted(p.name for p in self.out.iterdir()), ["summary.json", "summary.md"]
        )

    def test_annotations_cannot_inject_workflow_commands(self):
        hostile = "x::error::injected\n::warning::more %0A"
        rendered = gate.problem("KIND", hostile)["detail"]
        self.assertNotIn("::", rendered)
        self.assertNotIn("\n", rendered)
        self.assertNotIn("%", rendered)
        self.assertEqual(gate.safe_path(b"dir/a b\nc.txt"), "<unprintable-path>")
        self.assertEqual(gate.problem("KIND", SYNTH_OPENAI)["detail"], "value-redacted")


if __name__ == "__main__":
    unittest.main()
