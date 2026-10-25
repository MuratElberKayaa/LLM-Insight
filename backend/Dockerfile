# LLM Insight backend image for the opt-in compose `app` profile (Phase 3C).
# Build context: the repository root (see docker-compose.yml). Only backend/ is copied
# (.dockerignore); no .env, key or database dump enters the image. Configuration comes from
# the environment that compose passes explicitly.
FROM python:3.12.6-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend
# The full lock (runtime + dev, CR-09 pins); psycopg-binary needs no system libpq.
COPY backend/requirements.lock ./requirements.lock
RUN python -m pip install --no-deps -r requirements.lock && python -m pip check

COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/app ./app

RUN useradd --system --uid 10001 --home-dir /nonexistent llminsight
USER 10001

# Loopback bind inside the container (the config refuses anything else, ADR-08): only
# processes in this container's network namespace (the compose `frontend`) can reach it.
CMD ["sh", "-c", "alembic upgrade head && exec python -m app"]
