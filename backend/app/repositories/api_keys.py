"""Projects and ingest keys (ARCHITECTURE.md §4.2–4.3). Only HMAC hashes are stored."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.core import security
from app.models import Project, ProjectApiKey


@dataclass(frozen=True)
class KeyLookup:
    key_id: int
    project_id: int
    key_hash: str
    revoked: bool


def find_by_prefix(session: Session, prefix: str) -> KeyLookup | None:
    row = session.execute(
        select(
            ProjectApiKey.id,
            ProjectApiKey.project_id,
            ProjectApiKey.key_hash,
            ProjectApiKey.revoked_at,
        ).where(ProjectApiKey.prefix == prefix)
    ).one_or_none()
    if row is None:
        return None
    return KeyLookup(row.id, row.project_id, row.key_hash, row.revoked_at is not None)


def get_or_create_project(session: Session, name: str) -> int:
    existing = session.execute(select(Project.id).where(Project.name == name)).scalar_one_or_none()
    if existing is not None:
        return existing
    return session.execute(insert(Project).values(name=name).returning(Project.id)).scalar_one()


def create_key(session: Session, project_id: int, pepper: bytes) -> security.GeneratedKey:
    generated = security.generate_key()
    session.execute(
        insert(ProjectApiKey).values(
            project_id=project_id,
            prefix=generated.prefix,
            key_hash=security.hash_key(pepper, generated.key),
        )
    )
    return generated


def revoke_key(session: Session, prefix: str) -> bool:
    """Set revoked_at once; returns False when no active key has this prefix."""
    result = session.execute(
        update(ProjectApiKey)
        .where(ProjectApiKey.prefix == prefix, ProjectApiKey.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )
    return result.rowcount == 1


@dataclass(frozen=True)
class KeyInfo:
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


def list_keys(session: Session, project_id: int) -> list[KeyInfo]:
    rows = session.execute(
        select(ProjectApiKey.prefix, ProjectApiKey.created_at, ProjectApiKey.revoked_at)
        .where(ProjectApiKey.project_id == project_id)
        .order_by(ProjectApiKey.created_at, ProjectApiKey.id)
    ).all()
    return [KeyInfo(r.prefix, r.created_at, r.revoked_at) for r in rows]
