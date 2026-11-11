#!/usr/bin/env bash
# CI Playwright E2E gate (R104-5): runs the real stack on the GitHub runner.
#   browser -> Next.js -> FastAPI -> PostgreSQL, with synthetic fixtures only.
#
# Usage: bash scripts/qa/e2e_gate.sh {prepare|install|database|backend|frontend|test|cleanup}
#
# Refuses to run outside GitHub Actions, so it cannot touch a developer's Docker daemon or
# database. Every Compose call names the run-specific project, and only PostgreSQL is started.
# Every phase requires the phases before it. Values are never printed; test output is redacted.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

fail() {
  printf '::error title=e2e::%s\n' "$1"
  exit 1
}

[ "${GITHUB_ACTIONS:-}" = "true" ] || fail "refused: this gate runs only on a GitHub Actions runner"
[ -n "${RUNNER_TEMP:-}" ] || fail "RUNNER_TEMP is not set"
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]{1,20}$ ]] || fail "GITHUB_RUN_ID must be numeric"
[ -z "${LLMI_E2E_KEEP:-}" ] || fail "LLMI_E2E_KEEP must not be set"

project="llmi-r1045-${GITHUB_RUN_ID}"
state="${RUNNER_TEMP}/e2e"
py="${LLMI_PYTHON:-python}"
ready_url="http://127.0.0.1:8000/health/ready"
# R133: the main backend's /api/v1 bucket is keyed by client address, and the dashboard's
# server-side reads and the tests' direct reads all come from 127.0.0.1, so the suite's own
# navigation (3-5 parallel reads per page load) can drain the default 10/s, burst 20.
# Sized for this E2E backend process only: far above the suite's peak, still finite, so a
# request storm fails the backend-log check of the report. The production defaults, the
# auth-failure and ingest buckets, and the :8001/:8002 backends of the 429 tests are unchanged.
readonly e2e_api_rps=1000 e2e_api_burst=1000
env_args=(--env .env --env frontend/.env.local)
unset COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_FILE

compose() {
  docker compose --project-name "$project" "$@"
}

check() {
  "$py" -I scripts/qa/e2e_check.py "$@"
}

done_mark() {
  : > "$state/$1.done"
}

require_done() {
  local phase
  for phase in "$@"; do
    [ -f "$state/$phase.done" ] || fail "required phase did not complete: $phase"
  done
}

assert_none() {
  local label=$1 out
  shift
  out="$("$@")"
  [ -z "$out" ] || fail "unexpected $label remain for the CI project"
}

backend_pid() {
  [ -s "$state/backend.pid" ] || fail "the backend process was not started"
  cat "$state/backend.pid"
}

backend_alive() {
  kill -0 "$1"
}

backend_ready() {
  local body
  body="$(curl -fsS --max-time 2 "$ready_url")" || return 1
  [[ "$body" == *'"status":"ready"'* ]]
}

prepare() {
  mkdir -p "$state"
  echo "docker server: $(docker version --format '{{.Server.Version}}')"
  echo "docker compose: $(docker compose version --short)"
  local want have
  want="v$(cat frontend/.nvmrc)"
  have="$(node --version)"
  [ "$have" = "$want" ] || fail "node version differs from frontend/.nvmrc"
  have="$("$py" -c 'import platform; print(platform.python_version())')"
  [ "$have" = "3.12.6" ] || fail "python version must be 3.12.6"
  [ ! -e .env ] || fail "refused: .env already exists"
  [ ! -e frontend/.env.local ] || fail "refused: frontend/.env.local already exists"
  "$py" -I scripts/dev/init_env.py
  "$py" -I scripts/dev/init_frontend_env.py --project-id 1
  check env --env .env --frontend-env frontend/.env.local
  compose config --quiet
  compose config --no-interpolate --format json > "$state/compose.json"
  compose config --services > "$state/services.txt"
  check compose --config "$state/compose.json" --services "$state/services.txt" --project "$project"
  check ports --set all
  echo "effective compose project: $project"
  done_mark prepare
}

install_backend() {
  require_done prepare
  "$py" -m venv backend/.venv
  local venv=backend/.venv/bin/python
  "$venv" -m pip install --disable-pip-version-check -r backend/requirements.lock
  "$venv" -m pip install --disable-pip-version-check --no-deps -e backend
  "$venv" -m pip check
  done_mark install
}

