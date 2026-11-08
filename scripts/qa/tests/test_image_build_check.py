"""Offline tests of scripts/qa/image_build_check.py and static checks of the Docker inputs (R104-4).

Stdlib only: no Docker, no network, no process. The dockerignore cases use a small matcher that
follows Docker's documented rules (last match wins, a pattern also covers children); they are
static checks, not a real build context. Values are synthetic and assembled at run time.

Run: python -I -m unittest discover -s scripts/qa/tests -p test_image_build_check.py -v
"""

import contextlib
import gzip
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

QA = Path(__file__).resolve().parents[1]
REPO = QA.parents[1]
sys.path.insert(0, str(QA))

import image_build_check as check

PROJECT = "llmi-r1044-12345"
GOOD_ENV = {
    name: f"synthetic-{index}-value-00000000"
    for index, name in enumerate(check.REQUIRED_ENV)
}
GOOD_ENV.update(LLMI_ALLOW_REAL_LLM="false", OPENAI_API_KEY="")
BACKEND_FILES = list(check.IMAGES["backend"]["required"])


def kinds(problems):
    return {p["kind"] for p in problems}


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)

    def write_env(self, values):
        path = self.tmp / "env"
        path.write_text(
            "\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8"
        )
        return path


class EnvChecks(Base):
    def test_valid_env_passes(self):
        self.assertEqual(check.check_env(self.write_env(GOOD_ENV), {}), [])

    def test_every_required_value_is_enforced(self):
        for name in check.REQUIRED_ENV:
            for mutated in (
                {k: v for k, v in GOOD_ENV.items() if k != name},
                {**GOOD_ENV, name: ""},
                {**GOOD_ENV, name: "CHANGE_ME"},
            ):
                with self.subTest(name):
                    self.assertIn(
                        "ENV_REQUIRED_MISSING",
                        kinds(check.check_env(self.write_env(mutated), {})),
                    )

    def test_provider_credentials_are_refused(self):
        for name in check.PROVIDER_ENV:
            with self.subTest(name):
                found = check.check_env(self.write_env({**GOOD_ENV, name: "x"}), {})
                self.assertIn("ENV_PROVIDER_VALUE", kinds(found))
                found = check.check_env(self.write_env(GOOD_ENV), {name: "x"})
                self.assertIn("ENV_PROVIDER_VALUE", kinds(found))

    def test_real_llm_is_refused(self):
        self.assertIn(
            "ENV_REAL_LLM",
            kinds(
                check.check_env(
                    self.write_env({**GOOD_ENV, "LLMI_ALLOW_REAL_LLM": "true"}), {}
                )
            ),
        )
        self.assertIn(
            "ENV_REAL_LLM",
            kinds(
                check.check_env(
                    self.write_env({**GOOD_ENV, "LLMI_PROVIDER_MODE": "real"}), {}
                )
            ),
        )

    def test_missing_file_fails(self):
        self.assertEqual(
            kinds(check.check_env(self.tmp / "absent", {})), {"ENV_MISSING"}
        )

    def test_values_never_appear_in_problems(self):
        value = "visible-value-must-not-print"
        found = check.check_env(
            self.write_env({**GOOD_ENV, "OPENAI_API_KEY": value}), {}
        )
        self.assertNotIn(value, json.dumps(found))


