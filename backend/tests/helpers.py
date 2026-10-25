"""Shared test helpers (no DB access at import time)."""

import secrets
import string
from pathlib import Path

from alembic.config import Config

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# Dummy values for tests only; never real credentials.
DUMMY_PEPPER = "p" * 40
DUMMY_TOKEN = "t" * 40
UNREACHABLE_DB_URL = "postgresql+psycopg://user:pw@127.0.0.1:1/none"


def letters(n: int = 12) -> str:
    """A random lowercase suffix for test identifiers (provider names, versions). Letters
    only: random hex can contain an 11-digit run, which the R08 rule drops as a phone- or
    ID-like value (TELEMETRY_CONTRACT.md §3.1), so the identifier would vanish at random."""
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(n))


def make_settings(database_url: str = UNREACHABLE_DB_URL, **overrides) -> Settings:
    """Settings independent of the developer's .env and LLMI_* environment."""
    values = {
        "database_url": database_url,
        "key_pepper": DUMMY_PEPPER,
        "admin_token": DUMMY_TOKEN,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def alembic_config(url: str) -> Config:
    """Alembic config for a test database; the URL is passed in memory, not via alembic.ini."""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = url
    cfg.attributes["configure_logger"] = False
    return cfg
