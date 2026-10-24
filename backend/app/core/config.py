"""Typed backend configuration (LLMI_* environment variables, see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root: backend/app/core/config.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"

PLACEHOLDER = "CHANGE_ME"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLMI_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds compose, SDK and frontend variables
        hide_input_in_errors=True,  # a rejected secret must not appear in tracebacks/logs
    )

    database_url: SecretStr
    bind_host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    # Not used before Phase 1B (ingest keys, /api/v1 auth); validated now so a
    # placeholder never reaches a running backend.
    key_pepper: SecretStr
    admin_token: SecretStr

    # Contract defaults: TELEMETRY_CONTRACT.md / ADR-17 / SECURITY.md §4. Enforced in Phase 1B.
    otlp_max_body_bytes: int = Field(default=4 * 1024 * 1024, gt=0)
    otlp_max_decompressed_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    otlp_max_spans_per_request: int = Field(default=2048, gt=0)
    rate_limit_ingest_rps: float = Field(default=20, gt=0)
    rate_limit_ingest_burst: int = Field(default=40, gt=0)
    rate_limit_api_rps: float = Field(default=10, gt=0)
    rate_limit_api_burst: int = Field(default=20, gt=0)
    rate_limit_authfail_per_min: int = Field(default=30, gt=0)
    retention_days: int = Field(default=14, gt=0)
    allow_real_llm: bool = False

    @field_validator("database_url")
    @classmethod
    def _database_url(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if not raw.startswith("postgresql+psycopg://"):
            raise ValueError("must use the postgresql+psycopg:// driver")
        if f":{PLACEHOLDER}@" in raw:
            raise ValueError("still contains the CHANGE_ME placeholder password")
        return v

    @field_validator("bind_host")
    @classmethod
    def _local_only(cls, v: str) -> str:
        # ADR-08 / SECURITY.md §7: local-only until real auth and a threat review exist.
        if v not in LOOPBACK_HOSTS:
            raise ValueError("must be a loopback address (local-only MVP)")
        return v

    @field_validator("key_pepper", "admin_token")
    @classmethod
    def _strong_secret(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw == PLACEHOLDER:
            raise ValueError("is still the CHANGE_ME placeholder")
        if len(raw.encode("utf-8")) < 32:
            raise ValueError("must be at least 32 bytes")
        return v

    @field_validator("otlp_max_decompressed_bytes")
    @classmethod
    def _decompressed_not_below_wire(cls, v: int, info: ValidationInfo) -> int:
        wire = info.data.get("otlp_max_body_bytes")
        if wire is not None and v < wire:
            raise ValueError("must be >= otlp_max_body_bytes")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
