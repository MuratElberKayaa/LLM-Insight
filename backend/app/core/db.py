"""SQLAlchemy engine/session setup (sync driver: psycopg 3)."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

# libpq connect timeout (seconds); the readiness probe applies its own 1 s bound on top.
CONNECT_TIMEOUT_S = 2


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
        hide_parameters=True,  # never echo bound values into logs or exceptions
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def ping(engine: Engine) -> None:
    """Run SELECT 1; raises on any connection or query failure."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1")).scalar_one()
