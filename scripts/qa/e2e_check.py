"""Checks for the CI Playwright E2E gate (R104-5). Stdlib only; runs no process and no Docker command.

scripts/qa/e2e_gate.sh runs the stack on the GitHub runner and hands its outputs to this file:

  env      the throwaway env files point at the loopback backend and the isolated database
  compose  the effective Compose model: run-specific project, PostgreSQL only, loopback port
  ports    the ports of the E2E topology are free
  redact   filters a stream, replacing the generated values
  report   the Playwright JSON report: every discovered test ran and passed, none was skipped

Every failure exits 1 and prints only a fixed kind and a sanitized detail, never a value.
"""

import argparse
import importlib.util
import json
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

_spec = importlib.util.spec_from_file_location(
    "image_build_check", Path(__file__).resolve().with_name("image_build_check.py")
)
ibc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ibc)

problem = ibc.problem

PROJECT = re.compile(r"llmi-r1045-[0-9]{1,20}\Z")
BACKEND_ORIGIN = "http://127.0.0.1:8000"
DATABASE_PORT = 55432
RUN_PORTS = (8001, 8002, 3001, 3002, 3003, 3004, 3005)
ALL_PORTS = (DATABASE_PORT, 8000, *RUN_PORTS)
# The suite as committed. A lower number means tests disappeared; raise it when tests are added.
MIN_TESTS = 62
SCHEMA_SUMMARY = "llm-insight/e2e-summary/v1"
SECRET_ENV = ibc.GENERATED_VALUES
SETUP_LINE = re.compile(r"\[e2e\] run ([0-9a-f]{12}): projects .*\(SYNTHETIC\)")
TEARDOWN_LINE = re.compile(r"\[e2e\] teardown: (\{.*\})")
OWNED_PROJECTS = 4
MAX_ANNOTATED = 20
LIMITATIONS = [
    "Chromium bundled with Playwright is not the Microsoft Edge used by the local default.",
    "Only synthetic fixture data is used; no real provider is called.",
    "The suite covers the behaviours it asserts and is not proof of absent defects.",
]


def read_env(path):
    return ibc.read_env(path)


def ibc_placeholder():
    return "CHANGE" + "_ME"


def report_problems(problems, success):
    for item in problems:
        print(f"::error title=e2e::{item['kind']} {item['detail']}")
    if problems:
        return 1
    print(success)
    return 0


def check_env(env_path, frontend_path, environ=None):
    environ = os.environ if environ is None else environ
    problems = ibc.check_env(env_path, environ)
    if any(p["kind"] == "ENV_MISSING" for p in problems):
        return problems
    values = read_env(env_path)
    if environ.get("LLMI_E2E_KEEP"):
        problems.append(problem("ENV_KEEP", "LLMI_E2E_KEEP must not be set"))
    problems += check_database(values)
    if values.get("LLMI_BIND_HOST", "127.0.0.1") != "127.0.0.1":
        problems.append(problem("ENV_BACKEND", "backend bind host must be loopback"))
    if values.get("LLMI_PORT", "8000") != "8000":
        problems.append(problem("ENV_BACKEND", "backend port must be 8000"))
    try:
        frontend = read_env(frontend_path)
    except (OSError, UnicodeDecodeError):
        return problems + [
            problem("FRONTEND_ENV_MISSING", "frontend env cannot be read")
        ]
    return problems + check_frontend(frontend, values)


def check_database(values):
    try:
        url = urlsplit(values.get("LLMI_DATABASE_URL", ""))
        port = url.port
    except ValueError:
        return [problem("ENV_DATABASE", "database URL cannot be parsed")]
    if (
        url.scheme != "postgresql+psycopg"
        or url.hostname != "127.0.0.1"
        or port != DATABASE_PORT
        or url.path != "/" + values.get("POSTGRES_DB", "")
        or url.username != values.get("POSTGRES_USER")
        or url.password != values.get("POSTGRES_PASSWORD")
    ):
        return [problem("ENV_DATABASE", "database URL is not the isolated database")]
    if values.get("POSTGRES_HOST_PORT", str(DATABASE_PORT)) != str(DATABASE_PORT):
        return [problem("ENV_DATABASE", "unexpected PostgreSQL host port")]
    return []


def check_frontend(frontend, values):
    problems = []
    if frontend.get("LLMI_API_BASE_URL") != BACKEND_ORIGIN:
        problems.append(
            problem("FRONTEND_ORIGIN", "the dashboard must use the loopback backend")
        )
    server_value = frontend.get("LLMI_API_TOKEN", "")
    if len(server_value) < 32 or server_value == ibc_placeholder():
        problems.append(problem("FRONTEND_TOKEN_MISSING", "server-side token missing"))
    elif server_value != values.get("LLMI_ADMIN_TOKEN"):
        problems.append(
            problem("FRONTEND_TOKEN_MISMATCH", "token differs from the backend")
        )
    if any(key.startswith("NEXT_PUBLIC_") for key in frontend):
        problems.append(
            problem("FRONTEND_PUBLIC_VARIABLE", "NEXT_PUBLIC_ variable present")
        )
    return problems


