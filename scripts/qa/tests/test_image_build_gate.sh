#!/usr/bin/env bash
# Offline negative tests of scripts/qa/image_build_gate.sh (R104-4).
#
# Runs the real gate against a FAKE `docker` placed first on PATH, in a disposable directory.
# No Docker daemon is contacted: the run aborts unless `docker` resolves to the fake.
#
# Run: bash scripts/qa/tests/test_image_build_gate.sh
# shellcheck disable=SC2030,SC2031  # each case sets its own environment inside a subshell
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export LLMI_PYTHON="${LLMI_PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/mk_tar.py" <<'PY'
"""Writes a synthetic saved-image tar: one layer holding the expected files."""
import io, json, os, sys, tarfile
out, kind = sys.argv[1:3]
files = {
    "backend": ["app/backend/app/main.py", "app/backend/alembic.ini", "app/backend/alembic/env.py", "app/backend/requirements.lock"],
    "frontend": ["app/.next/BUILD_ID", "app/next.config.ts", "app/scripts/next.mjs", "app/package.json"],
    "frontend-build-stage": ["app/.next/BUILD_ID", "app/package.json"],
}[kind]
contents = {name: b"synthetic\n" for name in files}
if os.environ.get("FAKE_TAR_DROP") == kind:
    contents.pop(files[0])
if os.environ.get("FAKE_TAR_ENV") == kind:
    contents["app/.env"] = b"X=1\n"
if os.environ.get("FAKE_TAR_HANDOFF") == kind:
    contents["app/LLM_INSIGHT_HANDOFF.md"] = b"note\n"
if os.environ.get("FAKE_TAR_CANARY") == kind:
    state = os.path.join(os.environ["RUNNER_TEMP"], "image-build", "canary.json")
    contents["app/leak.txt"] = json.load(open(state))["canary"].encode()
if os.environ.get("FAKE_TAR_ENVVALUE") == kind:
    contents["app/leak2.txt"] = b"prefix " + os.environ["FAKE_SECRET"].encode()
layer = io.BytesIO()
with tarfile.open(fileobj=layer, mode="w") as tar:
    for name, data in contents.items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
blob = layer.getvalue()
with tarfile.open(out, "w") as tar:
    info = tarfile.TarInfo("blobs/sha256/layer")
    info.size = len(blob)
    tar.addfile(info, io.BytesIO(blob))
    meta = b"{}"
    info = tarfile.TarInfo("manifest.json")
    info.size = len(meta)
    tar.addfile(info, io.BytesIO(meta))
PY

cat > "$FAKE_BIN/docker" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  version) echo 28.0.0; exit 0 ;;
  compose)
    shift
    if [ "${1:-}" = "version" ]; then echo 2.40.0; exit 0; fi
    [ "${1:-}" = "--project-name" ] || exit 64
    project=$2; shift 2
    [ "${1:-} ${2:-}" = "--profile app" ] || exit 64
    shift 2
    case "$1 ${2:-}" in
      "config --quiet")
        [ -z "${FAKE_CONFIG_FAIL:-}" ] || exit 15
        exit 0 ;;
      "config --no-interpolate")
        if [ -n "${FAKE_JSON_BAD:-}" ]; then echo '{'; exit 0; fi
        root="$(pwd -W 2>/dev/null || pwd)"
        args=""
        [ -z "${FAKE_BUILD_ARGS:-}" ] || args=',"args":{"X":"1"}'
        cat <<JSON
{"name":"${FAKE_NAME:-$project}","services":{
"postgres":{"image":"postgres:17"},
"backend":{"build":{"context":"$root","dockerfile":"backend/Dockerfile"$args},"profiles":["app"],"environment":{"LLMI_ALLOW_REAL_LLM":"false"}},
"frontend":{"build":{"context":"$root/frontend","dockerfile":"Dockerfile"},"profiles":["app"],"environment":{}}}}
JSON
        exit 0 ;;
      "config --images")
        printf '%s\n' "${project}-backend" "${project}-frontend" "postgres:17"; exit 0 ;;
      "build backend")
        [ "$*" = "build backend frontend" ] || exit 64
        [ "${FAKE_BUILD_FAIL:-}" != "backend" ] || exit 17
        [ "${FAKE_BUILD_FAIL:-}" != "frontend" ] || exit 18
        for s in backend frontend; do
          [ "${FAKE_SKIP_IMAGE:-}" = "$s" ] || : > "$FAKE_STATE/$s"
        done
        exit 0 ;;
    esac
    exit 99 ;;
  build)
    [ -z "${FAKE_STAGE_FAIL:-}" ] || exit 19
    : > "$FAKE_STATE/frontend-build-stage"; exit 0 ;;
  image)
    case "$2" in
      inspect)
        kind="${3##llmi-r1044-*[0-9]-}"
        [ -f "$FAKE_STATE/$kind" ] || exit 1
        user=""; [ "$kind" = backend ] && user=10001; [ "$kind" = frontend ] && user=node
        [ -z "${FAKE_IMAGE_USER:-}" ] || user="$FAKE_IMAGE_USER"
        env='"PATH=/usr/bin"'
        [ -z "${FAKE_IMAGE_ENV:-}" ] || env="\"LLMI_ADMIN_TOKEN=x\""
        printf '{"User":"%s","Env":[%s]}\n' "$user" "$env"; exit 0 ;;
      save)
        kind="${5##llmi-r1044-*[0-9]-}"
        exec "$LLMI_PYTHON" -I "$FAKE_BIN/mk_tar.py" "$4" "$kind" ;;
    esac
    exit 99 ;;
  ps) [ -z "${FAKE_CONTAINER:-}" ] || echo abc123; exit 0 ;;
  volume) [ -z "${FAKE_VOLUME:-}" ] || echo vol1; exit 0 ;;
  network) [ -z "${FAKE_NETWORK:-}" ] || echo net1; exit 0 ;;