class ConfigChecks(Base):
    def model(self):
        root = str(self.tmp)
        return {
            "name": PROJECT,
            "services": {
                "postgres": {"image": "postgres:17"},
                "backend": {
                    "build": {"context": root, "dockerfile": "backend/Dockerfile"},
                    "profiles": ["app"],
                    "environment": {"LLMI_ALLOW_REAL_LLM": "false"},
                },
                "frontend": {
                    "build": {
                        "context": os.path.join(root, "frontend"),
                        "dockerfile": "Dockerfile",
                    },
                    "profiles": ["app"],
                    "environment": {},
                },
            },
        }

    def run_check(self, model, project=PROJECT, images=None):
        (self.tmp / "c.json").write_text(json.dumps(model), encoding="utf-8")
        listed = (
            images
            if images is not None
            else [f"{PROJECT}-backend", f"{PROJECT}-frontend", "postgres:17"]
        )
        (self.tmp / "i.txt").write_text("\n".join(listed), encoding="utf-8")
        return check.check_config(
            self.tmp / "c.json", self.tmp / "i.txt", project, str(self.tmp)
        )

    def test_valid_model_passes(self):
        self.assertEqual(self.run_check(self.model()), [])

    def test_project_rules(self):
        for bad in (
            "llminsight",
            "feature/x",
            "LLMI-R1044-1",
            "llmi-r1044-",
            "llmi-r1044-abc",
            "",
        ):
            with self.subTest(bad):
                self.assertEqual(
                    kinds(self.run_check(self.model(), project=bad)),
                    {"PROJECT_INVALID"},
                )

    def test_default_project_in_output_fails(self):
        model = {**self.model(), "name": "llminsight"}
        self.assertIn("PROJECT_MISMATCH", kinds(self.run_check(model)))

    def test_build_rules(self):
        cases = {
            "CONFIG_BUILD_KEYS": lambda m: m["services"]["backend"]["build"].update(
                args={"X": "1"}
            ),
            "CONFIG_BUILD_CONTEXT": lambda m: m["services"]["frontend"]["build"].update(
                context=str(self.tmp / "other")
            ),
            "CONFIG_DOCKERFILE": lambda m: m["services"]["backend"]["build"].update(
                dockerfile="Other"
            ),
            "CONFIG_IMAGE_TARGET": lambda m: m["services"]["backend"].update(
                image="registry.invalid/x"
            ),
            "CONFIG_PROFILE": lambda m: m["services"]["frontend"].update(profiles=[]),
            "CONFIG_BUILD_POSTGRES": lambda m: m["services"]["postgres"].update(
                build={"context": "."}
            ),
            "ENV_REAL_LLM": lambda m: m["services"]["backend"]["environment"].update(
                LLMI_ALLOW_REAL_LLM="true"
            ),
            "CONFIG_PROVIDER_ENV": lambda m: m["services"]["backend"][
                "environment"
            ].update(OPENAI_API_KEY="${X}"),
        }
        for kind, change in cases.items():
            with self.subTest(kind):
                model = self.model()
                change(model)
                self.assertIn(kind, kinds(self.run_check(model)))

    def test_secrets_key_in_build_fails(self):
        model = self.model()
        model["services"]["frontend"]["build"]["secrets"] = ["x"]
        self.assertIn("CONFIG_BUILD_KEYS", kinds(self.run_check(model)))

    def test_unexpected_service_fails(self):
        model = self.model()
        model["services"]["extra"] = {"image": "x"}
        self.assertEqual(kinds(self.run_check(model)), {"CONFIG_SERVICES"})

    def test_missing_image_name_fails(self):
        self.assertIn(
            "CONFIG_IMAGE_NAME",
            kinds(self.run_check(self.model(), images=[f"{PROJECT}-backend"])),
        )

    def test_malformed_outputs_fail(self):
        (self.tmp / "c.json").write_text("{", encoding="utf-8")
        (self.tmp / "i.txt").write_text("x", encoding="utf-8")
        self.assertEqual(
            kinds(
                check.check_config(
                    self.tmp / "c.json", self.tmp / "i.txt", PROJECT, "."
                )
            ),
            {"CONFIG_MALFORMED"},
        )
        self.assertEqual(
            kinds(
                check.check_config(
                    self.tmp / "absent", self.tmp / "i.txt", PROJECT, "."
                )
            ),
            {"CONFIG_MALFORMED"},
        )


class Plant(Base):
    def test_refused_outside_ci(self):
        self.assertEqual(
            kinds(check.plant(self.tmp, self.tmp / "s.json", {})), {"PLANT_REFUSED"}
        )
        self.assertFalse((self.tmp / "frontend").exists())

    def test_writes_every_canary_without_printing(self):
        self.assertEqual(
            check.plant(self.tmp, self.tmp / "s.json", {"GITHUB_ACTIONS": "true"}), []
        )
        canary = json.loads((self.tmp / "s.json").read_text(encoding="utf-8"))["canary"]
        for rel in check.CANARY_PATHS:
            self.assertIn(canary, (self.tmp / rel).read_text(encoding="utf-8"))

    def test_never_overwrites_an_existing_file(self):
        target = self.tmp / "LLM_INSIGHT_HANDOFF.md"
        target.write_text("user note\n", encoding="utf-8")
        self.assertEqual(
            kinds(
                check.plant(self.tmp, self.tmp / "s.json", {"GITHUB_ACTIONS": "true"})
            ),
            {"PLANT_PATH_EXISTS"},
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "user note\n")


