"""Load the SYNTHETIC golden dataset (`golden-synthetic-v1`, Phase 4C) into a project through the
real admin API, and optionally run one mock A/B experiment on it.

Run with the backend venv; the backend (`python -m app`) and PostgreSQL must be running:

    backend/.venv/Scripts/python scripts/dev/seed_golden_dataset.py --project <id> \\
        --approved-by <your name or handle> [--run-experiment --model <model>]

- The cases come from `backend/app/services/experiments/golden.py` and go through
  `POST /api/v1/datasets` and `POST /api/v1/datasets/{id}/cases` exactly like a person's cases:
  `approved: true` is sent because the person running this command approves them by doing so,
  and `--approved-by` records who that is (self-declared, like every approver label).
- `--run-experiment` sends `POST /api/v1/experiments` with `mode: "mock"` only. The mock
  provider is scripted; nothing real or paid is ever called.
- The admin token is read from the backend settings (`backend/.env`), sent only as a bearer
  header to a loopback backend, and never printed. Output is one JSON line of ids and counts.
- A dataset of that name already in the project is refused by the API (`409`); nothing is
  overwritten.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from app.core import exact_json  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.experiments import golden  # noqa: E402

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _post(client: httpx.Client, path: str, project: int, payload: dict) -> httpx.Response:
    return client.post(
        path,
        params={"project": project},
        content=exact_json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", type=int, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--run-experiment", action="store_true")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    if urlsplit(args.api).hostname not in LOOPBACK:
        print("error: --api must be a loopback URL", file=sys.stderr)
        return 2
    if args.run_experiment and not args.model:
        print("error: --run-experiment needs --model", file=sys.stderr)
        return 2
    token = get_settings().admin_token.get_secret_value()
    out: dict = {"golden_version": golden.GOLDEN_VERSION}
    with httpx.Client(
        base_url=args.api, headers={"Authorization": f"Bearer {token}"}, timeout=60
    ) as client:
        r = _post(client, "/api/v1/datasets", args.project, {"name": golden.DATASET_NAME})
        if r.status_code != 201:
            print(f"error: creating the dataset failed: HTTP {r.status_code}", file=sys.stderr)
            return 1
        dataset = r.json()["id"]
        out["dataset_id"] = dataset
        case_ids = {}
        for case, payload in zip(
            golden.CASES, golden.case_payloads(args.approved_by), strict=True
        ):
            r = _post(client, f"/api/v1/datasets/{dataset}/cases", args.project, payload)
            if r.status_code != 201:
                print(f"error: case {case.key} refused: HTTP {r.status_code}", file=sys.stderr)
                return 1
            case_ids[case.key] = r.json()["id"]
        out["cases"] = case_ids
        if args.run_experiment:
            r = _post(
                client,
                "/api/v1/experiments",
                args.project,
                {
                    "dataset_id": dataset,
                    "prompt_version_a": golden.PROMPT_VERSION_A,
                    "prompt_version_b": golden.PROMPT_VERSION_B,
                    "model": args.model,
                    "mode": "mock",
                },
            )
            if r.status_code != 201:
                print(f"error: experiment refused: HTTP {r.status_code}", file=sys.stderr)
                return 1
            doc = r.json()
            out["experiment_id"] = doc["experiment"]["id"]
            out["variants"] = {
                v["prompt_version"]: {k: v[k] for k in ("passed", "failed", "not_evaluated")}
                for v in doc["variants"]
            }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