esac
exit 99
SH
chmod +x "$FAKE_BIN/docker"
export PATH="$FAKE_BIN:$PATH" FAKE_BIN

resolved="$(command -v docker)"
case "$resolved" in
  "$FAKE_BIN"/*|*/bin/docker) [ "$(cd "$(dirname "$resolved")" && pwd)" = "$(cd "$FAKE_BIN" && pwd)" ] || { echo "ABORT: docker is not the fake" >&2; exit 2; } ;;
  *) echo "ABORT: docker is not the fake" >&2; exit 2 ;;
esac

SECRET_PW="synthetic-postgres-value-0123456789"
export FAKE_SECRET="$SECRET_PW"
RUN_ID=424242
P="llmi-r1044-$RUN_ID"
passed=0
failed=0

note_fail() { echo "  FAIL: $1"; failed=$((failed + 1)); }
note_pass() { passed=$((passed + 1)); }

setup() {
  TREE="$WORK/tree"
  rm -rf "$TREE" "$WORK/temp" "$WORK/state"
  mkdir -p "$TREE/scripts/qa" "$TREE/backend/app" "$TREE/frontend" "$TREE/docs" "$WORK/temp" "$WORK/state"
  cp "$SRC/scripts/qa/image_build_gate.sh" "$SRC/scripts/qa/image_build_check.py" "$TREE/scripts/qa/"
  : > "$TREE/docker-compose.yml"; : > "$TREE/backend/Dockerfile"; : > "$TREE/frontend/Dockerfile"
  : > "$TREE/.dockerignore"; : > "$TREE/frontend/.dockerignore"
  cat > "$TREE/.env" <<ENV
POSTGRES_USER=synthetic
POSTGRES_PASSWORD=$SECRET_PW
POSTGRES_DB=synthetic
LLMI_KEY_PEPPER=synthetic-pepper-value-0123456789
LLMI_ADMIN_TOKEN=synthetic-admin-value-0123456789
LLMI_ALLOW_REAL_LLM=false
OPENAI_API_KEY=
GEMINI_API_KEY=
ENV
  export FAKE_LOG="$WORK/fake.log" FAKE_STATE="$WORK/state"
  : > "$FAKE_LOG"
  : > "$WORK/out.log"
}

# gate <phase> [ENV=value ...]: runs one phase of the real gate; returns its exit status.
gate() {
  local phase=$1
  shift
  (
    export GITHUB_ACTIONS=true GITHUB_RUN_ID="$RUN_ID" RUNNER_TEMP="$WORK/temp"
    unset GITHUB_STEP_SUMMARY
    for assignment in "$@"; do export "${assignment?}"; done
    bash "$TREE/scripts/qa/image_build_gate.sh" "$phase"
  ) >> "$WORK/out.log" 2>&1
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
  if gate "$phase" "$@"; then note_pass; else note_fail "$name: exit status non-zero"; cat "$WORK/out.log"; fi
}

