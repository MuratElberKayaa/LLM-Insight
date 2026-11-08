"""Checks for the CI image-build gate (R104-4). Stdlib only; runs no process and no Docker command.

scripts/qa/image_build_gate.sh runs Docker on the GitHub runner and hands its outputs to this file:

  env     the throwaway .env holds every required value and no provider credential
  config  the effective Compose model: project name, build targets, no build arguments or secrets
  plant   writes canary files that the build contexts must exclude (GitHub runner only)
  image   an image's saved tar: expected outputs, no excluded file, canary or generated secret

Every failure exits 1 and prints only a fixed kind and a sanitized detail, never a value.
"""

import argparse
import json
import os
import re
import secrets
import sys
import tarfile
from pathlib import Path

DEFAULT_PROJECT = "llminsight"
PROJECT = re.compile(r"llmi-r1044-[0-9]{1,20}\Z")
REQUIRED_ENV = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "LLMI_KEY_PEPPER",
    "LLMI_ADMIN_TOKEN",
)
PROVIDER_ENV = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
)
EMPTY_ENV = ("LLMI_LIVE_CONSENT",)
GENERATED_VALUES = (
    "POSTGRES_PASSWORD",
    "LLMI_KEY_PEPPER",
    "LLMI_ADMIN_TOKEN",
    "LLMI_API_TOKEN",
)
SERVICES = {"postgres", "backend", "frontend"}
BUILD_KEYS = {"context", "dockerfile"}
CANARY_PATHS = (
    "frontend/.env.local",
    "frontend/.ci-canary/.env",
    "frontend/.ci-canary/.env.local",
    "backend/app/.env",
    "backend/app/.ci-canary/.env.local",
    "LLM_INSIGHT_HANDOFF.md",
    "docs/PHASE7_LIVE_DEMO_PLAN.md",
    "docs/OPUS_PHASE7_PROMPTS.md",
)
IMAGES = {
    "backend": {
        "user": "10001",
        "required": (
            "app/backend/app/main.py",
            "app/backend/alembic.ini",
            "app/backend/alembic/env.py",
            "app/backend/requirements.lock",
        ),
    },
    "frontend": {
        "user": "node",
        "required": (
            "app/.next/BUILD_ID",
            "app/next.config.ts",
            "app/scripts/next.mjs",
            "app/package.json",
        ),
    },
    # The stage that runs COPY . . ; only this stage holds the frontend build context.
    "frontend-build-stage": {
        "user": "",
        "required": ("app/.next/BUILD_ID", "app/package.json"),
    },
}
ENV_FILE = re.compile(r"\.env(\..*)?\Z")
EXCLUDED_NAMES = {
    "LLM_INSIGHT_HANDOFF.md",
    "PHASE7_LIVE_DEMO_PLAN.md",
    "OPUS_PHASE7_PROMPTS.md",
}
EXCLUDED_DIRS = {".git", ".venv"}
EXCLUDED_SUFFIXES = (".pem", ".key", ".sqlite", ".sqlite3", ".db", ".dump")
SECRET_ENV_NAME = re.compile(r"PASSWORD|PEPPER|TOKEN|API_KEY|SECRET|DATABASE_URL")
CHUNK = 1024 * 1024


def problem(kind, detail=""):
    clean = re.sub(r"[^A-Za-z0-9._/@+=, -]", "?", str(detail))[:300]
    return {"kind": kind, "detail": clean}


def read_env(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def check_env(path, environ=None):
    environ = os.environ if environ is None else environ
    try:
        values = read_env(path)
    except (OSError, UnicodeDecodeError):
        return [problem("ENV_MISSING", "the interpolation env file cannot be read")]
    problems = []
    for name in REQUIRED_ENV:
        if not values.get(name) or values[name] == "CHANGE_ME":
            problems.append(problem("ENV_REQUIRED_MISSING", name))
    for name in PROVIDER_ENV + EMPTY_ENV:
        if values.get(name) or environ.get(name):
            problems.append(problem("ENV_PROVIDER_VALUE", name))
    if values.get("LLMI_ALLOW_REAL_LLM", "false").lower() != "false":
        problems.append(problem("ENV_REAL_LLM", "LLMI_ALLOW_REAL_LLM must be false"))
    if values.get("LLMI_PROVIDER_MODE", "mock") != "mock":
        problems.append(problem("ENV_REAL_LLM", "LLMI_PROVIDER_MODE must be mock"))
    return problems


def same_path(left, right):
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
        os.path.realpath(right)
    )


