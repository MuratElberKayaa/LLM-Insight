#!/usr/bin/env bash
# Offline negative tests of scripts/qa/e2e_gate.sh (R104-5).
#
# Runs the real gate against FAKE docker, curl, npm, npx, node, sleep and timeout placed first on
# PATH, in a disposable directory. No Docker daemon, database, browser or network is contacted:
# the run aborts unless docker resolves to the fake.
#
# Run: bash scripts/qa/tests/test_e2e_gate.sh   (LLMI_REAL_PYTHON=python if `python` differs)
# shellcheck disable=SC2030,SC2031,SC2016  # cases set their own environment in subshells; sed text is literal
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export LLMI_REAL_PYTHON="${LLMI_REAL_PYTHON:-python}"
WORK="$(mktemp -d)"
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"
export FAKE_BIN WORK LLMI_PYTHON="$FAKE_BIN/py"

stop_backend() {
  local file="$WORK/temp/e2e/backend.pid" pid attempt
  if [ -s "$file" ]; then
    pid="$(cat "$file")"
    kill -9 "$pid" > /dev/null 2>&1
    for ((attempt = 0; attempt < 100; attempt++)); do
      kill -0 "$pid" > /dev/null 2>&1 || break
      /usr/bin/sleep 0.05
    done
  fi
  return 0
}

finish() {
  stop_backend
  rm -rf "$WORK"
}
trap finish EXIT

cat > "$FAKE_BIN/mk_report.py" <<'PY'
"""Writes a synthetic Playwright JSON report: 72 tests unless the FAKE_* variables say otherwise."""
import json, os, sys
target, listed = sys.argv[1], sys.argv[2] == "list"
get = os.environ.get
count = int(get("FAKE_LIST_COUNT", "72")) if listed else int(get("FAKE_RUN_COUNT", get("FAKE_LIST_COUNT", "72")))
specs = []
expected = 0
for i in range(count):
    status, attempts = "expected", 1
    if not listed and i == 0 and get("FAKE_RUN_FAIL"):
        status = "unexpected"
    if not listed and i == 1 and get("FAKE_RUN_SKIP"):
        status = "skipped"
    if not listed and i == 2 and get("FAKE_RUN_FLAKY"):
        status, attempts = "flaky", 2
    expected += status == "expected"
    specs.append({"title": f"synthetic test {i}", "line": i + 1, "tests": [{"status": status, "results": [{}] * (0 if listed else attempts)}]})
report = {
    "config": {"projects": [{"retries": int(get("FAKE_RETRIES", "0"))}]},
    "suites": [{"file": "synthetic.spec.ts", "specs": specs, "suites": []}],
    "errors": [{"message": "global"}] if get("FAKE_GLOBAL_ERROR") and not listed else [],
    "stats": {"expected": expected},
}
if get("FAKE_NO_REPORT") and not listed:
    sys.exit(0)
with open(target, "w", encoding="utf-8") as handle:
    json.dump(report, handle)
PY

cat > "$FAKE_BIN/py" <<'SH'
#!/usr/bin/env bash
# Stands in for `python`: venv creation and the port probe are faked, everything else is real.
printf 'py %s\n' "$*" >> "$FAKE_LOG"
if [ "${1:-} ${2:-}" = "-m venv" ]; then
  [ -z "${FAKE_VENV_FAIL:-}" ] || exit 21
  mkdir -p "$3/bin"
  cp "$FAKE_BIN/venv_python" "$3/bin/python"
  chmod +x "$3/bin/python"
  exit 0
fi
if [ "${1:-}" = "-c" ] && [ -n "${FAKE_PY_VERSION:-}" ]; then echo "$FAKE_PY_VERSION"; exit 0; fi
if [ "${3:-}" = "ports" ]; then
  if [ -n "${FAKE_PORT_BUSY:-}" ] && [ "${5:-}" = "$FAKE_PORT_BUSY" ]; then
    echo "::error title=e2e::PORT_BUSY 3001"; exit 1
  fi
  exit 0