full_preflight_and_build() {
  gate preflight && gate build
}

assert_only_allowed_commands() {
  local bad
  bad="$(grep -Ev "^version |^compose version|^compose --project-name $P --profile app (config --quiet|config --no-interpolate --format json|config --images|build backend frontend)\$|^build --target build --tag ${P}-frontend-build-stage --file frontend/Dockerfile frontend\$|^image (inspect|save) |^ps -a -q |^volume ls |^network ls " "$FAKE_LOG" || true)"
  if [ -n "$bad" ]; then note_fail "$1: disallowed docker command used"; else note_pass; fi
}

echo "== positive"
setup
expect_ok "preflight" preflight
expect_ok "build" build
expect_ok "verify" verify
assert_only_allowed_commands "positive"
if grep -q -- "$SECRET_PW" "$WORK/out.log" "$FAKE_LOG"; then note_fail "secret printed"; else note_pass; fi
if grep -q "llmi-canary-" "$WORK/out.log"; then note_fail "canary printed"; else note_pass; fi
compose_calls="$(grep -E '^compose ' "$FAKE_LOG" | grep -v '^compose version' || true)"
if [ -n "$compose_calls" ] && ! printf '%s\n' "$compose_calls" | grep -qv -- "^compose --project-name $P --profile app "; then note_pass; else note_fail "a compose call lacks the CI project"; fi
for p in frontend/.env.local backend/app/.env LLM_INSIGHT_HANDOFF.md docs/PHASE7_LIVE_DEMO_PLAN.md docs/OPUS_PHASE7_PROMPTS.md; do
  if [ -f "$TREE/$p" ]; then note_pass; else note_fail "canary missing: $p"; fi
done

echo "== A invalid compose configuration"
setup; expect_fail "A config --quiet" preflight "" FAKE_CONFIG_FAIL=1
setup; expect_fail "A malformed json" preflight "CONFIG_MALFORMED" FAKE_JSON_BAD=1

echo "== B/C missing Dockerfile"
setup; rm "$TREE/backend/Dockerfile"; expect_fail "B backend" preflight "missing required file: backend/Dockerfile"
if grep -q " build " "$FAKE_LOG"; then note_fail "B build ran"; else note_pass; fi
setup; rm "$TREE/frontend/Dockerfile"; expect_fail "C frontend" preflight "missing required file: frontend/Dockerfile"
if grep -q " build " "$FAKE_LOG"; then note_fail "C build ran"; else note_pass; fi

echo "== D/E failed builds"
setup; gate preflight; expect_fail "D backend build" build "" FAKE_BUILD_FAIL=backend
setup; gate preflight; expect_fail "E frontend build" build "" FAKE_BUILD_FAIL=frontend
setup; gate preflight; expect_fail "build stage fails" build "" FAKE_STAGE_FAIL=1
setup; expect_fail "build without preflight" build "preflight did not complete"
setup; gate preflight; gate build FAKE_SKIP_IMAGE=backend; expect_fail "D silent backend skip" verify ""
setup; gate preflight; gate build FAKE_SKIP_IMAGE=frontend; expect_fail "E silent frontend skip" verify ""

echo "== F required environment"
for key in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB LLMI_KEY_PEPPER LLMI_ADMIN_TOKEN; do
  setup; grep -v "^$key=" "$TREE/.env" > "$TREE/.env.tmp"; mv "$TREE/.env.tmp" "$TREE/.env"
  expect_fail "F missing $key" preflight "ENV_REQUIRED_MISSING $key"
done
setup; sed -i "s/^LLMI_KEY_PEPPER=.*/LLMI_KEY_PEPPER=CHANGE_ME/" "$TREE/.env"; expect_fail "F change-me" preflight "ENV_REQUIRED_MISSING LLMI_KEY_PEPPER"
setup; rm "$TREE/.env"; expect_fail "F no env file" preflight "missing required file: .env"
setup; sed -i "s/^OPENAI_API_KEY=.*/OPENAI_API_KEY=not-empty/" "$TREE/.env"; expect_fail "F provider key in file" preflight "ENV_PROVIDER_VALUE OPENAI_API_KEY"
setup; expect_fail "F provider key in environment" preflight "ENV_PROVIDER_VALUE GEMINI_API_KEY" GEMINI_API_KEY=not-empty
setup; sed -i "s/^LLMI_ALLOW_REAL_LLM=.*/LLMI_ALLOW_REAL_LLM=true/" "$TREE/.env"; expect_fail "F real llm" preflight "ENV_REAL_LLM"

