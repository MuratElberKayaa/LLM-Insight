"""Declarative base, naming convention and the CHECK shorthands of ARCHITECTURE.md §4.1."""

from sqlalchemy import CheckConstraint, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def hex32(col: str) -> CheckConstraint:
    """HEX32: 32 lowercase hex chars, not all zero (OTel trace ID)."""
    return CheckConstraint(
        f"{col} ~ '^[0-9a-f]{{32}}$' AND {col} <> repeat('0', 32)", name=f"{col}_hex32"
    )


def hex16(col: str) -> CheckConstraint:
    """HEX16: 16 lowercase hex chars, not all zero (OTel span ID)."""
    return CheckConstraint(
        f"{col} ~ '^[0-9a-f]{{16}}$' AND {col} <> repeat('0', 16)", name=f"{col}_hex16"
    )


def length(col: str, n: int) -> CheckConstraint:
    """LEN(n): 1..n characters."""
    return CheckConstraint(f"char_length({col}) BETWEEN 1 AND {n}", name=f"{col}_len")


def max_length(col: str, n: int) -> CheckConstraint:
    """`<= n` characters (nullable columns; NULL passes)."""
    return CheckConstraint(f"char_length({col}) <= {n}", name=f"{col}_maxlen")


def non_negative(col: str) -> CheckConstraint:
    return CheckConstraint(f"{col} >= 0", name=f"{col}_nonneg")


def one_of(col: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{col} IN ({allowed})", name=f"{col}_enum")


def json_object(col: str) -> CheckConstraint:
    return CheckConstraint(f"jsonb_typeof({col}) = 'object'", name=f"{col}_object")
