"""Liveness and readiness probes (ARCHITECTURE.md §5). Never traced."""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import ping

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

READY_TIMEOUT_S = 1.0
NO_STORE = {"Cache-Control": "no-store"}


@router.get("/live")
async def live() -> JSONResponse:
    """Process is up. No DB access."""
    return JSONResponse({"status": "live"}, headers=NO_STORE)


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """200 only if `SELECT 1` succeeds within 1 s, else 503."""
    engine = request.app.state.engine
    try:
        await asyncio.wait_for(asyncio.to_thread(ping, engine), timeout=READY_TIMEOUT_S)
    except (TimeoutError, SQLAlchemyError, OSError) as exc:
        # Class name only: driver messages can include host/user details.
        logger.warning("readiness check failed: %s", type(exc).__name__)
        return JSONResponse({"status": "not_ready"}, status_code=503, headers=NO_STORE)
    return JSONResponse({"status": "ready"}, headers=NO_STORE)