def check_compose(config_path, services_path, project):
    """`--no-interpolate` lists every service and keeps short ports; `--services` is the active set."""
    if not PROJECT.match(project):
        return [problem("PROJECT_INVALID", "not a run-specific CI project name")]
    try:
        model = json.loads(Path(config_path).read_bytes())
        active = Path(services_path).read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return [problem("CONFIG_MALFORMED", "the Compose output cannot be parsed")]
    if not isinstance(model, dict) or not isinstance(model.get("services"), dict):
        return [problem("CONFIG_MALFORMED", "no services in the Compose output")]
    problems = []
    if model.get("name") != project:
        problems.append(problem("PROJECT_MISMATCH", "effective project name differs"))
    if active != ["postgres"] or "postgres" not in model["services"]:
        problems.append(problem("CONFIG_SERVICES", "only PostgreSQL may be active"))
        return problems
    ports = model["services"]["postgres"].get("ports")
    if not isinstance(ports, list) or len(ports) != 1 or not loopback_port(ports[0]):
        problems.append(
            problem("CONFIG_PORT", "PostgreSQL must publish one loopback port")
        )
    return problems


def loopback_port(item):
    if isinstance(item, dict):
        return item.get("host_ip") == "127.0.0.1"
    return isinstance(item, str) and item.startswith("127.0.0.1:")


def check_ports(ports):
    problems = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                problems.append(problem("PORT_BUSY", str(port)))
    return problems


def needles_from(*env_paths):
    found = set()
    for path in env_paths:
        values = read_env(path)
        for name in SECRET_ENV:
            if len(values.get(name, "")) >= 16:
                found.add(values[name])
    return sorted(found, key=len, reverse=True)


def redact_text(text, needles):
    for needle in needles:
        text = text.replace(needle, "[REDACTED]")
    return text


def redact_stream(source, sink, needles):
    for raw in source:
        sink.write(redact_text(raw.decode("utf-8", "replace"), needles).encode("utf-8"))
        sink.flush()


def iter_tests(suites, inherited=""):
    for suite in suites or []:
        file_name = suite.get("file") or inherited
        for spec in suite.get("specs") or []:
            for test in spec.get("tests") or []:
                yield file_name, spec, test
        yield from iter_tests(suite.get("suites"), file_name)


def load_json(path, label):
    try:
        data = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return None, [problem(label + "_MISSING", "the report cannot be read")]
    if not isinstance(data, dict) or not isinstance(data.get("suites"), list):
        return None, [problem(label + "_MALFORMED", "unexpected report shape")]
    return data, []


def retries_of(data):
    projects = (data.get("config") or {}).get("projects") or []
    return [p.get("retries") for p in projects if isinstance(p, dict)]


def identity(file_name, spec):
    return (str(file_name), str(spec.get("title")), spec.get("line"))


def check_lifecycle(log_path):
    """The fixture setup ran and the teardown removed exactly this run's projects."""
    try:
        lines = (
            Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        )
    except OSError:
        return [problem("LOG_MISSING", "the test log cannot be read")]
    runs = [m.group(1) for m in (SETUP_LINE.search(line) for line in lines) if m]
    if len(runs) != 1:
        return [problem("FIXTURE_SETUP", "the fixture setup did not report one run")]
    found = [m.group(1) for m in (TEARDOWN_LINE.search(line) for line in lines) if m]
    if len(found) != 1:
        return [problem("FIXTURE_TEARDOWN", "the fixture teardown did not report once")]
    try:
        result = json.loads(found[0])
    except ValueError:
        return [problem("FIXTURE_TEARDOWN", "the teardown output cannot be parsed")]
    if (
        not isinstance(result, dict)
        or result.get("run") != runs[0]
        or result.get("remaining") != []
        or not isinstance(result.get("deleted"), list)
        or len(result["deleted"]) != OWNED_PROJECTS
        or "kept" in result
    ):
        return [problem("FIXTURE_TEARDOWN", "the run's projects were not all removed")]
    return []