def check_build(service, spec, context, dockerfile):
    build = spec.get("build")
    if not isinstance(build, dict):
        return [problem("CONFIG_NO_BUILD", service)]
    problems = []
    if set(build) != BUILD_KEYS:
        problems.append(problem("CONFIG_BUILD_KEYS", service))
    if not same_path(str(build.get("context", "")), context):
        problems.append(problem("CONFIG_BUILD_CONTEXT", service))
    if build.get("dockerfile") != dockerfile:
        problems.append(problem("CONFIG_DOCKERFILE", service))
    if "image" in spec:
        problems.append(problem("CONFIG_IMAGE_TARGET", service))
    if spec.get("profiles") != ["app"]:
        problems.append(problem("CONFIG_PROFILE", service))
    return problems


def check_config(config_path, images_path, project, root):
    if not PROJECT.match(project) or project == DEFAULT_PROJECT:
        return [problem("PROJECT_INVALID", "not a run-specific CI project name")]
    try:
        model = json.loads(Path(config_path).read_bytes())
        listed = Path(images_path).read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return [problem("CONFIG_MALFORMED", "the Compose output cannot be parsed")]
    if not isinstance(model, dict) or not isinstance(model.get("services"), dict):
        return [problem("CONFIG_MALFORMED", "no services in the Compose output")]
    problems = []
    if model.get("name") != project:
        problems.append(problem("PROJECT_MISMATCH", "effective project name differs"))
    if set(model["services"]) != SERVICES:
        problems.append(problem("CONFIG_SERVICES", "unexpected service set"))
        return problems
    services = model["services"]
    problems += check_build("backend", services["backend"], root, "backend/Dockerfile")
    problems += check_build(
        "frontend", services["frontend"], os.path.join(root, "frontend"), "Dockerfile"
    )
    if "build" in services["postgres"]:
        problems.append(problem("CONFIG_BUILD_POSTGRES", "postgres must not be built"))
    if services["backend"].get("environment", {}).get("LLMI_ALLOW_REAL_LLM") != "false":
        problems.append(problem("ENV_REAL_LLM", "backend must set real LLM to false"))
    for name, spec in services.items():
        for key in spec.get("environment", {}):
            if key in PROVIDER_ENV:
                problems.append(problem("CONFIG_PROVIDER_ENV", f"{name} {key}"))
    for suffix in ("backend", "frontend"):
        if f"{project}-{suffix}" not in listed:
            problems.append(problem("CONFIG_IMAGE_NAME", suffix))
    return problems


def plant(root, state_path, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("GITHUB_ACTIONS") != "true":
        return [
            problem("PLANT_REFUSED", "canary files are written only on the CI runner")
        ]
    canary = "llmi-canary-" + secrets.token_hex(32)
    for rel in CANARY_PATHS:
        target = Path(root) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return [problem("PLANT_PATH_EXISTS", rel)]
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"LLMI_CI_CANARY={canary}\n")
    state = Path(state_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"canary": canary}), encoding="utf-8")
    return []


def clean_name(name):
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def is_excluded(path):
    """Applies to the application tree only; third-party packages may ship files of these names."""
    if not path.startswith("app/") or path.startswith("app/node_modules/"):
        return False
    parts = path.split("/")
    base = parts[-1]
    base = base.removeprefix(".wh.")
    return (
        ENV_FILE.match(base) is not None
        or base in EXCLUDED_NAMES
        or base.endswith(EXCLUDED_SUFFIXES)
        or bool(EXCLUDED_DIRS.intersection(parts))
    )


def stream_has(handle, needles):
    if not needles:
        return False
    overlap = max(len(item) for item in needles) - 1
    tail = b""
    while True:
        chunk = handle.read(CHUNK)
        if not chunk:
            return False
        buffer = tail + chunk
        if any(item in buffer for item in needles):
            return True
        tail = buffer[-overlap:] if overlap else b""