fi
"$LLMI_REAL_PYTHON" "$@"
status=$?
case "${2:-}" in
  scripts/dev/init_frontend_env.py)
    if [ -n "${FAKE_TAMPER_FRONTEND:-}" ]; then sed -i "$FAKE_TAMPER_FRONTEND" frontend/.env.local; fi ;;
  scripts/dev/init_env.py)
    if [ -n "${FAKE_TAMPER_ENV:-}" ]; then sed -i "$FAKE_TAMPER_ENV" .env; fi ;;
esac
exit $status
SH

cat > "$FAKE_BIN/venv_python" <<'SH'
#!/usr/bin/env bash
printf 'venv %s\n' "$*" >> "$FAKE_LOG"
case "$1 ${2:-} ${3:-}" in
  "-m pip install") [ -z "${FAKE_PIP_FAIL:-}" ] || exit 22; exit 0 ;;
  "-m pip check") [ -z "${FAKE_PIP_CHECK_FAIL:-}" ] || exit 23; exit 0 ;;
  "-m alembic upgrade") [ -z "${FAKE_ALEMBIC_FAIL:-}" ] || exit 24; exit 0 ;;
  "-m alembic check") [ -z "${FAKE_ALEMBIC_DRIFT:-}" ] || exit 25; exit 0 ;;
  "-m app.cli seed-prices") [ -z "${FAKE_SEED_FAIL:-}" ] || exit 26; exit 0 ;;
esac
if [ "$1 ${2:-}" = "-m app" ]; then
  [ -z "${FAKE_BACKEND_DIES:-}${FAKE_BACKEND_EXITS:-}" ] || exit 27
  while :; do /usr/bin/sleep 0.2; done
fi
exit 99
SH

cat > "$FAKE_BIN/docker" <<'SH'
#!/usr/bin/env bash
set -u
printf 'docker %s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  version) echo 28.0.0; exit 0 ;;
  compose)
    shift
    if [ "${1:-}" = "version" ]; then echo 2.40.0; exit 0; fi
    [ "${1:-}" = "--project-name" ] || exit 64
    project=$2; shift 2
    case "$1 ${2:-}" in
      "config --quiet") [ -z "${FAKE_CONFIG_FAIL:-}" ] || exit 15; exit 0 ;;
      "config --no-interpolate")
        if [ -n "${FAKE_JSON_BAD:-}" ]; then echo '{'; exit 0; fi
        printf '{"name":"%s","services":{"postgres":{"ports":["%s:55432:5432"]},"backend":{"profiles":["app"]},"frontend":{"profiles":["app"]}}}\n' "${FAKE_NAME:-$project}" "${FAKE_HOST_IP:-127.0.0.1}"
        exit 0 ;;
      "config --services")
        echo postgres
        [ -z "${FAKE_EXTRA_SERVICE:-}" ] || echo backend
        exit 0 ;;
      "up -d")
        [ "$*" = "up -d --wait --wait-timeout 120 postgres" ] || exit 64
        [ -z "${FAKE_UP_FAIL:-}" ] || exit 16
        exit 0 ;;
      "ps -q") [ -n "${FAKE_NO_CONTAINER:-}" ] || echo pgid; exit 0 ;;
      "down -v")
        [ "$*" = "down -v --remove-orphans --timeout 30" ] || exit 64
        [ -z "${FAKE_DOWN_FAIL:-}" ] || exit 17
        exit 0 ;;
    esac
    exit 99 ;;
  ps) [ -z "${FAKE_CONTAINER:-}" ] || echo abc; exit 0 ;;
  volume) [ -z "${FAKE_VOLUME:-}" ] || echo vol; exit 0 ;;
  network) [ -z "${FAKE_NETWORK:-}" ] || echo net; exit 0 ;;
esac
exit 99
SH

