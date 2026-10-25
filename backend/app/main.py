"""FastAPI application factory."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    admin,
    datasets,
    diagnostics,
    experiments,
    health,
    otel,
    overview,
    projects,
    reviews,
    traces,
)
from app.core.config import Settings, get_settings
from app.core.db import create_db_engine, create_session_factory
from app.core.ratelimit import Clock, KeyedTokenBucket
from app.services.ingestion.service import Ingestor


def create_app(settings: Settings | None = None, clock: Clock = time.monotonic) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Creating the engine does not connect: the app starts with the DB down
        # and /health/ready reports 503 until it is reachable.
        engine = create_db_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        # In-process buckets (CR-08): valid for this single worker only. Auth failures on
        # /v1/traces and /api/v1 share one bucket per client address.
        authfail_limiter = KeyedTokenBucket(
            settings.rate_limit_authfail_per_min / 60.0,
            settings.rate_limit_authfail_per_min,
            clock,
        )
        app.state.ingestor = Ingestor(
            settings,
            app.state.session_factory,
            authfail_limiter=authfail_limiter,
            ingest_limiter=KeyedTokenBucket(
                settings.rate_limit_ingest_rps, settings.rate_limit_ingest_burst, clock
            ),
        )
        app.state.admin_guard = admin.AdminGuard(
            settings,
            authfail_limiter=authfail_limiter,
            api_limiter=KeyedTokenBucket(
                settings.rate_limit_api_rps, settings.rate_limit_api_burst, clock
            ),
        )
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="LLM Insight", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_exception_handler(admin.APIError, admin.api_error_handler)
    app.add_exception_handler(RequestValidationError, admin.validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, admin.http_error_handler)
    app.include_router(health.router)
    app.include_router(otel.router)
    app.include_router(traces.router)
    app.include_router(diagnostics.router)
    app.include_router(overview.router)
    app.include_router(projects.router)
    app.include_router(reviews.router)
    app.include_router(datasets.router)
    app.include_router(experiments.router)
    return app
