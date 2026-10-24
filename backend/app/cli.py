"""Project / ingest-key administration (local only). Run from backend/:

    python -m app.cli create-key --project demo   # creates the project if needed
    python -m app.cli list-keys --project demo    # prefixes and dates only
    python -m app.cli revoke-key --prefix AbCd1234

The raw key is printed exactly once, by create-key, to stdout. Only its HMAC is stored.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory
from app.models import Project
from app.repositories import api_keys


def run(argv: Sequence[str], settings: Settings, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-key", help="create a project (if needed) and a new key")
    create.add_argument("--project", required=True)
    listing = sub.add_parser("list-keys", help="list key prefixes of a project")
    listing.add_argument("--project", required=True)
    revoke = sub.add_parser("revoke-key", help="revoke a key by its prefix")
    revoke.add_argument("--prefix", required=True)
    args = parser.parse_args(argv)

    engine = create_db_engine(settings)
    try:
        with create_session_factory(engine)() as session, session.begin():
            if args.command == "create-key":
                if not 1 <= len(args.project) <= 100:
                    print("error: project name must be 1-100 characters", file=sys.stderr)
                    return 2
                project_id = api_keys.get_or_create_project(session, args.project)
                pepper = settings.key_pepper.get_secret_value().encode("utf-8")
                generated = api_keys.create_key(session, project_id, pepper)
                print(f"project_id={project_id} prefix={generated.prefix}", file=out)
                print("X-Project-Key (shown once, store it now):", file=out)
                print(generated.key, file=out)
                return 0
            if args.command == "list-keys":
                project_id = session.execute(
                    select(Project.id).where(Project.name == args.project)
                ).scalar_one_or_none()
                if project_id is None:
                    print("error: unknown project", file=sys.stderr)
                    return 1
                for k in api_keys.list_keys(session, project_id):
                    state = f"revoked {k.revoked_at.isoformat()}" if k.revoked_at else "active"
                    print(f"{k.prefix} created {k.created_at.isoformat()} {state}", file=out)
                return 0
            if api_keys.revoke_key(session, args.prefix):
                print(f"revoked {args.prefix}", file=out)
                return 0
            print("error: no active key with that prefix", file=sys.stderr)
            return 1
    finally:
        engine.dispose()


def main() -> None:
    sys.exit(run(sys.argv[1:], get_settings()))


if __name__ == "__main__":
    main()