cat > "$FAKE_BIN/curl" <<'SH'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$FAKE_LOG"
[ -z "${FAKE_NOT_READY:-}" ] || exit 22
[ -z "${FAKE_BACKEND_DIES:-}" ] || exit 7
[ -z "${FAKE_READY_DELAY:-}" ] || /usr/bin/sleep "$FAKE_READY_DELAY"
printf '%s' "${FAKE_READY_BODY:-{\"status\":\"ready\"\}}"
SH

cat > "$FAKE_BIN/npm" <<'SH'
#!/usr/bin/env bash
printf 'npm %s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  "ci --no-audit --no-fund") [ -z "${FAKE_NPM_CI_FAIL:-}" ] || exit 31 ;;
  "run build") [ -z "${FAKE_BUILD_FAIL:-}" ] || exit 32 ;;
  "run check:bundle") [ -z "${FAKE_BUNDLE_FAIL:-}" ] || exit 33 ;;
  *) exit 99 ;;
esac
SH

cat > "$FAKE_BIN/npx" <<'SH'
#!/usr/bin/env bash
printf 'npx LLMI_E2E_CI=%s %s\n' "${LLMI_E2E_CI:-unset}" "$*" >> "$FAKE_LOG"
case "$*" in
  "playwright --version") echo "${FAKE_PW_VERSION:-Version 1.64.0}"; exit 0 ;;
  "playwright install --with-deps chromium") [ -z "${FAKE_INSTALL_FAIL:-}" ] || exit 41; exit 0 ;;
  "playwright test --list --reporter=json")
    [ -z "${FAKE_LIST_FAIL:-}" ] || exit 42
    exec "$LLMI_REAL_PYTHON" -I "$FAKE_BIN/mk_report.py" "$PLAYWRIGHT_JSON_OUTPUT_NAME" list ;;
  "playwright test --reporter=list,json")
    [ -n "${FAKE_NO_SETUP:-}" ] || echo "[e2e] run abcdefabcdef: projects main=1 foreign=2 hostile=3 providers=4 comparison=5 (SYNTHETIC)"
    if [ -n "${FAKE_LEAK:-}" ]; then grep '^POSTGRES_PASSWORD=' ../.env; fi
    "$LLMI_REAL_PYTHON" -I "$FAKE_BIN/mk_report.py" "$PLAYWRIGHT_JSON_OUTPUT_NAME" run
    [ -n "${FAKE_NO_TEARDOWN:-}" ] || echo "[e2e] teardown: {\"run\": \"abcdefabcdef\", \"deleted\": ${FAKE_DELETED:-[1, 2, 3, 4, 5]}, \"remaining\": ${FAKE_REMAINING:-[]}}"
    exit "${FAKE_PW_EXIT:-0}" ;;
esac
exit 99
SH

cat > "$FAKE_BIN/node" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "${FAKE_NODE_VERSION:-v22.23.4}" ;;
  -e) [ -n "${FAKE_NO_BROWSER:-}" ] && printf '%s' "$WORK/absent-browser" || printf '%s' "$FAKE_BIN/chromium" ;;
  *) exit 99 ;;
esac
SH

printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_BIN/sleep"
printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_BIN/chromium"
cat > "$FAKE_BIN/timeout" <<'SH'
#!/usr/bin/env bash
printf 'timeout %s\n' "$*" >> "$FAKE_LOG"
[ -z "${FAKE_TIMEOUT:-}" ] || exit 124
shift
"$@"
SH
chmod +x "$FAKE_BIN"/*

export PATH="$FAKE_BIN:$PATH"
if [ "$(cd "$(dirname "$(command -v docker)")" && pwd)" != "$(cd "$FAKE_BIN" && pwd)" ]; then
  echo "ABORT: docker is not the fake" >&2
  exit 2
fi

RUN_ID=424242
P="llmi-r1045-$RUN_ID"
passed=0
failed=0
PHASES=(prepare install database backend frontend test)

note_fail() {
  echo "  FAIL: $1"
  printf '::error title=e2e-self-test::%s\n' "${1//[^A-Za-z0-9 ._,=-]/?}"
  failed=$((failed + 1))
}
note_pass() { passed=$((passed + 1)); }

setup() {
  stop_backend
  TREE="$WORK/tree"
  rm -rf "$TREE" "$WORK/temp"
  mkdir -p "$TREE/scripts/qa" "$TREE/scripts/dev" "$TREE/frontend" "$TREE/backend" "$WORK/temp"
  cp "$SRC/scripts/qa/e2e_gate.sh" "$SRC/scripts/qa/e2e_check.py" "$SRC/scripts/qa/image_build_check.py" "$TREE/scripts/qa/"
  cp "$SRC/scripts/dev/init_env.py" "$SRC/scripts/dev/init_frontend_env.py" "$TREE/scripts/dev/"
  cp "$SRC/.env.example" "$TREE/.env.example"
  cp "$SRC/frontend/.nvmrc" "$TREE/frontend/.nvmrc"
  export FAKE_LOG="$WORK/fake.log"
  : > "$FAKE_LOG"
  : > "$WORK/out.log"
}

gate() { # <phase> [ENV=value ...]
  local phase=$1
  shift
  (
    export GITHUB_ACTIONS=true GITHUB_RUN_ID="$RUN_ID" RUNNER_TEMP="$WORK/temp"
    unset GITHUB_STEP_SUMMARY
    for assignment in "$@"; do export "${assignment?}"; done
    bash "$TREE/scripts/qa/e2e_gate.sh" "$phase"
  ) >> "$WORK/out.log" 2>&1
}

until_phase() { # <last phase to skip before the target>
  local stop=$1 phase
  for phase in "${PHASES[@]}"; do
    [ "$phase" = "$stop" ] && return 0
    gate "$phase" || { note_fail "setup phase $phase failed before $stop"; cat "$WORK/out.log"; return 1; }
  done
}

expect_fail() { # <name> <phase> <required-output-or-empty> [ENV...]
  local name=$1 phase=$2 want=$3
  shift 3
  if gate "$phase" "$@"; then
    note_fail "$name: the gate succeeded"
  elif [ -n "$want" ] && ! grep -q -- "$want" "$WORK/out.log"; then
    note_fail "$name: missing '$want' in the output"
  else
    note_pass
  fi
}

expect_ok() { # <name> <phase> [ENV...]
  local name=$1 phase=$2
  shift 2
  if gate "$phase" "$@"; then note_pass; else note_fail "$name: non-zero exit"; cat "$WORK/out.log"; fi
}

fail_at() { # <name> <phase> <want> [ENV...]: run earlier phases cleanly, then expect failure
  local name=$1 phase=$2 want=$3
  shift 3
  setup
  until_phase "$phase" || return 0
  expect_fail "$name" "$phase" "$want" "$@"
}

echo "== positive"
setup
for phase in "${PHASES[@]}" cleanup; do expect_ok "$phase" "$phase"; done
compose_calls="$(grep -E '^docker compose ' "$FAKE_LOG" | grep -v 'compose version' || true)"
if [ -n "$compose_calls" ] && ! printf '%s\n' "$compose_calls" | grep -qv -- "^docker compose --project-name $P "; then note_pass; else note_fail "a compose call lacks the CI project"; fi
if grep -q -- '--profile' "$FAKE_LOG"; then note_fail "a compose profile was used"; else note_pass; fi
if grep -Eq 'project-name llminsight( |$)' "$FAKE_LOG"; then note_fail "default project used"; else note_pass; fi
if [ "$(grep -c 'npx LLMI_E2E_CI=1 playwright test --reporter=list,json' "$FAKE_LOG")" = "1" ]; then note_pass; else note_fail "CI browser mode not set for the run"; fi
if grep -q 'venv -m app$' "$FAKE_LOG"; then note_pass; else note_fail "real backend not started"; fi
if [ -f "$WORK/temp/e2e-summary/summary.json" ] && [ -f "$WORK/temp/e2e-summary/summary.md" ] && [ "$(find "$WORK/temp/e2e-summary" -type f | wc -l)" -eq 2 ]; then note_pass; else note_fail "summary files"; fi
if grep -q '"verdict": "PASS"' "$WORK/temp/e2e-summary/summary.json"; then note_pass; else note_fail "summary verdict"; fi
password="$(grep '^POSTGRES_PASSWORD=' "$TREE/.env" | cut -d= -f2-)"
token="$(grep '^LLMI_ADMIN_TOKEN=' "$TREE/.env" | cut -d= -f2-)"
if [ -n "$password" ] && ! grep -q -- "$password" "$WORK/out.log" "$FAKE_LOG" && ! grep -q -- "$token" "$WORK/out.log" "$FAKE_LOG"; then note_pass; else note_fail "a generated value was printed"; fi
bad="$(grep -E '^docker ' "$FAKE_LOG" | grep -Ev "^docker version |^docker compose version|^docker compose --project-name $P (config --quiet|config --no-interpolate --format json|config --services|up -d --wait --wait-timeout 120 postgres|ps -q postgres|down -v --remove-orphans --timeout 30)\$|^docker (ps -a -q|volume ls -q|network ls -q) --filter label=com.docker.compose.project=$P\$" || true)"
if [ -n "$bad" ]; then note_fail "disallowed docker command: $bad"; else note_pass; fi

echo "== guards"
setup
if ( unset GITHUB_ACTIONS; export GITHUB_RUN_ID="$RUN_ID" RUNNER_TEMP="$WORK/temp"; bash "$TREE/scripts/qa/e2e_gate.sh" prepare ) >> "$WORK/out.log" 2>&1; then note_fail "ran outside CI"; else note_pass; fi
if [ -s "$FAKE_LOG" ]; then note_fail "tools ran outside CI"; else note_pass; fi
for bad in "abc" "" "12-3" "../x"; do
  setup; expect_fail "run id '$bad'" prepare "GITHUB_RUN_ID must be numeric" "GITHUB_RUN_ID=$bad"
done
setup; expect_fail "keep flag" prepare "LLMI_E2E_KEEP must not be set" LLMI_E2E_KEEP=1
setup; expect_ok "COMPOSE_PROJECT_NAME ignored" prepare COMPOSE_PROJECT_NAME=llminsight
if grep -Eq 'project-name llminsight( |$)' "$FAKE_LOG"; then note_fail "default project used"; else note_pass; fi
setup; expect_fail "unknown phase" nonsense "usage"
setup; expect_fail "effective default name" prepare "PROJECT_MISMATCH" FAKE_NAME=llminsight
setup; expect_fail "app service active" prepare "CONFIG_SERVICES" FAKE_EXTRA_SERVICE=1
setup; expect_fail "postgres not loopback" prepare "CONFIG_PORT" FAKE_HOST_IP=0.0.0.0
setup; expect_fail "invalid compose" prepare "" FAKE_CONFIG_FAIL=1
setup; expect_fail "malformed compose model" prepare "CONFIG_MALFORMED" FAKE_JSON_BAD=1
setup; : > "$TREE/.env"; expect_fail "existing .env" prepare "refused: .env already exists"
setup; : > "$TREE/frontend/.env.local"; expect_fail "existing frontend env" prepare "refused: frontend/.env.local"
setup; expect_fail "wrong node" prepare "node version differs" FAKE_NODE_VERSION=v22.11.0
setup; expect_fail "wrong python" prepare "python version must be 3.12.6" FAKE_PY_VERSION=3.12.5
setup; expect_fail "port busy" prepare "PORT_BUSY" FAKE_PORT_BUSY=all
setup; expect_fail "real llm enabled" prepare "ENV_REAL_LLM" 'FAKE_TAMPER_ENV=s/^LLMI_ALLOW_REAL_LLM=.*/LLMI_ALLOW_REAL_LLM=true/'
setup; expect_fail "provider key" prepare "ENV_PROVIDER_VALUE OPENAI_API_KEY" 'FAKE_TAMPER_ENV=s/^OPENAI_API_KEY=.*/OPENAI_API_KEY=not-empty/'

