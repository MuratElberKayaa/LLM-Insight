"""Reproducible, test-owned E2E fixtures for the dashboard (Phase 3C, R41).

Run with the backend venv (it has the app, `opentelemetry-proto` and `requests`); the backend
(`python -m app`) and PostgreSQL must be running:

    backend/.venv/Scripts/python scripts/dev/e2e_fixtures.py setup --state <file>
    backend/.venv/Scripts/python scripts/dev/e2e_fixtures.py teardown --state <file>

`setup` creates three projects owned by this run, named `e2e-<run>-main`, `e2e-<run>-foreign` and
`e2e-<run>-xss <hostile label>` (12 random hex characters per run, so no existing project is
reused), mints one ingest key per seeded project in memory, sends the SYNTHETIC sets of
`seed_trace_fixtures.py` through the real `POST /v1/traces` (`edge` + `phase3c` to main,
`isolation` to foreign; ids derived from the run, timestamps a few minutes before now, UTC),
revokes the keys and writes the state file: run id, project ids and names. No key, token or
password is printed or written. A failed setup removes what it created.

`teardown` deletes exactly the projects in the state file, and only when id **and** name
still match the recorded run (the cascade removes their keys, traces, spans and usage rows).
Nothing else is touched: no other project, no price row, no schema object. Set
`LLMI_E2E_KEEP=1` to keep the data (for screenshots); the state file then says so.
No provider is called and nothing claims `real`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings
from app.core.db import create_db_engine, create_session_factory
from app.models import Project
from app.repositories import api_keys
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

SEEDER = ROOT / "scripts" / "dev" / "seed_trace_fixtures.py"
HOSTILE = "<script>window.__llmiXss=1</script><img src=x onerror=window.__llmiXss=1>"
RUN_RE = re.compile(r"^[0-9a-f]{12}$")


def names(run: str) -> dict[str, str]:
    return {
        "main": f"e2e-{run}-main",
        "foreign": f"e2e-{run}-foreign",
        "hostile": f"e2e-{run}-xss {HOSTILE}",
    }


def owned(run: str, role: str, name: str) -> bool:
    return RUN_RE.match(run) is not None and names(run).get(role) == name


def _seed(key: str, set_name: str, seed: str, endpoint: str) -> dict:
    env = {**os.environ, "LLMI_SEED_PROJECT_KEY": key, "LLMI_SEED_ENDPOINT": endpoint}
    out = subprocess.run(
        [sys.executable, str(SEEDER), "--set", set_name, "--seed", seed],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if key in out.stdout or key in out.stderr:  # never expected; refuse to pass it on
        raise RuntimeError("the seeder echoed its key")
    if out.returncode != 0:
        raise RuntimeError(
            f"seeder --set {set_name} failed: {out.stderr.strip()[:300]}"
        )
    return json.loads(out.stdout.strip().splitlines()[-1])


def _delete(session_factory, projects: dict[str, dict]) -> list[int]:
    removed: list[int] = []
    with session_factory() as s, s.begin():
        for p in projects.values():
            n = s.execute(
                delete(Project).where(Project.id == p["id"], Project.name == p["name"])
            ).rowcount
            if n == 1:
                removed.append(p["id"])
    return removed


def setup(state_path: Path, endpoint: str) -> int:
    settings = get_settings()
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    run = secrets.token_hex(6)
    pepper = settings.key_pepper.get_secret_value().encode("utf-8")
    created: dict[str, dict] = {}
    done = False
    try:
        keys: dict[str, tuple[str, str]] = {}
        with factory() as s, s.begin():
            for role, name in names(run).items():
                if (
                    s.execute(select(Project.id).where(Project.name == name)).first()
                    is not None
                ):
                    raise RuntimeError("a project with this run's name already exists")
                pid = api_keys.get_or_create_project(s, name)
                created[role] = {"id": pid, "name": name}
                if role != "hostile":
                    k = api_keys.create_key(s, pid, pepper)
                    keys[role] = (k.key, k.prefix)
        traces: dict[str, str] = {}
        rejected = 0
        for role, set_name in (
            ("main", "edge"),
            ("main", "phase3c"),
            ("foreign", "isolation"),
        ):
            result = _seed(keys[role][0], set_name, f"e2e-{run}-{set_name}", endpoint)
            rejected += result["rejected_spans"]
            traces.update(result["traces"])
        with factory() as s, s.begin():
            for _key, prefix in keys.values():
                api_keys.revoke_key(s, prefix)
        keys.clear()
        if rejected:
            raise RuntimeError(f"ingest rejected {rejected} spans")
        state = {
            "run": run,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "projects": created,
            "traces": traces,
            "provenance": "SYNTHETIC (scripts/dev/seed_trace_fixtures.py); nothing claims real",
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "run": run,
                    "projects": {r: p["id"] for r, p in created.items()},
                    "traces": len(traces),
                }
            )
        )
        done = True
        return 0
    except RuntimeError as exc:
        print(f"error: e2e setup failed: {str(exc)[:300]}", file=sys.stderr)
        return 1
    finally:
        if (
            not done
        ):  # any failure, including an unexpected one: remove this run's projects
            _delete(factory, created)
        engine.dispose()


def teardown(state_path: Path) -> int:
    if not state_path.exists():
        print("nothing to tear down (no state file)")
        return 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    run = state.get("run", "")
    projects = state.get("projects", {})
    for role, p in projects.items():
        if not owned(run, role, p.get("name", "")) or not isinstance(p.get("id"), int):
            print(
                f"error: state entry {role!r} is not owned by an e2e run; nothing deleted",
                file=sys.stderr,
            )
            return 2
    if os.environ.get("LLMI_E2E_KEEP") == "1":
        print(
            json.dumps({"run": run, "kept": sorted(p["id"] for p in projects.values())})
        )
        return 0
    engine = create_db_engine(get_settings())
    try:
        removed = _delete(create_session_factory(engine), projects)
        with Session(engine) as s:
            ids = [p["id"] for p in projects.values()]
            left = list(
                s.execute(select(Project.id).where(Project.id.in_(ids))).scalars()
            )
    finally:
        engine.dispose()
    state_path.unlink()
    print(json.dumps({"run": run, "deleted": sorted(removed), "remaining": left}))
    return 0 if not left else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("setup", "teardown"))
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args(argv)
    endpoint = os.environ.get("LLMI_SEED_ENDPOINT", "http://127.0.0.1:8000/v1/traces")
    return (
        setup(args.state, endpoint) if args.command == "setup" else teardown(args.state)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