database() {
  require_done prepare install
  : > "$state/compose.started"
  compose up -d --wait --wait-timeout 120 postgres
  local id
  id="$(compose ps -q postgres)"
  [ -n "$id" ] || fail "the PostgreSQL service is not running for the CI project"
  (
    cd backend
    .venv/bin/python -m alembic upgrade head
    .venv/bin/python -m alembic check
    .venv/bin/python -m app.cli seed-prices
  )
  done_mark database
}

start_backend() {
  require_done prepare install database
  (
    cd backend
    export LLMI_RATE_LIMIT_API_RPS="$e2e_api_rps" LLMI_RATE_LIMIT_API_BURST="$e2e_api_burst"
    exec nohup .venv/bin/python -m app
  ) > "$state/backend.log" 2>&1 &
  local pid=$! attempt
  echo "$pid" > "$state/backend.pid"
  for ((attempt = 0; attempt < 60; attempt++)); do
    if backend_ready; then
      backend_alive "$pid" || fail "the backend process exited"
      done_mark backend
      echo "backend ready on 127.0.0.1:8000"
      return 0
    fi
    backend_alive "$pid" || break
    sleep 1
  done
  tail -n 40 "$state/backend.log" | check redact "${env_args[@]}"
  fail "the backend did not become ready"
}

frontend() {
  require_done prepare
  (
    cd frontend
    npm ci --no-audit --no-fund
    npm run build
    npm run check:bundle
    npx playwright install --with-deps chromium
  )
  local version path
  version="$(cd frontend && npx playwright --version)"
  [ "$version" = "Version 1.64.0" ] || fail "unexpected Playwright version"
  path="$(cd frontend && node -e 'process.stdout.write(require("@playwright/test").chromium.executablePath())')"
  [ -x "$path" ] || fail "the Playwright Chromium browser is not installed"
  done_mark frontend
}

run_tests() {
  require_done prepare install database backend frontend
  backend_alive "$(backend_pid)" || fail "the backend process exited"
  backend_ready || fail "the backend is not ready"
  check ports --set run
  export LLMI_E2E_CI=1
  (
    cd frontend
    LLMI_E2E_PROJECT=1 LLMI_E2E_FOREIGN_PROJECT=2 LLMI_E2E_HOSTILE_PROJECT=3 LLMI_E2E_PROVIDERS_PROJECT=4 \
      PLAYWRIGHT_JSON_OUTPUT_NAME="$state/list.json" npx playwright test --list --reporter=json > "$state/list.stdout"
  )
  local status=0
  (
    cd frontend
    PLAYWRIGHT_JSON_OUTPUT_NAME="$state/playwright.json" timeout 900 npx playwright test --reporter=list,json 2>&1
  ) | check redact "${env_args[@]}" | tee "$state/playwright.log" || status=$?
  check report --list "$state/list.json" --report "$state/playwright.json" --log "$state/playwright.log" \
    --backend-log "$state/backend.log" --exit-status "$status" "${env_args[@]}" --out-dir "$RUNNER_TEMP/e2e-summary"
}

cleanup() {
  if [ -s "$state/backend.pid" ]; then
    local pid attempt
    pid="$(cat "$state/backend.pid")"
    if backend_alive "$pid"; then
      kill "$pid"
      for ((attempt = 0; attempt < 20; attempt++)); do
        backend_alive "$pid" || break
        sleep 1
      done
      if backend_alive "$pid"; then
        kill -9 "$pid"
      fi
    fi
  fi
  if [ -f "$state/compose.started" ]; then
    compose down -v --remove-orphans --timeout 30
  fi
  local label="label=com.docker.compose.project=$project"
  assert_none "containers" docker ps -a -q --filter "$label"
  assert_none "volumes" docker volume ls -q --filter "$label"
  assert_none "networks" docker network ls -q --filter "$label"
  echo "cleanup: no resource remains for the CI project"
}

case "${1:-}" in
  prepare) prepare ;;
  install) install_backend ;;
  database) database ;;
  backend) start_backend ;;
  frontend) frontend ;;
  test) run_tests ;;
  cleanup) cleanup ;;
  *) fail "usage: e2e_gate.sh {prepare|install|database|backend|frontend|test|cleanup}" ;;
esac