echo "== N required phases cannot be skipped"
for phase in install database backend frontend test; do
  setup; expect_fail "$phase without prepare" "$phase" "required phase did not complete"
done
setup; gate prepare; expect_fail "database without install" database "required phase did not complete: install"
setup; until_phase backend; expect_fail "test without backend+frontend" test "required phase did not complete: backend"
setup; until_phase frontend; expect_fail "test without frontend" test "required phase did not complete: frontend"

echo "== A database unavailable, B migration failure"
fail_at "A postgres unavailable" database "" FAKE_UP_FAIL=1
fail_at "A service not running" database "PostgreSQL service is not running" FAKE_NO_CONTAINER=1
fail_at "B alembic upgrade" database "" FAKE_ALEMBIC_FAIL=1
fail_at "B alembic drift" database "" FAKE_ALEMBIC_DRIFT=1
fail_at "price catalogue" database "" FAKE_SEED_FAIL=1
fail_at "pip install" install "" FAKE_PIP_FAIL=1
fail_at "pip check" install "" FAKE_PIP_CHECK_FAIL=1

echo "== C backend health"
fail_at "C never ready" backend "did not become ready" FAKE_NOT_READY=1
fail_at "C backend dies" backend "did not become ready" FAKE_BACKEND_DIES=1
fail_at "C backend exits after answering ready" backend "the backend process exited" FAKE_BACKEND_EXITS=1 FAKE_READY_DELAY=0.5
fail_at "C wrong body" backend "did not become ready" 'FAKE_READY_BODY={"status":"not_ready"}'