class ImageChecks(Base):
    def setUp(self):
        super().setUp()
        self.canary = "llmi-canary-" + "ab" * 32
        (self.tmp / "s.json").write_text(
            json.dumps({"canary": self.canary}), encoding="utf-8"
        )
        self.env = self.write_env(GOOD_ENV)

    def layer(self, files, compress=False, dirs=()):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for name in dirs:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return gzip.compress(buffer.getvalue()) if compress else buffer.getvalue()

    def image(self, files, compress=False, meta=b"{}", dirs=()):
        path = self.tmp / "image.tar"
        with tarfile.open(path, "w") as tar:
            for name, data in (
                ("blobs/sha256/one", self.layer(files, compress, dirs)),
                ("manifest.json", meta),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return path

    def run_check(self, files, name="backend", config=None, **kwargs):
        (self.tmp / "c.json").write_text(
            json.dumps(
                config
                if config is not None
                else {"User": "10001", "Env": ["PATH=/usr/bin"]}
            ),
            encoding="utf-8",
        )
        return check.check_image(
            name,
            self.image(files, **kwargs),
            self.tmp / "c.json",
            self.tmp / "s.json",
            self.env,
        )[0]

    def good(self):
        return {name: b"synthetic\n" for name in BACKEND_FILES}

    def test_valid_image_passes(self):
        self.assertEqual(self.run_check(self.good()), [])

    def test_compressed_layer_is_read(self):
        self.assertEqual(self.run_check(self.good(), compress=True), [])
        self.assertIn(
            "IMAGE_VALUE_FOUND",
            kinds(
                self.run_check(
                    {**self.good(), "app/x": self.canary.encode()}, compress=True
                )
            ),
        )

    def test_excluded_files_fail(self):
        for path in (
            "app/.env",
            "app/backend/app/.env.local",
            "app/LLM_INSIGHT_HANDOFF.md",
            "app/docs/OPUS_PHASE7_PROMPTS.md",
            "app/.git/config",
            "app/.venv/x",
            "app/backend/server.pem",
            "app/data.sqlite",
            "app/.ci-canary/.env",
            "app/.ci-canary/.env.local",
        ):
            with self.subTest(path):
                self.assertIn(
                    "IMAGE_EXCLUDED_FILE",
                    kinds(self.run_check({**self.good(), path: b"x"})),
                )

    def test_empty_canary_directory_is_not_a_leak(self):
        # Regression, GitHub run 42505551056: .dockerignore drops the file, not its directory.
        dirs = ["app/backend/app/.ci-canary/"]
        self.assertEqual(self.run_check(self.good(), dirs=dirs), [])

    def test_third_party_env_files_are_not_application_files(self):
        self.assertEqual(
            self.run_check(
                {
                    **self.good(),
                    "app/node_modules/pkg/.env": b"x",
                    "usr/local/lib/x/.env": b"x",
                }
            ),
            [],
        )

    def test_canary_in_content_fails(self):
        self.assertIn(
            "IMAGE_VALUE_FOUND",
            kinds(
                self.run_check(
                    {**self.good(), "app/x.js": b"a" + self.canary.encode() + b"b"}
                )
            ),
        )

    def test_canary_spanning_a_read_chunk_fails(self):
        data = b"x" * (check.CHUNK - 10) + self.canary.encode()
        self.assertIn(
            "IMAGE_VALUE_FOUND",
            kinds(self.run_check({**self.good(), "app/big.bin": data})),
        )

    def test_generated_value_in_content_fails(self):
        self.assertIn(
            "IMAGE_VALUE_FOUND",
            kinds(
                self.run_check(
                    {**self.good(), "usr/x": GOOD_ENV["LLMI_ADMIN_TOKEN"].encode()}
                )
            ),
        )

    def test_value_in_metadata_or_configuration_fails(self):
        self.assertIn(
            "IMAGE_VALUE_FOUND",
            kinds(self.run_check(self.good(), meta=self.canary.encode())),
        )
        config = {"User": "10001", "Labels": {"a": GOOD_ENV["POSTGRES_PASSWORD"]}}
        self.assertIn(
            "IMAGE_VALUE_FOUND", kinds(self.run_check(self.good(), config=config))
        )

    def test_missing_output_fails(self):
        files = self.good()
        files.pop(BACKEND_FILES[0])
        self.assertIn("IMAGE_OUTPUT_MISSING", kinds(self.run_check(files)))

    def test_wrong_user_fails(self):
        self.assertIn(
            "IMAGE_USER", kinds(self.run_check(self.good(), config={"User": "root"}))
        )
        self.assertIn("IMAGE_USER", kinds(self.run_check(self.good(), config={})))

    def test_secret_env_name_fails_without_printing_the_value(self):
        value = "visible-value-must-not-print"
        found = self.run_check(
            self.good(), config={"User": "10001", "Env": [f"LLMI_ADMIN_TOKEN={value}"]}
        )
        self.assertIn("IMAGE_SECRET_ENV", kinds(found))
        self.assertNotIn(value, json.dumps(found))

    def test_invalid_inputs_fail(self):
        (self.tmp / "c.json").write_text("{}", encoding="utf-8")
        (self.tmp / "bad.tar").write_bytes(b"not a tar")
        call = check.check_image
        self.assertEqual(
            kinds(
                call(
                    "backend",
                    self.tmp / "bad.tar",
                    self.tmp / "c.json",
                    self.tmp / "s.json",
                    self.env,
                )[0]
            )
            & {"IMAGE_TAR_INVALID"},
            {"IMAGE_TAR_INVALID"},
        )
        self.assertEqual(
            kinds(
                call(
                    "unknown",
                    self.tmp / "bad.tar",
                    self.tmp / "c.json",
                    self.tmp / "s.json",
                    self.env,
                )[0]
            ),
            {"IMAGE_UNKNOWN"},
        )
        self.assertEqual(
            kinds(
                call(
                    "backend",
                    self.tmp / "bad.tar",
                    self.tmp / "c.json",
                    self.tmp / "absent",
                    self.env,
                )[0]
            ),
            {"IMAGE_STATE_MISSING"},
        )
        (self.tmp / "c.json").write_text("{", encoding="utf-8")
        self.assertEqual(
            kinds(
                call(
                    "backend",
                    self.tmp / "bad.tar",
                    self.tmp / "c.json",
                    self.tmp / "s.json",
                    self.env,
                )[0]
            ),
            {"IMAGE_CONFIG_MALFORMED"},
        )

    def test_image_without_layers_fails(self):
        path = self.tmp / "empty.tar"
        with tarfile.open(path, "w"):
            pass
        (self.tmp / "c.json").write_text(
            json.dumps({"User": "10001"}), encoding="utf-8"
        )
        found = check.check_image(
            "backend", path, self.tmp / "c.json", self.tmp / "s.json", self.env
        )[0]
        self.assertIn("IMAGE_NO_LAYERS", kinds(found))

    def test_cli_exit_status_and_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check.main(
                [
                    "env",
                    "--env",
                    str(self.write_env({**GOOD_ENV, check.REQUIRED_ENV[1]: ""})),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn(
            "::error title=image-build::ENV_REQUIRED_MISSING POSTGRES_PASSWORD",
            output.getvalue(),
        )


def ignored(patterns, path):
    """Docker's rule: patterns apply in order, the last match wins, a match also covers children."""

    def to_regex(pattern):
        out, i = "", 0
        while i < len(pattern):
            if pattern.startswith("**/", i):
                out, i = out + "(?:.*/)?", i + 3
            elif pattern.startswith("**", i):
                out, i = out + ".*", i + 2
            elif pattern[i] == "*":
                out, i = out + "[^/]*", i + 1
            elif pattern[i] == "?":
                out, i = out + "[^/]", i + 1
            else:
                out, i = out + re.escape(pattern[i]), i + 1
        return re.compile(out + r"\Z")

    parts = path.split("/")
    candidates = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    result = False
    for raw in patterns:
        negate = raw.startswith("!")
        regex = to_regex(raw[1:].rstrip("/") if negate else raw.rstrip("/"))
        if any(regex.match(candidate) for candidate in candidates):
            result = not negate
    return result


def patterns_of(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


class DockerInputs(unittest.TestCase):
    def test_root_context_excludes_sensitive_paths(self):
        patterns = patterns_of(REPO / ".dockerignore")
        self.assertEqual(patterns[0], "*")
        for path in (
            ".env",
            ".env.local",
            ".git/config",
            "LLM_INSIGHT_HANDOFF.md",
            "docs/PHASE7_LIVE_DEMO_PLAN.md",
            "docs/OPUS_PHASE7_PROMPTS.md",
            "backend/.env",
            "backend/.venv/pyvenv.cfg",
            "backend/tests/unit/x.py",
            "frontend/.env.local",
            "frontend/node_modules/x",
            ".tools/node",
            "backend/app/.env",
            "backend/app/.env.local",
            "backend/app/sub/.env",
            "backend/alembic/.env.dev",
            "backend/app/__pycache__/x.pyc",
        ):
            with self.subTest(path):
                self.assertTrue(ignored(patterns, path))

    def test_root_context_keeps_what_the_backend_copies(self):
        patterns = patterns_of(REPO / ".dockerignore")
        for path in (
            "backend/requirements.lock",
            "backend/alembic.ini",
            "backend/alembic/env.py",
            "backend/app/main.py",
        ):
            with self.subTest(path):
                self.assertFalse(ignored(patterns, path))

    def test_frontend_context_excludes_sensitive_paths(self):
        patterns = patterns_of(REPO / "frontend/.dockerignore")
        for path in (
            ".env",
            ".env.local",
            ".env.production",
            "sub/.env",
            "sub/dir/.env.local",
            "node_modules/x/y",
            ".next/BUILD_ID",
            ".test-build/x",
            "test-results/x",
            "playwright-report/x",
            "tsconfig.tsbuildinfo",
        ):
            with self.subTest(path):
                self.assertTrue(ignored(patterns, path))

    def test_frontend_context_keeps_the_sources(self):
        patterns = patterns_of(REPO / "frontend/.dockerignore")
        for path in (
            "package.json",
            "package-lock.json",
            ".npmrc",
            ".nvmrc",
            "next.config.ts",
            "scripts/next.mjs",
            "app/layout.tsx",
        ):
            with self.subTest(path):
                self.assertFalse(ignored(patterns, path))

    def test_every_tracked_style_env_file_stays_out_of_the_frontend(self):
        self.assertFalse(list((REPO / "frontend").glob("app/**/.env*")))

    def dockerfile(self, rel):
        return (REPO / rel).read_text(encoding="utf-8")

    def test_dockerfiles_are_pinned_and_carry_no_build_secret(self):
        for rel in ("backend/Dockerfile", "frontend/Dockerfile"):
            text = self.dockerfile(rel)
            with self.subTest(rel):
                bases = re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE)
                self.assertTrue(bases)
                for base in bases:
                    self.assertRegex(base, r":\d+\.\d+\.\d+-")
                self.assertIsNone(re.search(r"^\s*(ARG|ADD)\b", text, re.MULTILINE))
                self.assertNotIn("--secret", text)
                self.assertRegex(
                    text.rsplit("FROM", 1)[1], r"(?m)^USER\s+(10001|node)\s*$"
                )

    def test_copy_sources_match_the_contexts(self):
        backend = re.findall(
            r"^COPY\s+(?!--)(\S+)", self.dockerfile("backend/Dockerfile"), re.MULTILINE
        )
        self.assertEqual(
            backend,
            [
                "backend/requirements.lock",
                "backend/alembic.ini",
                "backend/alembic",
                "backend/app",
            ],
        )
        final_stage = self.dockerfile("frontend/Dockerfile").rsplit("FROM", 1)[1]
        self.assertNotIn("COPY . .", final_stage)

    def test_compose_targets_and_default_name_unchanged(self):
        text = self.dockerfile("docker-compose.yml")
        self.assertRegex(text, r"(?m)^name: llminsight$")
        self.assertRegex(
            text,
            r"(?ms)^  backend:.*?build:\n\s+context: \.\n\s+dockerfile: backend/Dockerfile",
        )
        self.assertRegex(text, r"(?ms)^  frontend:.*?build:\n\s+context: \./frontend\n")
        self.assertRegex(text, r"(?m)^    image: postgres:17$")
        self.assertIsNone(re.search(r"(?m)^\s+(args|secrets):", text))

    def test_workflow_job_shape(self):
        text = self.dockerfile(".github/workflows/ci.yml").replace("\r\n", "\n")
        jobs = re.findall(r"(?m)^  ([a-z-]+):$", text.split("\njobs:\n", 1)[1])
        self.assertEqual(
            jobs,
            [
                "docs",
                "python",
                "frontend",
                "security-python",
                "security-history",
                "image-build",
            ],
        )
        job = text[text.index("\n  image-build:\n") :]
        self.assertIn("runs-on: ubuntu-24.04", job)
        self.assertRegex(job, r"timeout-minutes: \d+")
        self.assertRegex(job, r"permissions:\n\s+contents: read\n")
        self.assertIn("persist-credentials: false", job)
        self.assertEqual(
            re.findall(r"uses: (\S+)", job),
            ["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"],
        )
        self.assertEqual(job.count("scripts/qa/image_build_gate.sh"), 3)
        for needle in (
            "continue-on-error",
            "secrets.",
            "if:",
            "docker compose",
            "docker push",
            "up -d",
        ):
            with self.subTest(needle):
                self.assertNotIn(needle, job)


if __name__ == "__main__":
    unittest.main()
