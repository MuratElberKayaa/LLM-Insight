"""`GET /api/v1/sessions` (Phase 10C, ADR-56): pseudonymous session aggregates, read-only.

Same authentication, project scoping, window rules, snapshot read and `no-store` as the other
read endpoints. Session values are keyed hashes (`s1_` + 32 hex), never raw conversation IDs.
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
from app.repositories import sessions as repo
from app.repositories import traces as trace_repo

router = APIRouter(prefix="/api/v1", tags=["sessions"], dependencies=[Depends(require_admin)])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _int(v: Any) -> int | None:
    return None if v is None else int(v)


def _session(r: Any) -> dict[str, Any]:
    unknown = int(r.unknown_cost_calls)
    return {
        "session": r.session,
        "traces": int(r.traces),
        "error_traces": int(r.error_traces),
        "first_seen": iso_from_datetime(r.first_seen),
        "last_seen": iso_from_datetime(r.last_seen),
        "attempts": int(r.attempts),
        "input_tokens": _int(r.input_tokens),
        "unknown_input_tokens": int(r.unknown_input_tokens),
        "output_tokens": _int(r.output_tokens),
        "unknown_output_tokens": int(r.unknown_output_tokens),
        "known_cost_usd": None if r.known_cost_usd is None else format(r.known_cost_usd, "f"),
        "computed_cost_calls": int(r.computed_cost_calls),
        "unknown_cost_calls": unknown,
        "cost_complete": int(r.attempts) > 0 and unknown == 0,
    }


@router.get("/sessions")
async def list_sessions(
    request: Request,
    project: ProjectParam,
    from_: Annotated[str | None, Query(alias="from", min_length=1, max_length=64)] = None,
    to: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> JSONResponse:
    """Sessions with at least one trace starting in `[from, to)`, most traces first."""
    start, end = resolve_window(from_, to, datetime.now(UTC))

    def read(session: Session) -> list[Any]:
        if not trace_repo.project_exists(session, project):
            raise not_found()
        return repo.list_sessions(session, project, start, end, limit)

    rows = await run_in_threadpool(read_snapshot, request, read)
    return JSONResponse(
        {
            "project": project,
            "window": {"from": iso_from_datetime(start), "to": iso_from_datetime(end)},
            "pseudonymous": True,
            "items": [_session(r) for r in rows[:limit]],
            "truncated": len(rows) > limit,
            "limit": limit,
        },
        headers=NO_STORE,
    )
