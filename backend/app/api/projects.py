"""`GET /api/v1/projects` (Phase 3C): the bounded project listing behind the dashboard's
project selector.

Same access rules as the other `/api/v1` endpoints: `require_admin` (bearer token, server-side
only, CR-06) runs before any parameter is validated or any query runs, and the request runs in
one read-only snapshot transaction. The single local admin token already reads every project
(CR-06, no per-project authorization in the MVP), so listing them reveals nothing that token
could not read anyway; it adds no new permission.

Output: `{items, truncated, limit}`; each item is `id`, `name` (as stored, rendered as text by
clients), `created_at` (ISO-8601 UTC `Z`). No key material or telemetry is returned.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, require_admin
from app.api.traces import iso_from_datetime, read_snapshot
from app.repositories import projects as repo

router = APIRouter(prefix="/api/v1", tags=["projects"], dependencies=[Depends(require_admin)])

MAX_PROJECTS = 200
DEFAULT_PROJECTS = 100


@router.get("/projects")
async def list_projects(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_PROJECTS)] = DEFAULT_PROJECTS,
) -> JSONResponse:
    """Projects in id order, at most `limit` (1–200, default 100); `truncated` when more exist."""

    def read(session: Session) -> list[Any]:
        return repo.list_projects(session, limit)

    rows = await run_in_threadpool(read_snapshot, request, read)
    return JSONResponse(
        {
            "items": [
                {"id": r.id, "name": r.name, "created_at": iso_from_datetime(r.created_at)}
                for r in rows[:limit]
            ],
            "truncated": len(rows) > limit,
            "limit": limit,
        },
        headers=NO_STORE,
    )
