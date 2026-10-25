"""Read-only project listing for `GET /api/v1/projects` (Phase 3C, ARCHITECTURE.md §4.2).

Selects `id`, `name` and `created_at` only: never key prefixes, hashes or counts of other
tables. Ordered by the primary key, `limit + 1` rows so truncation is visible.
"""

from typing import Any

from sqlalchemy import Row, Select, select
from sqlalchemy.orm import Session

from app.models import Project


def list_projects_stmt(limit: int) -> Select[Any]:
    return (
        select(Project.id, Project.name, Project.created_at).order_by(Project.id).limit(limit + 1)
    )


def list_projects(session: Session, limit: int) -> list[Row[Any]]:
    return list(session.execute(list_projects_stmt(limit)))
