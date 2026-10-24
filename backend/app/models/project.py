"""ARCHITECTURE.md §4.2 `projects` and §4.3 `project_api_keys`."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, length


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        length("name", 100),
        UniqueConstraint("name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectApiKey(Base):
    __tablename__ = "project_api_keys"
    __table_args__ = (
        CheckConstraint("prefix ~ '^[A-Za-z0-9]{8}$'", name="prefix_format"),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_hash_hmac_sha256_hex"),
        CheckConstraint("revoked_at >= created_at", name="revoked_after_created"),
        UniqueConstraint("prefix"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