def needles_from(state_path, env_path):
    try:
        canary = json.loads(Path(state_path).read_bytes())["canary"]
        values = read_env(env_path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    found = [canary.encode()]
    for name in GENERATED_VALUES:
        value = values.get(name, "")
        if len(value) >= 16:
            found.append(value.encode())
    return found


def open_layer(handle):
    """A tar reader for an image blob, or None when the blob is metadata and not a layer."""
    try:
        return tarfile.open(fileobj=handle, mode="r:*")
    except tarfile.ReadError:
        return None


def scan_tar(tar_path, needles, seen, counters):
    """Reads every layer of a saved image. Returns the problems found in its file contents."""
    problems = []
    with tarfile.open(tar_path, "r:") as outer:
        for member in outer:
            if not member.isfile():
                continue
            handle = outer.extractfile(member)
            layer = open_layer(handle)
            if layer is None:
                handle.seek(0)
                if stream_has(handle, needles):
                    problems.append(problem("IMAGE_VALUE_FOUND", "image metadata"))
                continue
            with layer:
                counters["layers"] += 1
                for entry in layer:
                    path = clean_name(entry.name)
                    seen.add(path)
                    if is_excluded(path):
                        problems.append(
                            problem("IMAGE_EXCLUDED_FILE", "excluded path in app")
                        )
                    if entry.isfile():
                        counters["files"] += 1
                        if stream_has(layer.extractfile(entry), needles):
                            problems.append(
                                problem("IMAGE_VALUE_FOUND", "layer content")
                            )
    return problems


def check_image(name, tar_path, config_path, state_path, env_path):
    spec = IMAGES.get(name)
    if spec is None:
        return [problem("IMAGE_UNKNOWN", name)], {}
    needles = needles_from(state_path, env_path)
    if needles is None:
        return [
            problem("IMAGE_STATE_MISSING", "canary state or env file unreadable")
        ], {}
    try:
        raw_config = Path(config_path).read_bytes()
        config = json.loads(raw_config)
    except (OSError, ValueError):
        return [problem("IMAGE_CONFIG_MALFORMED", name)], {}
    if not isinstance(config, dict):
        return [problem("IMAGE_CONFIG_MALFORMED", name)], {}
    problems = []
    if (config.get("User") or "") != spec["user"]:
        problems.append(problem("IMAGE_USER", name))
    for item in config.get("Env") or []:
        if SECRET_ENV_NAME.search(str(item).split("=", 1)[0]):
            problems.append(problem("IMAGE_SECRET_ENV", "a secret-like variable name"))
    if any(item in raw_config for item in needles):
        problems.append(problem("IMAGE_VALUE_FOUND", "image configuration"))
    seen, counters = set(), {"layers": 0, "files": 0}
    try:
        problems += scan_tar(tar_path, needles, seen, counters)
    except (OSError, tarfile.TarError):
        return [problem("IMAGE_TAR_INVALID", name)], counters
    if not counters["layers"]:
        problems.append(problem("IMAGE_NO_LAYERS", name))
    for required in spec["required"]:
        if required not in seen:
            problems.append(problem("IMAGE_OUTPUT_MISSING", required))
    return problems, counters


def report(problems, success):
    for item in problems:
        print(f"::error title=image-build::{item['kind']} {item['detail']}")
    if problems:
        return 1
    print(success)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    env = sub.add_parser("env")
    env.add_argument("--env", required=True)
    config = sub.add_parser("config")
    config.add_argument("--config", required=True)
    config.add_argument("--images", required=True)
    config.add_argument("--project", required=True)
    config.add_argument("--root", required=True)
    plant_cmd = sub.add_parser("plant")
    plant_cmd.add_argument("--root", required=True)
    plant_cmd.add_argument("--state", required=True)
    image = sub.add_parser("image")
    image.add_argument("--name", required=True)
    image.add_argument("--tar", required=True)
    image.add_argument("--config", required=True)
    image.add_argument("--state", required=True)
    image.add_argument("--env", required=True)
    args = parser.parse_args(argv)
    if args.command == "env":
        return report(
            check_env(args.env), "env: required values set, no provider credential"
        )
    if args.command == "config":
        found = check_config(args.config, args.images, args.project, args.root)
        return report(
            found, "config: project name, build targets and build keys verified"
        )
    if args.command == "plant":
        return report(plant(args.root, args.state), "plant: canary files written")
    found, counters = check_image(
        args.name, args.tar, args.config, args.state, args.env
    )
    return report(found, f"image {args.name}: verified, {counters}")


if __name__ == "__main__":
    sys.exit(main())
