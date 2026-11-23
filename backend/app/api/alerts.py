"""`GET /api/v1/alerts` (Phase 10F, ADR-59): threshold rules evaluated on the overview metrics.

Read-only; nothing is delivered anywhere. Same authentication, project scoping, window rules,
snapshot read and `no-store` as the other reads. A malformed rules file is a server
configuration error (`500 internal_error`, the cause logged without content), never a silent
"ok".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, not_found, require_admin
from app.api.traces import ProjectParam, iso_from_datetime, read_snapshot, resolve_window
from app.repositories import traces as trace_repo
from app.repositories.metrics import MetricsFilter
from app.services import alerts, metrics

router = APIRouter(prefix="/api/v1", tags=["alerts"], dependencies=[Depends(require_admin)])
logger = logging.getLogger("app.api")


@router.get("/alerts")
async def list_alerts(
    request: Request,
    project: ProjectParam,
    from_: Annotated[str | None, Query(alias="from", min_length=1, max_length=64)] = None,
    to: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> JSONResponse:
    """Every rule's state for the window: `firing`, `ok` or `insufficient_data`."""
    start, end = resolve_window(from_, to, datetime.now(UTC))
    try:
        rules = alerts.load_rules()
    except alerts.RulesError as exc:
        logger.error("alert rules unusable: %s", exc)
        raise
    f = MetricsFilter(project, start, end)

    def read(session: Session) -> dict[str, Any]:
        if not trace_repo.project_exists(session, project):
            raise not_found()
        return metrics.overview(session, f)["inference"]

    inference = await run_in_threadpool(read_snapshot, request, read)
    states = alerts.evaluate(rules, inference)
    return JSONResponse(
        {
            "project": project,
            "window": {"from": iso_from_datetime(start), "to": iso_from_datetime(end)},
            "contract_version": alerts.CONTRACT,
            "delivery": "none",
            "rules": states,
            "firing": sum(s["state"] == "firing" for s in states),
        },
        headers=NO_STORE,
    )