echo "== D wrong origin, E missing token"
setup; expect_fail "D wrong origin" prepare "FRONTEND_ORIGIN" 'FAKE_TAMPER_FRONTEND=s#^LLMI_API_BASE_URL=.*#LLMI_API_BASE_URL=http://127.0.0.1:8001#'
setup; expect_fail "E token removed" prepare "FRONTEND_TOKEN_MISSING" 'FAKE_TAMPER_FRONTEND=/^LLMI_API_TOKEN=/d'
setup; expect_fail "public variable" prepare "FRONTEND_PUBLIC_VARIABLE" 'FAKE_TAMPER_FRONTEND=$aNEXT_PUBLIC_X=1'

echo "== F browser, G build"
fail_at "F browser install" frontend "" FAKE_INSTALL_FAIL=1
fail_at "F browser absent" frontend "Chromium browser is not installed" FAKE_NO_BROWSER=1
fail_at "F wrong Playwright" frontend "unexpected Playwright version" "FAKE_PW_VERSION=Version 1.65.0"
fail_at "G npm ci" frontend "" FAKE_NPM_CI_FAIL=1
fail_at "G build" frontend "" FAKE_BUILD_FAIL=1
fail_at "bundle scan" frontend "" FAKE_BUNDLE_FAIL=1

echo "== H-L suite outcome"
fail_at "H server startup (exit 1, no report)" test "REPORT_MISSING" FAKE_PW_EXIT=1 FAKE_NO_REPORT=1 FAKE_NO_SETUP=1 FAKE_NO_TEARDOWN=1
fail_at "I failed test" test "TEST_FAILED" FAKE_RUN_FAIL=1 FAKE_PW_EXIT=1
fail_at "I failed test with exit 0" test "TEST_FAILED" FAKE_RUN_FAIL=1
fail_at "I exit 1 clean report" test "PLAYWRIGHT_FAILED" FAKE_PW_EXIT=1
fail_at "J timeout" test "PLAYWRIGHT_TIMEOUT" FAKE_TIMEOUT=1
fail_at "K empty discovery" test "DISCOVERY_EMPTY" FAKE_LIST_COUNT=0
fail_at "K too few tests" test "DISCOVERY_TOO_FEW" FAKE_LIST_COUNT=71
fail_at "K fewer executed" test "EXECUTION_MISMATCH" FAKE_RUN_COUNT=71
fail_at "K discovery command fails" test "" FAKE_LIST_FAIL=1
fail_at "L setup failed" test "FIXTURE_SETUP" FAKE_NO_SETUP=1
fail_at "L global error" test "GLOBAL_ERROR" FAKE_GLOBAL_ERROR=1
fail_at "skipped test" test "TEST_SKIPPED" FAKE_RUN_SKIP=1
fail_at "flaky test" test "TEST_FLAKY" FAKE_RUN_FLAKY=1
fail_at "retries on" test "RETRIES_ENABLED" FAKE_RETRIES=1
fail_at "backend gone before suite" test "" FAKE_NOT_READY=1
fail_at "keep flag at the test phase" test "LLMI_E2E_KEEP must not be set" LLMI_E2E_KEEP=1
setup; until_phase test
stop_backend
expect_fail "backend process exited" test "the backend process exited"
fail_at "busy UI port" test "PORT_BUSY" FAKE_PORT_BUSY=run
fail_at "M teardown missing" test "FIXTURE_TEARDOWN" FAKE_NO_TEARDOWN=1
fail_at "M project left behind" test "FIXTURE_TEARDOWN" 'FAKE_REMAINING=[99]'
fail_at "M only four projects deleted" test "FIXTURE_TEARDOWN" 'FAKE_DELETED=[1,2,3,4]'
fail_at "M six projects deleted" test "FIXTURE_TEARDOWN" 'FAKE_DELETED=[1,2,3,4,5,6]'
fail_at "M a project id repeated" test "FIXTURE_TEARDOWN" 'FAKE_DELETED=[1,1,2,3,4]'

