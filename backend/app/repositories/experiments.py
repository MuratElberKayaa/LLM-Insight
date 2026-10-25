"""Experiment queries (ARCHITECTURE.md §4.11–4.12, Phase 4C).

`experiments` has no `project_id`: every read joins its dataset, whose `project_id` scopes the
request (one indistinguishable `404` otherwise). Case content (`expected_output`, `rule`) is
read as `jsonb::text` so numbers stay exact. Bound parameters only (SECURITY.md §3).
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Text, cast, func, insert, select, update
from sqlalchemy.orm import Session

from app.models import Experiment, ExperimentResult, TestCase, TestDataset

EXPERIMENT_COLUMNS = (
    Experiment.id,
    Experiment.dataset_id,
    Experiment.prompt_version_a,
    Experiment.prompt_version_b,
    Experiment.model,
    Experiment.mode,
    Experiment.budget_usd,
    Experiment.started_at,
    Experiment.finished_at,
)
RESULT_COLUMNS = (
    ExperimentResult.test_case_id,
    ExperimentResult.prompt_version,
    ExperimentResult.passed,
    ExperimentResult.evaluation_detail,
    ExperimentResult.duration_ms,
    ExperimentResult.input_tokens,
    ExperimentResult.output_tokens,
    ExperimentResult.cost_usd,
    ExperimentResult.cost_status,
    ExperimentResult.cost_unknown_reason,
    ExperimentResult.provenance,
)


def cases_for_run(session: Session, dataset_id: int, limit: int) -> Sequence[Row[Any]]:
    """At most `limit + 1` cases of the dataset in id order (the extra row signals too many)."""
    stmt = (
        select(
            TestCase.id,
            TestCase.sanitized_input,
            cast(TestCase.expected_output, Text).label("expected_output_json"),
            cast(TestCase.rule, Text).label("rule_json"),
        )
        .where(TestCase.dataset_id == dataset_id)
        .order_by(TestCase.id)
        .limit(limit + 1)
    )
    return session.execute(stmt).all()


def insert_experiment(session: Session, values: dict[str, Any]) -> Row[Any]:
    stmt = insert(Experiment).values(**values).returning(*EXPERIMENT_COLUMNS)
    return session.execute(stmt).one()


def insert_results(session: Session, rows: Sequence[dict[str, Any]]) -> None:
    if rows:
        session.execute(insert(ExperimentResult), list(rows))


def finish_experiment(session: Session, experiment_id: int) -> None:
    """`clock_timestamp()`, not `now()`: the transaction's start time would equal
    `started_at`, hiding how long the run took."""
    session.execute(
        update(Experiment)
        .where(Experiment.id == experiment_id)
        .values(finished_at=func.clock_timestamp())
    )


def get_experiment(session: Session, project_id: int, experiment_id: int) -> Row[Any] | None:
    stmt = (
        select(*EXPERIMENT_COLUMNS, TestDataset.name.label("dataset_name"))
        .join(TestDataset, TestDataset.id == Experiment.dataset_id)
        .where(Experiment.id == experiment_id, TestDataset.project_id == project_id)
    )
    return session.execute(stmt).one_or_none()


def results_for(session: Session, experiment_id: int) -> Sequence[Row[Any]]:
    stmt = (
        select(*RESULT_COLUMNS)
        .where(ExperimentResult.experiment_id == experiment_id)
        .order_by(ExperimentResult.test_case_id, ExperimentResult.prompt_version)
    )
    return session.execute(stmt).all()
