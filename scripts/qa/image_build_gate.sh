#!/usr/bin/env bash
# CI image-build gate (R104-4): builds the Compose app-profile images on the GitHub runner.
#
# Usage: bash scripts/qa/image_build_gate.sh {preflight|build|verify}
#
# Refuses to run outside GitHub Actions, so it cannot touch a developer's Docker daemon.
# Always passes an explicit run-specific project name, never the repository's default.
# Builds images only: it starts no container and contacts no registry for upload.
# Values are never printed; `config --quiet` and `--no-interpolate` keep secrets out of the output.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

fail() {
  printf '::error title=image-build::%s\n' "$1"
  exit 1
}

[ "${GITHUB_ACTIONS:-}" = "true" ] || fail "refused: this gate runs only on a GitHub Actions runner"
[ -n "${RUNNER_TEMP:-}" ] || fail "RUNNER_TEMP is not set"
[[ "${GITHUB_RUN_ID:-}" =~ ^[0-9]{1,20}$ ]] || fail "GITHUB_RUN_ID must be numeric"

project="llmi-r1044-${GITHUB_RUN_ID}"
state="${RUNNER_TEMP}/image-build"
py="${LLMI_PYTHON:-python3}"
unset COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_FILE

compose() {
  docker compose --project-name "$project" --profile app "$@"
}

check() {
  "$py" -I scripts/qa/image_build_check.py "$@"
}

assert_none() {
  local label=$1 out
  shift
  out="$("$@")"
  [ -z "$out" ] || fail "unexpected $label exist for the CI project"
}

preflight() {
  mkdir -p "$state"
  echo "docker server: $(docker version --format '{{.Server.Version}}')"
  echo "docker compose: $(docker compose version --short)"
  local path
  for path in docker-compose.yml backend/Dockerfile frontend/Dockerfile .dockerignore frontend/.dockerignore .env; do
    [ -f "$path" ] || fail "missing required file: $path"
  done
  check env --env .env
  compose config --quiet
  compose config --no-interpolate --format json > "$state/compose.json"
  compose config --images > "$state/images.txt"
  check config --config "$state/compose.json" --images "$state/images.txt" --project "$project" --root .
  check plant --root . --state "$state/canary.json"
  echo "effective compose project: $project"
}

build() {
  [ -f "$state/canary.json" ] || fail "preflight did not complete"
  export BUILDKIT_PROGRESS=plain
  compose build backend frontend
  # The only stage that runs COPY . .; inspected in verify, then never used for anything else.
  docker build --target build --tag "${project}-frontend-build-stage" --file frontend/Dockerfile frontend
}

verify_image() {
  local name=$1 image="${project}-$1"
  docker image inspect "$image" --format '{{json .Config}}' > "$state/$name.config.json"
  docker image save -o "$state/$name.tar" "$image"
  check image --name "$name" --tar "$state/$name.tar" --config "$state/$name.config.json" \
    --state "$state/canary.json" --env .env
  rm -f "$state/$name.tar"
}

verify() {
  [ -f "$state/canary.json" ] || fail "preflight did not complete"
  verify_image backend
  verify_image frontend
  verify_image frontend-build-stage
  local label="label=com.docker.compose.project=$project"
  assert_none "containers" docker ps -a -q --filter "$label"
  assert_none "postgres containers" docker ps -a -q --filter "ancestor=postgres:17"
  assert_none "volumes" docker volume ls -q --filter "$label"
  assert_none "networks" docker network ls -q --filter "$label"
  echo "verified: backend and frontend images built; no container, volume or network created"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### Image build gate"
      echo "- Compose project: \`$project\`"
      echo "- Images verified: backend, frontend, frontend build stage"
      echo "- Containers, volumes and networks created: none"
      echo "- Building an image does not prove the application runs."
    } >> "$GITHUB_STEP_SUMMARY"
  fi
}

case "${1:-}" in
  preflight) preflight ;;
  build) build ;;
  verify) verify ;;
  *) fail "usage: image_build_gate.sh {preflight|build|verify}" ;;
esac