echo "== G project name"
setup; expect_fail "G default name effective" preflight "PROJECT_MISMATCH" FAKE_NAME=llminsight
setup; expect_ok "G env override ignored" preflight COMPOSE_PROJECT_NAME=llminsight
if grep -Eq "project-name llminsight( |$)" "$FAKE_LOG"; then note_fail "G default used"; else note_pass; fi
for bad in "abc" "" "12-3" "../x" "1 2"; do
  setup; expect_fail "G run id '$bad'" preflight "GITHUB_RUN_ID must be numeric" "GITHUB_RUN_ID=$bad"
done
if [ -s "$FAKE_LOG" ]; then note_fail "G docker ran with an invalid id"; else note_pass; fi

echo "== H build context and image contents"
for kind in backend frontend frontend-build-stage; do
  setup; full_preflight_and_build; expect_fail "H .env in $kind" verify "IMAGE_EXCLUDED_FILE" "FAKE_TAR_ENV=$kind"
  setup; full_preflight_and_build; expect_fail "H canary in $kind" verify "IMAGE_VALUE_FOUND" "FAKE_TAR_CANARY=$kind"
done
setup; full_preflight_and_build; expect_fail "H handoff note" verify "IMAGE_EXCLUDED_FILE" FAKE_TAR_HANDOFF=backend
setup; full_preflight_and_build; expect_fail "H generated value" verify "IMAGE_VALUE_FOUND" FAKE_TAR_ENVVALUE=frontend
setup; full_preflight_and_build; expect_fail "expected output missing" verify "IMAGE_OUTPUT_MISSING" FAKE_TAR_DROP=frontend
setup; full_preflight_and_build; expect_fail "root user" verify "IMAGE_USER" FAKE_IMAGE_USER=root
setup; full_preflight_and_build; expect_fail "secret env in image" verify "IMAGE_SECRET_ENV" FAKE_IMAGE_ENV=1
setup; expect_fail "build args rejected" preflight "CONFIG_BUILD_KEYS" FAKE_BUILD_ARGS=1
setup; : > "$TREE/frontend/.env.local"; expect_fail "canary never overwrites" preflight "PLANT_PATH_EXISTS"

echo "== runtime resources must not exist"
setup; full_preflight_and_build; expect_fail "container" verify "unexpected containers" FAKE_CONTAINER=1
setup; full_preflight_and_build; expect_fail "volume" verify "unexpected volumes" FAKE_VOLUME=1
setup; full_preflight_and_build; expect_fail "network" verify "unexpected networks" FAKE_NETWORK=1

echo "== refusal outside CI and phase handling"
setup
if ( unset GITHUB_ACTIONS; export GITHUB_RUN_ID="$RUN_ID" RUNNER_TEMP="$WORK/temp"; bash "$TREE/scripts/qa/image_build_gate.sh" preflight ) >> "$WORK/out.log" 2>&1; then note_fail "ran outside CI"; else note_pass; fi
if [ -s "$FAKE_LOG" ]; then note_fail "docker called outside CI"; else note_pass; fi
setup; expect_fail "unknown phase" nonsense "usage"

echo "== I no failure can be hidden (static, the real files)"
GATE="$SRC/scripts/qa/image_build_gate.sh"
if head -20 "$GATE" | grep -q '^set -euo pipefail$'; then note_pass; else note_fail "I strict mode"; fi
if grep -nE '\|\| *(true|:)\b|set \+e|exit 0|2>/dev/null' "$GATE"; then note_fail "I ignored status in the gate"; else note_pass; fi
if grep -nE 'compose[^#]*\b(up|down|run|start|restart|rm|push|pull)\b|docker +(push|run|start|login|volume +rm|system|container|rm)\b' "$GATE"; then note_fail "I forbidden docker verb in the gate"; else note_pass; fi
JOB="$WORK/job.txt"
sed -n '/^  image-build:$/,$p' "$SRC/.github/workflows/ci.yml" > "$JOB"
if [ -s "$JOB" ]; then note_pass; else note_fail "I job not found"; fi
if grep -nE 'continue-on-error|\|\| *true|set \+e|^ *if:|secrets\.|docker compose|docker push' "$JOB"; then note_fail "I job can hide a failure or calls Docker directly"; else note_pass; fi

echo
echo "image_build_gate tests: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
