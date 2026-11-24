"""`GET /api/v1/error-patterns` (Phase 10D, ADR-57): deterministic error fingerprints, read-only.

Same authentication, project scoping, window rules, snapshot read and `no-store` as the other
reads. `error_class` is the diagnostics engine's exact-match reading of `error.type` (a label,
not a root cause); every pattern is a count of stored ERROR spans, nothing more.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, not_found, require_admin
from app.api.traces import ProjectParam, iso_from_datetime, read_snapshot, resolve_window
from app.repositories import error_patterns as repo
from app.repositories import traces as trace_repo
from app.services.diagnostics.engine import error_class

router = APIRouter(prefix="/api/v1", tags=["diagnostics"], dependencies=[Depends(require_admin)])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _pattern(r: Any) -> dict[str, Any]:
    return {
        "source": r.source,
        "provider": r.provider,
        "model": r.model,
        "tool_name": r.tool_name,
        "error_type": r.error_type,
        "error_class": error_class(r.error_type),
        "spans": int(r.spans),
        "traces": int(r.traces),
        "first_seen": iso_from_datetime(r.first_seen),
        "last_seen": iso_from_datetime(r.last_seen),
        "latest_trace_id": r.trace_ids[0],
    }


@router.get("/error-patterns")
async def list_error_patterns(
    request: Request,
    project: ProjectParam,
    from_: Annotated[str | None, Query(alias="from", min_length=1, max_length=64)] = None,
    to: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> JSONResponse:
    """ERROR spans of traces starting in `[from, to)`, grouped by exact fingerprint."""
    start, end = resolve_window(from_, to, datetime.now(UTC))

    def read(session: Session) -> tuple[list[Any], int]:
        if not trace_repo.project_exists(session, project):
            raise not_found()
        return repo.list_patterns(session, project, start, end, limit)

    rows, total = await run_in_threadpool(read_snapshot, request, read)
    return JSONResponse(
        {
            "project": project,
            "window": {"from": iso_from_datetime(start), "to": iso_from_datetime(end)},
            "method": "exact_fingerprint",
            "fingerprint": ["source", "provider", "model", "tool_name", "error_type"],
            "error_spans": total,
            "patterns": [_pattern(r) for r in rows[:limit]],
            "truncated": len(rows) > limit,
            "limit": limit,
        },
        headers=NO_STORE,
    )
