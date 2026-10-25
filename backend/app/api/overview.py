"""`GET /api/v1/overview` and `GET /api/v1/models/compare` (ARCHITECTURE.md §5, Phase 2C).

Aggregate metrics M01–M15 (`app/services/metrics.py`, METRICS_CATALOG.md). Same access
rules as the trace endpoints: `require_admin` (bearer token, server-side only, CR-06) runs
before any parameter is validated or any query runs; the mandatory `project` id scopes
every query; an unknown project is a `404` identical to the trace API's. Every request runs
in one read-only snapshot transaction.

Parameters (both endpoints): `project`; `from` / `to` (ISO-8601 UTC, `[from, to)` on event
time, at most 31 days, default the last 31 days); `model` (exact `response_model` or
`requested_model`); `status` (`ok` | `error`, applied to traces for trace-level metrics and
to inference attempts for usage-level metrics); `prompt_version`; `provenance` (`real` |
`synthetic` | `mixed` | `unknown`, applied at each metric's own level: no usage row is
`mixed`). `models/compare` adds `limit` (1–200, default 50 groups).
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, not_found, require_admin
from app.api.traces import ProjectParam, iso_from_datetime, read_snapshot, resolve_window
from app.repositories import traces as trace_repo
from app.repositories.metrics import MetricsFilter
from app.services import metrics

router = APIRouter(prefix="/api/v1", tags=["metrics"], dependencies=[Depends(require_admin)])

MAX_GROUPS = 200
DEFAULT_GROUPS = 50

FromParam = Annotated[str | None, Query(alias="from", min_length=1, max_length=64)]
ToParam = Annotated[str | None, Query(min_length=1, max_length=64)]
ModelParam = Annotated[str | None, Query(min_length=1, max_length=256)]
PromptVersionParam = Annotated[str | None, Query(min_length=1, max_length=64)]
StatusParam = Literal["ok", "error"] | None
ProvenanceParam = Literal["real", "synthetic", "mixed", "unknown"] | None


def _filter(
    project: int,
    from_: str | None,
    to: str | None,
    model: str | None,
    status: str | None,
    prompt_version: str | None,
    provenance: str | None,
) -> MetricsFilter:
    start, end = resolve_window(from_, to, datetime.now(UTC))
    return MetricsFilter(project, start, end, model, status, prompt_version, provenance)


async def _respond(request: Request, f: MetricsFilter, build) -> JSONResponse:
    def read(session: Session) -> dict[str, Any]:
        if not trace_repo.project_exists(session, f.project_id):
            raise not_found()
        return build(session)

    body = await run_in_threadpool(read_snapshot, request, read)
    return JSONResponse(
        {
            "project": f.project_id,
            "window": {
                "from": iso_from_datetime(f.started_from),
                "to": iso_from_datetime(f.started_to),
            },
            "filters": metrics.filters_echo(f),
            **body,
        },
        headers=NO_STORE,
    )


@router.get("/overview")
async def overview(
    request: Request,
    project: ProjectParam,
    from_: FromParam = None,
    to: ToParam = None,
    model: ModelParam = None,
    status: StatusParam = None,
    prompt_version: PromptVersionParam = None,
    provenance: ProvenanceParam = None,
) -> JSONResponse:
    """KPIs M01–M14 with `n`, the provenance split and null status, plus a trend."""
    f = _filter(project, from_, to, model, status, prompt_version, provenance)
    return await _respond(request, f, lambda session: metrics.overview(session, f))


@router.get("/models/compare")
async def models_compare(
    request: Request,
    project: ProjectParam,
    from_: FromParam = None,
    to: ToParam = None,
    model: ModelParam = None,
    status: StatusParam = None,
    prompt_version: PromptVersionParam = None,
    provenance: ProvenanceParam = None,
    limit: Annotated[int, Query(ge=1, le=MAX_GROUPS)] = DEFAULT_GROUPS,
) -> JSONResponse:
    """M15: per provider / requested model / response model / prompt version."""
    f = _filter(project, from_, to, model, status, prompt_version, provenance)
    return await _respond(request, f, lambda session: metrics.compare(session, f, limit))
