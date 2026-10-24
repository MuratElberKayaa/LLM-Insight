"""FastAPI application factory."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, otel
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
        # In-process buckets (CR-08): valid for this single worker only.
        app.state.ingestor = Ingestor(
            settings,
            app.state.session_factory,
            authfail_limiter=KeyedTokenBucket(
                settings.rate_limit_authfail_per_min / 60.0,
                settings.rate_limit_authfail_per_min,
                clock,
            ),
            ingest_limiter=KeyedTokenBucket(
                settings.rate_limit_ingest_rps, settings.rate_limit_ingest_burst, clock
            ),
        )
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="LLM Insight", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(otel.router)
    return app
