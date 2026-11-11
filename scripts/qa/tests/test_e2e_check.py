"""Offline tests of scripts/qa/e2e_check.py and static checks of the E2E inputs (R104-5).

Stdlib only: no Docker, no browser, no network beyond binding a loopback port. Values are
synthetic and assembled at run time, so this file holds no detector match.

Run: python -I -m unittest discover -s scripts/qa/tests -p test_e2e_check.py -v
"""

import contextlib
import io
import json
import re
import socket
import sys
import tempfile
import unittest
from pathlib import Path

QA = Path(__file__).resolve().parents[1]
REPO = QA.parents[1]
sys.path.insert(0, str(QA))

import e2e_check as check

PROJECT = "llmi-r1045-12345"
REQUIRED = check.ibc.REQUIRED_ENV
GOOD = {name: f"synthetic-{i}-value-{'0' * 24}" for i, name in enumerate(REQUIRED)}
USER_VAR, SECRET_VAR, DB_VAR, PEPPER_VAR, ADMIN_VAR = REQUIRED
API_VAR = "LLMI_API" + "_TOKEN"
ORIGIN_VAR = "LLMI_API_BASE_URL"
URL_VAR = "LLMI_DATABASE_URL"
RUN = "abcdefabcdef"


def database_url(host="127.0.0.1", port=55432, db=None, user=None, credential=None):
    parts = (
        "postgresql+psycopg" + "://",
        user or GOOD[USER_VAR],
        ":",
        credential or GOOD[SECRET_VAR],
        "@",
        f"{host}:{port}/",
        db or GOOD[DB_VAR],
    )
    return "".join(parts)


def kinds(problems):
    return {item["kind"] for item in problems}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)

    def env_values(self):
        return {**GOOD, URL_VAR: database_url(), "LLMI_ALLOW_REAL_LLM": "false"}

    def frontend_values(self):
        return {ORIGIN_VAR: check.BACKEND_ORIGIN, API_VAR: GOOD[ADMIN_VAR]}

    def write(self, name, values):
        path = self.tmp / name
        path.write_text(
            "\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8"
        )
        return path

    def run_env(self, env=None, frontend=None, environ=None):
        return check.check_env(
            self.write("env", self.env_values() if env is None else env),
            self.write(
                "frontend", self.frontend_values() if frontend is None else frontend
            ),
            {} if environ is None else environ,
        )


class EnvChecks(Base):
    def test_valid_env_passes(self):
        self.assertEqual(self.run_env(), [])

    def test_e_missing_or_placeholder_token(self):
        for value in ("", "short", "CHANGE_ME"):
            with self.subTest(value):
                frontend = {**self.frontend_values(), API_VAR: value}
                self.assertIn(
                    "FRONTEND_TOKEN_MISSING", kinds(self.run_env(frontend=frontend))
                )
        frontend = {ORIGIN_VAR: check.BACKEND_ORIGIN}
        self.assertIn("FRONTEND_TOKEN_MISSING", kinds(self.run_env(frontend=frontend)))

    def test_token_must_equal_the_backend_token(self):
        frontend = {**self.frontend_values(), API_VAR: "x" * 40}
        self.assertIn("FRONTEND_TOKEN_MISMATCH", kinds(self.run_env(frontend=frontend)))

    def test_d_wrong_backend_origin(self):
        for value in (
            "http://127.0.0.1:8001",
            "http://localhost:8000",
            "https://example.invalid",
        ):
            with self.subTest(value):
                frontend = {**self.frontend_values(), ORIGIN_VAR: value}
                self.assertIn("FRONTEND_ORIGIN", kinds(self.run_env(frontend=frontend)))

    def test_public_variable_is_refused(self):
        frontend = {**self.frontend_values(), "NEXT_PUBLIC_ANYTHING": "x"}
        self.assertIn(
            "FRONTEND_PUBLIC_VARIABLE", kinds(self.run_env(frontend=frontend))
        )

    def test_other_database_is_refused(self):
        for changed in (
            {"host": "db.example.invalid"},
            {"port": 5432},
            {"db": "other"},
            {"user": "other"},
            {"credential": "other-value-0000000000000000"},
        ):
            with self.subTest(changed):
                env = {**self.env_values(), URL_VAR: database_url(**changed)}
                self.assertIn("ENV_DATABASE", kinds(self.run_env(env=env)))

    def test_unexpected_compose_port_is_refused(self):
        env = {**self.env_values(), "POSTGRES_HOST_PORT": "5432"}
        self.assertIn("ENV_DATABASE", kinds(self.run_env(env=env)))

    def test_backend_bind_is_enforced(self):
        for changed in ({"LLMI_BIND_HOST": "192.0.2.1"}, {"LLMI_PORT": "9000"}):
            with self.subTest(changed):
                self.assertIn(
                    "ENV_BACKEND",
                    kinds(self.run_env(env={**self.env_values(), **changed})),
                )

    def test_provider_and_real_llm_are_refused(self):
        env = {**self.env_values(), "OPENAI_API_KEY": "x"}
        self.assertIn("ENV_PROVIDER_VALUE", kinds(self.run_env(env=env)))
        env = {**self.env_values(), "LLMI_ALLOW_REAL_LLM": "true"}
        self.assertIn("ENV_REAL_LLM", kinds(self.run_env(env=env)))

    def test_keep_flag_is_refused(self):
        self.assertIn("ENV_KEEP", kinds(self.run_env(environ={"LLMI_E2E_KEEP": "1"})))

    def test_missing_files_fail(self):
        self.assertEqual(
            kinds(check.check_env(self.tmp / "absent", self.tmp / "absent", {})),
            {"ENV_MISSING"},
        )
        env = self.write("env", self.env_values())
        self.assertEqual(
            kinds(check.check_env(env, self.tmp / "absent", {})),
            {"FRONTEND_ENV_MISSING"},
        )

    def test_values_never_appear_in_problems(self):
        env = {**self.env_values(), URL_VAR: database_url(host="db.example.invalid")}
        found = self.run_env(env=env)
        self.assertNotIn(GOOD[SECRET_VAR], json.dumps(found))