def evaluate_report(list_path, report_path, log_path, exit_status):
    """Returns (problems, rows, totals)."""
    problems = []
    if exit_status != 0:
        kind = "PLAYWRIGHT_TIMEOUT" if exit_status == 124 else "PLAYWRIGHT_FAILED"
        problems.append(problem(kind, f"exit status {exit_status}"))
    listed, found = load_json(list_path, "DISCOVERY")
    problems += found
    data, found = load_json(report_path, "REPORT")
    problems += found
    if listed is None or data is None:
        return problems + check_lifecycle(log_path), [], {}
    expected_ids = sorted(identity(f, s) for f, s, _t in iter_tests(listed["suites"]))
    if not expected_ids:
        problems.append(problem("DISCOVERY_EMPTY", "no test was discovered"))
    elif len(expected_ids) < MIN_TESTS:
        problems.append(
            problem(
                "DISCOVERY_TOO_FEW",
                f"{len(expected_ids)} discovered, at least {MIN_TESTS}",
            )
        )
    rows, seen, counts = [], [], {"passed": 0, "failed": 0, "skipped": 0, "flaky": 0}
    for file_name, spec, test in iter_tests(data["suites"]):
        seen.append(identity(file_name, spec))
        state = test.get("status")
        attempts = len(test.get("results") or [])
        if state == "expected" and attempts == 1:
            status = "passed"
        elif state == "skipped":
            status = "skipped"
        elif state == "flaky":
            status = "flaky"
        else:
            status = "failed"
        counts[status] += 1
        rows.append(
            {"file": str(file_name), "title": str(spec.get("title")), "status": status}
        )
    if sorted(seen) != expected_ids:
        problems.append(
            problem("EXECUTION_MISMATCH", "executed tests differ from discovered")
        )
    for label, count in (
        ("TEST_FAILED", counts["failed"]),
        ("TEST_SKIPPED", counts["skipped"]),
        ("TEST_FLAKY", counts["flaky"]),
    ):
        if count:
            problems.append(problem(label, f"{count} tests"))
    if data.get("errors"):
        problems.append(
            problem("GLOBAL_ERROR", f"{len(data['errors'])} errors outside tests")
        )
    stats = data.get("stats")
    if not isinstance(stats, dict) or stats.get("expected") != counts["passed"]:
        problems.append(
            problem("REPORT_INCONSISTENT", "stats differ from the test results")
        )
    if any(value != 0 for value in retries_of(data)) or not retries_of(data):
        problems.append(problem("RETRIES_ENABLED", "retries must be zero"))
    problems += check_lifecycle(log_path)
    return problems, rows, {"discovered": len(expected_ids), **counts}


def clean_label(text, needles):
    return problem("X", redact_text(text, needles))["detail"]


def write_summary(out_dir, problems, rows, totals, needles):
    verdict = "PASS" if not problems else "FAIL"
    summary = {
        "schema": SCHEMA_SUMMARY,
        "verdict": verdict,
        "browser": "chromium (bundled with Playwright)",
        "totals": totals,
        "problems": problems,
        "tests": [
            {
                "file": clean_label(row["file"], needles),
                "title": clean_label(row["title"], needles),
                "status": row["status"],
            }
            for row in rows
        ],
        "limitations": LIMITATIONS,
    }
    text = json.dumps(summary, indent=2) + "\n"
    if any(needle in text for needle in needles):
        return [problem("SUMMARY_UNSAFE", "the summary held a generated value")]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(text, encoding="utf-8")
    lines = [
        "# Playwright E2E gate",
        "",
        f"- Verdict: **{verdict}**",
        f"- Browser: {summary['browser']}",
        f"- Totals: {totals}",
        f"- Problems: {len(problems)}",
        "",
    ]
    lines += [f"- `{item['kind']}` {item['detail']}" for item in problems]
    lines += ["", "Known limitations:"] + [f"- {item}" for item in LIMITATIONS] + [""]
    body = "\n".join(lines)
    (out / "summary.md").write_text(body, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(body)
    return []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    env = sub.add_parser("env")
    env.add_argument("--env", required=True)
    env.add_argument("--frontend-env", required=True)
    compose = sub.add_parser("compose")
    compose.add_argument("--config", required=True)
    compose.add_argument("--services", required=True)
    compose.add_argument("--project", required=True)
    ports = sub.add_parser("ports")
    ports.add_argument("--set", choices=("all", "run"), required=True)
    redact = sub.add_parser("redact")
    redact.add_argument("--env", action="append", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--list", required=True)
    rep.add_argument("--report", required=True)
    rep.add_argument("--log", required=True)
    rep.add_argument("--exit-status", type=int, required=True)
    rep.add_argument("--env", action="append", required=True)
    rep.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "env":
        return report_problems(
            check_env(args.env, args.frontend_env), "env: isolated targets verified"
        )
    if args.command == "compose":
        return report_problems(
            check_compose(args.config, args.services, args.project),
            "compose: project and service verified",
        )
    if args.command == "ports":
        chosen = ALL_PORTS if args.set == "all" else RUN_PORTS
        return report_problems(check_ports(chosen), f"ports: {len(chosen)} free")
    if args.command == "redact":
        try:
            needles = needles_from(*args.env)
        except (OSError, UnicodeDecodeError):
            print("::error title=e2e::REDACT_ENV the env files cannot be read")
            return 1
        redact_stream(sys.stdin.buffer, sys.stdout.buffer, needles)
        return 0
    try:
        needles = needles_from(*args.env)
    except (OSError, UnicodeDecodeError):
        needles = None
    if needles is None:
        return report_problems(
            [problem("REDACT_ENV", "the env files cannot be read")], ""
        )
    problems, rows, totals = evaluate_report(
        args.list, args.report, args.log, args.exit_status
    )
    problems = [
        {**item, "detail": clean_label(item["detail"], needles)} for item in problems
    ]
    problems += write_summary(args.out_dir, problems, rows, totals, needles)
    failed = [row for row in rows if row["status"] != "passed"][:MAX_ANNOTATED]
    for row in failed:
        label = clean_label(f"{row['file']} {row['title']}", needles)
        print(f"::error title=e2e::{row['status']} {label}")
    return report_problems(problems, f"e2e: PASS, {totals}")


if __name__ == "__main__":
    sys.exit(main())