echo "== report privacy"
setup; until_phase test
expect_fail "leak in test output" test "PLAYWRIGHT_FAILED" FAKE_LEAK=1 FAKE_PW_EXIT=1
password="$(grep '^POSTGRES_PASSWORD=' "$TREE/.env" | cut -d= -f2-)"
if grep -q -- "$password" "$WORK/out.log" "$WORK/temp/e2e/playwright.log" "$WORK/temp/e2e-summary/summary.json"; then note_fail "value reached output"; else note_pass; fi
if grep -q '\[REDACTED\]' "$WORK/out.log"; then note_pass; else note_fail "redaction marker missing"; fi

echo "== M cleanup"
setup; until_phase test; expect_fail "compose down fails" cleanup "" FAKE_DOWN_FAIL=1
setup; until_phase test; expect_fail "container remains" cleanup "unexpected containers" FAKE_CONTAINER=1
setup; until_phase test; expect_fail "volume remains" cleanup "unexpected volumes" FAKE_VOLUME=1
setup; until_phase test; expect_fail "network remains" cleanup "unexpected networks" FAKE_NETWORK=1
setup; expect_ok "cleanup after nothing started" cleanup
if grep -q 'compose --project-name .* down' "$FAKE_LOG"; then note_fail "down ran with nothing started"; else note_pass; fi

echo "== static: no failure can be hidden"
GATE="$SRC/scripts/qa/e2e_gate.sh"
if head -12 "$GATE" | grep -q '^set -euo pipefail$'; then note_pass; else note_fail "strict mode"; fi
if grep -nE '\|\| *(true|:)\b|set \+e|exit 0' "$GATE"; then note_fail "ignored status in the gate"; else note_pass; fi
if grep -nE 'docker +(push|run|start|login|system|container|rm)\b|volume +(rm|prune)|--profile|LLMI_E2E_KEEP=' "$GATE"; then note_fail "forbidden docker usage"; else note_pass; fi
if [ "$(grep -c '|| status=\$?' "$GATE")" = "1" ]; then note_pass; else note_fail "the status capture changed"; fi

echo
echo "e2e_gate tests: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