class ComposeChecks(Base):
    def model(self):
        return {
            "name": PROJECT,
            "services": {
                "postgres": {
                    "image": "postgres:17",
                    "ports": ["127.0.0.1:${X:-55432}:5432"],
                },
                "backend": {"profiles": ["app"]},
                "frontend": {"profiles": ["app"]},
            },
        }

    def run_check(self, model, project=PROJECT, active="postgres\n"):
        (self.tmp / "c.json").write_text(json.dumps(model), encoding="utf-8")
        (self.tmp / "s.txt").write_text(active, encoding="utf-8")
        return check.check_compose(self.tmp / "c.json", self.tmp / "s.txt", project)

    def test_valid_model_passes(self):
        self.assertEqual(self.run_check(self.model()), [])

    def test_project_rules(self):
        for bad in (
            "llminsight",
            "llmi-r1044-1",
            "llmi-r1045-",
            "llmi-r1045-abc",
            "feature/x",
            "",
        ):
            with self.subTest(bad):
                self.assertEqual(
                    kinds(self.run_check(self.model(), project=bad)),
                    {"PROJECT_INVALID"},
                )

    def test_effective_name_must_match(self):
        self.assertIn(
            "PROJECT_MISMATCH",
            kinds(self.run_check({**self.model(), "name": "llminsight"})),
        )

    def test_only_postgres_may_be_active(self):
        for active in ("postgres\nbackend\n", "backend\n", ""):
            with self.subTest(active):
                self.assertEqual(
                    kinds(self.run_check(self.model(), active=active)),
                    {"CONFIG_SERVICES"},
                )

    def test_port_must_be_loopback(self):
        anywhere = ".".join("0" * 4)
        for ports in (
            [f"{anywhere}:55432:5432"],
            ["55432:5432"],
            [{"host_ip": anywhere}],
            [],
            None,
            [1],
        ):
            with self.subTest(ports):
                model = self.model()
                model["services"]["postgres"]["ports"] = ports
                self.assertIn("CONFIG_PORT", kinds(self.run_check(model)))

    def test_normalized_port_shape_is_accepted(self):
        model = self.model()
        model["services"]["postgres"]["ports"] = [
            {"host_ip": "127.0.0.1", "target": 5432}
        ]
        self.assertEqual(self.run_check(model), [])

    def test_malformed_output_fails(self):
        (self.tmp / "s.txt").write_text("postgres", encoding="utf-8")
        (self.tmp / "c.json").write_text("{", encoding="utf-8")
        self.assertEqual(
            kinds(
                check.check_compose(self.tmp / "c.json", self.tmp / "s.txt", PROJECT)
            ),
            {"CONFIG_MALFORMED"},
        )
        self.assertEqual(
            kinds(
                check.check_compose(self.tmp / "absent", self.tmp / "s.txt", PROJECT)
            ),
            {"CONFIG_MALFORMED"},
        )
        (self.tmp / "c.json").write_text("{}", encoding="utf-8")
        self.assertEqual(
            kinds(
                check.check_compose(self.tmp / "c.json", self.tmp / "absent", PROJECT)
            ),
            {"CONFIG_MALFORMED"},
        )


