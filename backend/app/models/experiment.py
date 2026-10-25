"""ARCHITECTURE.md §4.11 `experiments` and §4.12 `experiment_results`, Phase 4C (migration 0005).

An experiment runs the cases of one dataset under two prompt versions (A and B) with one model
and records one result per (case, prompt version). `experiments` has no `project_id`: its
dataset scopes it (as for `test_cases`). Money is `NUMERIC(20,10)` with the ledger's unknown-
cost rule: `cost_status = 'computed'` exactly when `cost_usd` is set, and an unknown cost
always has a reason (never 0, ADR-11). `passed IS NULL` means "not evaluated".

The trigger functions of migration 0005 (not modelled here; `alembic check` does not compare
triggers) add what a column check cannot express:
- `experiment_results_integrity`: a result's `prompt_version` is one of its experiment's two
  versions, its test case belongs to the experiment's dataset, a `mock` experiment's results
  are `synthetic` (T39), and results are written only while the experiment is unfinished and
  never updated (append-only);
- `experiments_integrity`: only `finished_at` may change, once, from NULL.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, length, max_length, non_negative, one_of

PROMPT_VERSION_MAX = 64
MODEL_MAX = 256
EVALUATION_DETAIL_MAX = 1000
EXPERIMENT_MODES = ("real", "mock")
RESULT_PROVENANCE = ("real", "synthetic")
COST_STATUSES = ("computed", "unknown")
COST_UNKNOWN_REASONS = ("price_missing", "usage_missing", "usage_inconsistent")
RESULT_UNIQUE = "uq_experiment_results_experiment_id_test_case_id_prompt_version"


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        length("prompt_version_a", PROMPT_VERSION_MAX),
        length("prompt_version_b", PROMPT_VERSION_MAX),
        CheckConstraint("prompt_version_b <> prompt_version_a", name="prompt_versions_differ"),
        length("model", MODEL_MAX),
        one_of("mode", EXPERIMENT_MODES),
        CheckConstraint("budget_usd > 0", name="budget_usd_positive"),
        CheckConstraint("mode = 'mock' OR budget_usd IS NOT NULL", name="real_needs_budget"),
        CheckConstraint("finished_at >= started_at", name="finished_after_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("test_datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_version_a: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version_b: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentResult(Base):
    __tablename__ = "experiment_results"
    __table_args__ = (
        length("prompt_version", PROMPT_VERSION_MAX),
        max_length("evaluation_detail", EVALUATION_DETAIL_MAX),
        non_negative("duration_ms"),
        non_negative("input_tokens"),
        non_negative("output_tokens"),
        non_negative("cost_usd"),
        one_of("cost_status", COST_STATUSES),
        one_of("cost_unknown_reason", COST_UNKNOWN_REASONS),
        one_of("provenance", RESULT_PROVENANCE),
        # §4.12: "the same two cost checks as usage_records" (§4.6).
        CheckConstraint(
            "(cost_status = 'computed') = (cost_usd IS NOT NULL)", name="cost_computed_has_value"
        ),
        CheckConstraint(
            "(cost_status = 'unknown') = (cost_unknown_reason IS NOT NULL)",
            name="cost_unknown_has_reason",
        ),
        UniqueConstraint("experiment_id", "test_case_id", "prompt_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    experiment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    test_case_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    cost_status: Mapped[str] = mapped_column(Text, nullable=False)
    cost_unknown_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