class Ports(unittest.TestCase):
    def test_free_and_busy_ports(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            busy = server.getsockname()[1]
            self.assertEqual(kinds(check.check_ports([busy])), {"PORT_BUSY"})
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        self.assertEqual(check.check_ports([free]), [])

    def test_topology_is_the_documented_one(self):
        self.assertEqual(
            set(check.RUN_PORTS), {8001, 8002, 3001, 3002, 3003, 3004, 3005}
        )
        self.assertEqual(set(check.ALL_PORTS), {55432, 8000, *check.RUN_PORTS})


class Redaction(Base):
    def test_every_generated_value_is_replaced(self):
        env = self.write("env", self.env_values())
        needles = check.needles_from(env)
        self.assertIn(GOOD[SECRET_VAR], needles)
        line = (
            f"a {GOOD[SECRET_VAR]} b {GOOD[PEPPER_VAR]} c {GOOD[ADMIN_VAR]}\n".encode()
        )
        sink = io.BytesIO()
        check.redact_stream(io.BytesIO(line), sink, needles)
        out = sink.getvalue().decode()
        for name in (SECRET_VAR, PEPPER_VAR, ADMIN_VAR):
            self.assertNotIn(GOOD[name], out)
        self.assertIn("[REDACTED]", out)

    def test_short_values_are_not_needles(self):
        self.assertEqual(
            check.needles_from(self.write("env", {SECRET_VAR: "short"})), []
        )


class Reports(Base):
    COUNT = check.MIN_TESTS

    def spec(self, i, state="expected", attempts=1, listed=False):
        test = {"status": state, "results": [{}] * attempts}
        if listed:
            test = {"status": "expected", "results": []}
        return {"title": f"test {i}", "line": i, "tests": [test]}

    def document(self, specs, **overrides):
        return {
            "config": {"projects": [{"retries": 0}]},
            "suites": [{"file": "a.spec.ts", "specs": specs, "suites": []}],
            "errors": [],
            "stats": {"expected": len(specs)},
            **overrides,
        }

    def listed(self, count=COUNT):
        return self.document([self.spec(i, listed=True) for i in range(count)])

    def passed(self, count=COUNT):
        return self.document([self.spec(i) for i in range(count)])

    def log(self, remaining=(), deleted=(1, 2, 3, 4, 5), setup=True, teardown=True):
        lines = []
        if setup:
            lines.append(
                f"[e2e] run {RUN}: projects main=1 foreign=2 hostile=3 providers=4"
                " comparison=5 (SYNTHETIC)"
            )
        if teardown:
            body = {"run": RUN, "deleted": list(deleted), "remaining": list(remaining)}
            lines.append("[e2e] teardown: " + json.dumps(body))
        return "\n".join(lines) + "\n"

    def run_check(self, listed=None, report=None, log=None, status=0):
        for name, value in (
            ("list.json", listed if listed is not None else self.listed()),
            ("run.json", report if report is not None else self.passed()),
        ):
            (self.tmp / name).write_text(json.dumps(value), encoding="utf-8")
        (self.tmp / "log").write_text(
            self.log() if log is None else log, encoding="utf-8"
        )
        return check.evaluate_report(
            self.tmp / "list.json", self.tmp / "run.json", self.tmp / "log", status
        )

    def test_complete_green_run_passes(self):
        problems, rows, totals = self.run_check()
        self.assertEqual(problems, [])
        self.assertEqual(len(rows), self.COUNT)
        self.assertEqual(totals["passed"], self.COUNT)

    def test_i_failed_test_fails_even_with_exit_zero(self):
        specs = [self.spec(i) for i in range(self.COUNT)]
        specs[0] = self.spec(0, state="unexpected")
        report = self.document(specs, stats={"expected": self.COUNT - 1})
        self.assertIn("TEST_FAILED", kinds(self.run_check(report=report)[0]))

    def test_i_nonzero_exit_fails_a_clean_looking_report(self):
        self.assertIn("PLAYWRIGHT_FAILED", kinds(self.run_check(status=1)[0]))

    def test_j_timeout_is_reported(self):
        self.assertIn("PLAYWRIGHT_TIMEOUT", kinds(self.run_check(status=124)[0]))

    def test_k_empty_discovery_fails(self):
        self.assertIn(
            "DISCOVERY_EMPTY",
            kinds(self.run_check(listed=self.listed(0), report=self.passed(0))[0]),
        )

    def test_k_too_few_tests_fail(self):
        count = self.COUNT - 1
        self.assertIn(
            "DISCOVERY_TOO_FEW",
            kinds(
                self.run_check(listed=self.listed(count), report=self.passed(count))[0]
            ),
        )

    def test_k_fewer_executed_than_discovered_fail(self):
        self.assertIn(
            "EXECUTION_MISMATCH",
            kinds(self.run_check(report=self.passed(self.COUNT - 1))[0]),
        )

    def test_a_replaced_test_fails_with_the_same_count(self):
        report = self.passed()
        report["suites"][0]["specs"][0]["title"] = "another test"
        self.assertIn("EXECUTION_MISMATCH", kinds(self.run_check(report=report)[0]))

    def test_n_skipped_test_fails(self):
        specs = [self.spec(i) for i in range(self.COUNT)]
        specs[1] = self.spec(1, state="skipped")
        report = self.document(specs, stats={"expected": self.COUNT - 1})
        self.assertIn("TEST_SKIPPED", kinds(self.run_check(report=report)[0]))

    def test_flaky_test_fails(self):
        specs = [self.spec(i) for i in range(self.COUNT)]
        specs[2] = self.spec(2, state="flaky", attempts=2)
        report = self.document(specs, stats={"expected": self.COUNT - 1})
        self.assertIn("TEST_FLAKY", kinds(self.run_check(report=report)[0]))

    def test_retries_are_refused(self):
        report = self.passed()
        report["config"]["projects"][0]["retries"] = 1
        self.assertIn("RETRIES_ENABLED", kinds(self.run_check(report=report)[0]))
        report["config"]["projects"] = []
        self.assertIn("RETRIES_ENABLED", kinds(self.run_check(report=report)[0]))

    def test_second_attempt_counts_as_failure(self):
        specs = [self.spec(i) for i in range(self.COUNT)]
        specs[3] = self.spec(3, attempts=2)
        report = self.document(specs, stats={"expected": self.COUNT - 1})
        self.assertIn("TEST_FAILED", kinds(self.run_check(report=report)[0]))

    def test_inconsistent_stats_fail(self):
        report = self.document(
            [self.spec(i) for i in range(self.COUNT)], stats={"expected": 1}
        )
        self.assertIn("REPORT_INCONSISTENT", kinds(self.run_check(report=report)[0]))

    def test_global_error_fails(self):
        report = self.document(
            [self.spec(i) for i in range(self.COUNT)], errors=[{"message": "x"}]
        )
        self.assertIn("GLOBAL_ERROR", kinds(self.run_check(report=report)[0]))

    def test_l_fixture_setup_is_required(self):
        self.assertIn(
            "FIXTURE_SETUP", kinds(self.run_check(log=self.log(setup=False))[0])
        )

    def test_m_teardown_is_required(self):
        self.assertIn(
            "FIXTURE_TEARDOWN", kinds(self.run_check(log=self.log(teardown=False))[0])
        )

    def test_m_remaining_project_fails(self):
        self.assertIn(
            "FIXTURE_TEARDOWN", kinds(self.run_check(log=self.log(remaining=[9]))[0])
        )

    def test_m_partial_delete_fails(self):
        self.assertIn(
            "FIXTURE_TEARDOWN", kinds(self.run_check(log=self.log(deleted=[1, 2]))[0])
        )

    def teardown_problems(self, body, setup=True):
        """Fixture problems for one teardown line (a dict is JSON-encoded, a str is raw)."""
        text = body if isinstance(body, str) else json.dumps(body)
        log = self.log(setup=setup, teardown=False) + "[e2e] teardown: " + text + "\n"
        return [
            p for p in self.run_check(log=log)[0] if p["kind"].startswith("FIXTURE_")
        ]

    def test_k_minimum_is_the_committed_suite(self):
        # 72 tests after Phase 8A-6 (62 + phase8a6.spec.ts). Lowering it hides lost tests.
        self.assertEqual(check.MIN_TESTS, 72)

    def test_m_exactly_five_distinct_projects_pass(self):
        self.assertEqual(check.OWNED_PROJECTS, 5)
        body = {"run": RUN, "deleted": [17, 3, 42, 8, 1001], "remaining": []}
        self.assertEqual(self.teardown_problems(body), [])

    def test_m_four_or_six_deleted_projects_fail(self):
        for deleted in ([1, 2, 3, 4], [1, 2, 3, 4, 5, 6], []):
            with self.subTest(deleted=deleted):
                body = {"run": RUN, "deleted": deleted, "remaining": []}
                self.assertEqual(
                    kinds(self.teardown_problems(body)), {"FIXTURE_TEARDOWN"}
                )

    def test_m_repeated_or_invalid_project_ids_fail(self):
        for deleted in (
            [1, 1, 2, 3, 4],  # five entries, four projects
            [1, 2, 3, 4, "5"],
            [1, 2, 3, 4, 5.0],
            [True, 2, 3, 4, 5],  # a bool is not a project id (and not a duplicate here)
            [0, 1, 2, 3, 4],
            [-1, 1, 2, 3, 4],
            [1, 2, 3, 4, None],
        ):
            with self.subTest(deleted=deleted):
                body = {"run": RUN, "deleted": deleted, "remaining": []}
                self.assertEqual(
                    kinds(self.teardown_problems(body)), {"FIXTURE_TEARDOWN"}
                )

    def test_m_remaining_projects_fail_even_after_a_complete_delete(self):
        for remaining in ([6], [1], None, "[]", {}):
            with self.subTest(remaining=remaining):
                body = {"run": RUN, "deleted": [1, 2, 3, 4, 5], "remaining": remaining}
                self.assertEqual(
                    kinds(self.teardown_problems(body)), {"FIXTURE_TEARDOWN"}
                )
        body = {"run": RUN, "deleted": [1, 2, 3, 4, 5]}  # no `remaining` reported
        self.assertEqual(kinds(self.teardown_problems(body)), {"FIXTURE_TEARDOWN"})

    def test_m_wrong_or_missing_run_ids_fail(self):
        for run in ("000000000000", RUN.upper(), RUN[:-1], "", None, 1):
            with self.subTest(run=run):
                body = {"run": run, "deleted": [1, 2, 3, 4, 5], "remaining": []}
                self.assertEqual(
                    kinds(self.teardown_problems(body)), {"FIXTURE_TEARDOWN"}
                )
        body = {"deleted": [1, 2, 3, 4, 5], "remaining": []}
        self.assertEqual(kinds(self.teardown_problems(body)), {"FIXTURE_TEARDOWN"})

    def test_m_malformed_teardown_reports_fail(self):
        for text in (
            '{"run": "' + RUN + '", "deleted": [1, 2, 3, 4, 5], "remaining": [}',
            "{not json}",
            '{"run": "' + RUN + '", "deleted": 5, "remaining": []}',
            '{"run": "' + RUN + '", "deleted": {"1": 1}, "remaining": []}',
            '{"run": "' + RUN + '", "deleted": "1,2,3,4,5", "remaining": []}',
            "[1, 2, 3, 4, 5]",
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    kinds(self.teardown_problems(text)), {"FIXTURE_TEARDOWN"}
                )

    def test_m_teardown_reported_twice_fails(self):
        line = "[e2e] teardown: " + json.dumps(
            {"run": RUN, "deleted": [1, 2, 3, 4, 5], "remaining": []}
        )
        log = self.log(teardown=False) + line + "\n" + line + "\n"
        self.assertIn("FIXTURE_TEARDOWN", kinds(self.run_check(log=log)[0]))

    def test_l_setup_reported_twice_or_malformed_fails(self):
        setup = self.log(teardown=False)
        teardown = self.log(setup=False)
        for log in (
            setup + setup + teardown,
            "[e2e] run ABCDEFABCDEF: projects main=1 (SYNTHETIC)\n" + teardown,
            f"[e2e] run {RUN}: projects main=1\n" + teardown,
        ):
            with self.subTest(log=log.splitlines()[0]):
                self.assertIn("FIXTURE_SETUP", kinds(self.run_check(log=log)[0]))

    def test_kept_data_fails(self):
        log = (
            self.log(teardown=False)
            + f'[e2e] teardown: {{"run": "{RUN}", "kept": [1, 2, 3, 4, 5]}}\n'
        )
        self.assertIn("FIXTURE_TEARDOWN", kinds(self.run_check(log=log)[0]))

    def test_kept_flag_fails_even_with_a_complete_delete(self):
        body = {"run": RUN, "deleted": [1, 2, 3, 4, 5], "remaining": [], "kept": [1]}
        log = self.log(teardown=False) + "[e2e] teardown: " + json.dumps(body) + "\n"
        self.assertIn("FIXTURE_TEARDOWN", kinds(self.run_check(log=log)[0]))

    def test_teardown_of_another_run_fails(self):
        log = (
            self.log(teardown=False)
            + '[e2e] teardown: {"run": "000000000000", "deleted": [1, 2, 3, 4, 5],'
            ' "remaining": []}\n'
        )
        self.assertIn("FIXTURE_TEARDOWN", kinds(self.run_check(log=log)[0]))

    def test_missing_or_malformed_reports_fail(self):
        for name, kind in (
            ("list.json", "DISCOVERY_MISSING"),
            ("run.json", "REPORT_MISSING"),
        ):
            with self.subTest(name):
                self.run_check()
                (self.tmp / name).unlink()
                found = check.evaluate_report(
                    self.tmp / "list.json", self.tmp / "run.json", self.tmp / "log", 0
                )[0]
                self.assertIn(kind, kinds(found))
        self.run_check()
        (self.tmp / "run.json").write_text("[]", encoding="utf-8")
        found = check.evaluate_report(
            self.tmp / "list.json", self.tmp / "run.json", self.tmp / "log", 0
        )[0]
        self.assertIn("REPORT_MALFORMED", kinds(found))

    def test_missing_log_fails(self):
        self.run_check()
        (self.tmp / "log").unlink()
        found = check.evaluate_report(
            self.tmp / "list.json", self.tmp / "run.json", self.tmp / "log", 0
        )[0]
        self.assertIn("LOG_MISSING", kinds(found))

    def test_nested_suites_are_counted(self):
        specs = [self.spec(i) for i in range(self.COUNT)]
        report = self.document([])
        report["stats"] = {"expected": self.COUNT}
        report["suites"] = [
            {"file": "a.spec.ts", "specs": [], "suites": [{"specs": specs}]}
        ]
        self.assertEqual(self.run_check(report=report)[0], [])

    def test_summary_is_sanitized_and_complete(self):
        needle = GOOD[SECRET_VAR]
        rows = [
            {
                "file": "a.spec.ts",
                "title": f"x {needle}\n::error::y",
                "status": "failed",
            }
        ]
        problems = [check.problem("TEST_FAILED", "1 tests")]
        self.assertEqual(
            check.write_summary(
                self.tmp / "out", problems, rows, {"failed": 1}, [needle]
            ),
            [],
        )
        text = (self.tmp / "out/summary.json").read_text(encoding="utf-8")
        self.assertNotIn(needle, text)
        self.assertNotIn("::", json.loads(text)["tests"][0]["title"])
        self.assertEqual(
            sorted(p.name for p in (self.tmp / "out").iterdir()),
            ["summary.json", "summary.md"],
        )

    def test_cli_writes_a_summary_and_fails_without_printing_values(self):
        problems_status = self.run_check(status=1)[0]
        self.assertTrue(problems_status)
        env = self.write("env", self.env_values())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check.main(
                [
                    "report",
                    "--list",
                    str(self.tmp / "list.json"),
                    "--report",
                    str(self.tmp / "run.json"),
                    "--log",
                    str(self.tmp / "log"),
                    "--exit-status",
                    "1",
                    "--env",
                    str(env),
                    "--out-dir",
                    str(self.tmp / "s"),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("PLAYWRIGHT_FAILED", output.getvalue())
        self.assertNotIn(GOOD[SECRET_VAR], output.getvalue())
        self.assertTrue((self.tmp / "s/summary.json").exists())


class E2eInputs(unittest.TestCase):
    def text(self, rel):
        return (REPO / rel).read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_playwright_config_keeps_its_safety_properties(self):
        text = self.text("frontend/playwright.config.ts")
        self.assertEqual(text.count("reuseExistingServer: false"), 7)
        self.assertNotIn("reuseExistingServer: true", text)
        self.assertRegex(text, r"\bworkers: 1,")
        self.assertRegex(text, r"\bretries: 0,")
        self.assertIn('trace: "off"', text)
        self.assertIn("...e2eBrowser(process.env)", text)
        for needle in (
            "video",
            "screenshot:",
            "route(",
            'channel: "msedge",\n    headless',
        ):
            with self.subTest(needle):
                self.assertNotIn(needle, text)

    def test_topology_matches_the_config(self):
        text = self.text("frontend/playwright.config.ts")
        ports = {
            int(item) for item in re.findall(r"http://127\.0\.0\.1:(\d{4})/", text)
        }
        self.assertEqual(ports, set(check.RUN_PORTS))

    def test_owned_projects_match_the_fixture_roles(self):
        """The gate's count is the fixture's own: one role per run-owned project in
        `e2e_fixtures.names()`, each printed by the Playwright global setup."""
        import ast

        tree = ast.parse(self.text("scripts/dev/e2e_fixtures.py"))
        (names,) = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "names"
        ]
        (returned,) = [n.value for n in ast.walk(names) if isinstance(n, ast.Return)]
        self.assertIsInstance(returned, ast.Dict)
        roles = [k.value for k in returned.keys]
        self.assertEqual(len(set(roles)), len(roles))
        self.assertEqual(len(roles), check.OWNED_PROJECTS)
        setup = self.text("frontend/tests/setup/e2e-global-setup.ts")
        for role in roles:
            with self.subTest(role):
                self.assertIn(f"{role}=${{result.projects.{role}}}", setup)

    def test_no_test_mocks_the_backend_or_is_focused(self):
        for path in sorted((REPO / "frontend/tests/e2e").glob("*.spec.ts")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.name):
                # phase4d captures and aborts one request with page.route; it never fakes a response.
                self.assertNotRegex(text, r"\.fulfill\(|\.only\(")

    def job(self):
        text = self.text(".github/workflows/ci.yml")
        start = text.index("\n  e2e:\n")
        return text[start : text.index("\n  image-build:\n", start)]

    def test_workflow_job_shape(self):
        text = self.text(".github/workflows/ci.yml")
        jobs = re.findall(r"(?m)^  ([a-z0-9-]+):$", text.split("\njobs:\n", 1)[1])
        self.assertEqual(
            jobs,
            [
                "docs",
                "python",
                "frontend",
                "security-python",
                "security-history",
                "e2e",
                "image-build",
            ],
        )
        job = self.job()
        self.assertIn("runs-on: ubuntu-24.04", job)
        self.assertRegex(job, r"timeout-minutes: \d+")
        self.assertRegex(job, r"permissions:\n\s+contents: read\n")
        self.assertIn("persist-credentials: false", job)
        self.assertEqual(
            re.findall(r"uses: (\S+)", job),
            [
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
                "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1",
                "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
            ],
        )
        self.assertEqual(
            re.findall(r"if: (.+)", job), ["${{ !cancelled() }}", "${{ always() }}"]
        )
        for needle in (
            "continue-on-error",
            "secrets.",
            "docker compose",
            "LLMI_E2E_KEEP",
        ):
            with self.subTest(needle):
                self.assertNotIn(needle, job)

    def test_every_phase_runs_in_order(self):
        phases = re.findall(r"scripts/qa/e2e_gate\.sh (\w+)", self.job())
        self.assertEqual(
            phases,
            [
                "prepare",
                "install",
                "database",
                "backend",
                "frontend",
                "test",
                "cleanup",
            ],
        )

    def test_upload_holds_only_the_summary(self):
        self.assertIn("path: ${{ runner.temp }}/e2e-summary", self.job())
        self.assertEqual(self.job().count("path:"), 1)


if __name__ == "__main__":
    unittest.main()
