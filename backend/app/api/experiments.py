"""`POST /api/v1/experiments` and `GET /api/v1/experiments/{id}` (ARCHITECTURE.md §5, §4.11–4.12,
§5.9; Phase 4C).

An experiment runs every case of one dataset under prompt versions A and B with one model and
records one result per (case, version). **Only `mode: "mock"` runs in this build**: the
scripted, deterministic mock provider (`services/experiments/mock_provider.py`), never a real
or paid service. `mode: "real"` is validated as the contract requires (`budget_usd` and
`LLMI_ALLOW_REAL_LLM=true`) and then refused with `409`, because no real executor exists; no
row is written.

Each result is evaluated by the deterministic engine (`services/evaluation`, contract
`llminsight.rule.v1`); no language model judges anything. A run is one transaction: the
experiment, all its results and `finished_at` are stored together, or nothing is. The response
is the report of the stored rows (`services/experiments/report.py`), the same document as the
GET. Repeating a POST starts a new, independent experiment with identical results.

Access is that of the other `/api/v1` endpoints (§5.1): `require_admin` first, the mandatory
`project`, one indistinguishable `404` for an unknown project, dataset or experiment or one of
another project, strict JSON bodies (§5.7, D7).

Phase 4D (the dashboard's experiment pages) adds, without changing the contract above:
- `GET /api/v1/experiments`: the project's experiments, newest first (read-only, additive);
- `GET /api/v1/experiment-options`: what a run can use: mock mode only, the prompt versions
  the scripted mock answers, the models with a stored price, the limits (read-only, additive);
- bounded execution: at most `MAX_CONCURRENT_EXPERIMENTS` runs at once in this process (more
  get `429 experiments_busy`, never queued), a wall-clock deadline per run
  (`409 experiment_time_limit`), and an unexpected evaluation error answered with the fixed
  `500 internal_error` (its type is logged, never its message). Each refusal stores nothing.
"""

import logging
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, APIError, internal_error, invalid, not_found, require_admin
from app.api.human_input import conflict, write_transaction
from app.api.json_body import ExactJSONResponse, parse_model, read_json
from app.api.traces import BIGINT_MAX, ProjectParam, iso_from_datetime, read_snapshot
from app.core import exact_json
from app.models.experiment import MODEL_MAX, PROMPT_VERSION_MAX
from app.repositories import datasets as dataset_repo
from app.repositories import experiments as repo
from app.repositories import pricing as pricing_repo
from app.repositories.traces import project_exists
from app.services import pricing, redaction
from app.services.experiments import report, runner
from app.services.experiments.mock_provider import GOLDEN_PROVIDER, SIMULATED_PROVIDER
from app.services.ingestion import attrmap

logger = logging.getLogger("app.api")

router = APIRouter(prefix="/api/v1", tags=["experiments"], dependencies=[Depends(require_admin)])

MAX_EXPERIMENT_CASES = 200  # at most 400 results per experiment
MAX_CONCURRENT_EXPERIMENTS = 4  # each run holds one database connection (pool: 5 + 5)
RUN_TIME_LIMIT_S = 15  # wall clock per run; one evaluation is bounded separately (R60)
LIST_MAX_LIMIT = 200
LIST_DEFAULT_LIMIT = 50
MAX_PRICE_ROWS = 1000
OPTIONS_CONTRACT = "llminsight.experiment_options.v1"
_PROMPT_VERSION = attrmap.PROMPT_VERSION.pattern
_MODEL = attrmap.MODEL.pattern
_BUDGET_QUANTUM = Decimal("1E-10")  # NUMERIC(20,10)

ExperimentIdPath = Annotated[int, Path(ge=1, le=BIGINT_MAX)]
ListLimitParam = Annotated[int, Query(ge=1, le=LIST_MAX_LIMIT)]


class RunSlots:
    """Running experiments in this process (single worker, like CR-08). A run beyond the
    bound is refused at once, never queued: a queued run would hold the dashboard's request
    open with no visible progress."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._semaphore = threading.BoundedSemaphore(size)

    def try_acquire(self) -> bool:
        return self._semaphore.acquire(blocking=False)

    def release(self) -> None:
        self._semaphore.release()


class _RunTimeLimit(Exception):
    pass


def _deadline(seconds: float) -> Callable[[], None]:
    """The runner's checkpoint: raises once `seconds` of wall clock have passed."""
    end = time.monotonic() + seconds

    def check() -> None:
        if time.monotonic() > end:
            raise _RunTimeLimit

    return check


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: int
    prompt_version_a: str
    prompt_version_b: str
    model: str
    mode: str
    budget_usd: Any = None


def _identifier(field: str, value: str, pattern: Any, max_len: int) -> str:
    if not 1 <= len(value) <= max_len or pattern is None or not pattern.fullmatch(value):
        raise invalid(field, f"{field} must be 1-{max_len} characters of an identifier")
    if redaction.label_is_sensitive(value):
        raise invalid(field, f"{field} looks like contact data or a secret")
    return value


def _budget(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not (type(value) is int or isinstance(value, Decimal)):
        raise invalid("budget_usd", "budget_usd must be a JSON number")
    amount = Decimal(value)
    if (
        not amount.is_finite()
        or amount <= 0
        or amount > pricing.MAX_COST
        or amount != amount.quantize(_BUDGET_QUANTUM)
    ):
        raise invalid(
            "budget_usd", "budget_usd must be a positive amount with at most 10 decimal places"
        )
    return amount


def _validated(body: ExperimentCreate) -> dict[str, Any]:
    if not 1 <= body.dataset_id <= BIGINT_MAX:
        raise invalid("dataset_id", "dataset_id must be a positive id")
    a = _identifier("prompt_version_a", body.prompt_version_a, _PROMPT_VERSION, PROMPT_VERSION_MAX)
    b = _identifier("prompt_version_b", body.prompt_version_b, _PROMPT_VERSION, PROMPT_VERSION_MAX)
    if a == b:
        raise APIError(
            422,
            "invalid_request",
            "prompt_version_a and prompt_version_b must differ",
            fields=["prompt_version_a", "prompt_version_b"],
        )
    model = _identifier("model", body.model, _MODEL, MODEL_MAX)
    if body.mode not in ("mock", "real"):
        raise invalid("mode", "mode must be mock or real")
    budget = _budget(body.budget_usd)
    if body.mode == "real" and budget is None:
        raise invalid("budget_usd", "a real experiment needs budget_usd")
    return {
        "dataset_id": body.dataset_id,
        "prompt_version_a": a,
        "prompt_version_b": b,
        "model": model,
        "mode": body.mode,
        "budget_usd": budget,
    }


def _real_refused(request: Request) -> APIError:
    if not request.app.state.settings.allow_real_llm:
        return conflict(
            "real_llm_not_allowed",
            "real experiments need LLMI_ALLOW_REAL_LLM=true on the backend; nothing was run",
        )
    return conflict(
        "real_mode_unavailable",
        "this build has no real provider executor (Phase 4C runs mock experiments only);"
        " nothing was run",
    )


def _case_record(row: Any) -> runner.CaseRecord:
    expected = (
        None if row.expected_output_json is None else exact_json.loads(row.expected_output_json)
    )
    return runner.CaseRecord(row.id, row.sanitized_input, expected, exact_json.loads(row.rule_json))


def _report(request: Request, project: int, experiment_id: int) -> dict[str, Any]:
    def read(session: Session) -> tuple[Any, list[Any]]:
        experiment = repo.get_experiment(session, project, experiment_id)
        if experiment is None:
            raise not_found()
        return experiment, list(repo.results_for(session, experiment_id))

    experiment, rows = read_snapshot(request, read)
    return report.build(experiment, experiment.dataset_name, rows, iso_from_datetime)


@router.post("/experiments")
async def create_experiment(request: Request, project: ProjectParam) -> ExactJSONResponse:
    body = parse_model(ExperimentCreate, await read_json(request))
    values = _validated(body)
    if values["mode"] == "real":
        raise _real_refused(request)

    def write(session: Session) -> int:
        if not dataset_repo.dataset_exists(session, project, values["dataset_id"]):
            raise not_found()
        rows = repo.cases_for_run(session, values["dataset_id"], MAX_EXPERIMENT_CASES)
        if not rows:
            raise conflict("dataset_empty", "the dataset has no test case to run")
        if len(rows) > MAX_EXPERIMENT_CASES:
            raise conflict(
                "dataset_too_large",
                f"an experiment runs at most {MAX_EXPERIMENT_CASES} test cases",
            )
        cases = [_case_record(r) for r in rows]
        experiment = repo.insert_experiment(session, values)
        prices = pricing_repo.load_prices(session, [(SIMULATED_PROVIDER, values["model"])])
        # Every refusal below raises inside the transaction, so the experiment row is rolled
        # back with it: a run is stored completely or not at all.
        try:
            planned = runner.run(
                cases,
                (values["prompt_version_a"], values["prompt_version_b"]),
                values["model"],
                GOLDEN_PROVIDER,
                prices,
                experiment.started_at,
                values["budget_usd"],
                checkpoint=_deadline(RUN_TIME_LIMIT_S),
            )
        except runner.BudgetRefused as exc:
            raise conflict(exc.code, exc.message) from None
        except _RunTimeLimit:
            raise conflict(
                "experiment_time_limit",
                f"the run exceeded {RUN_TIME_LIMIT_S} s and was stopped; nothing was stored",
            ) from None
        except Exception as exc:  # noqa: BLE001 - an evaluation bug; its message may quote content
            logger.error("experiment run failed: %s", type(exc).__name__)
            raise internal_error() from None
        repo.insert_results(session, [p.as_row(experiment.id) for p in planned])
        repo.finish_experiment(session, experiment.id)
        return experiment.id

    slots: RunSlots = request.app.state.experiment_slots
    if not slots.try_acquire():
        raise APIError(
            429,
            "experiments_busy",
            f"at most {slots.size} experiments run at once; nothing was run, retry shortly",
            headers={"Retry-After": "5"},
        )
    try:
        experiment_id = await run_in_threadpool(write_transaction, request, write)
    finally:
        slots.release()
    document = await run_in_threadpool(_report, request, project, experiment_id)
    return ExactJSONResponse(document, status_code=201, headers=NO_STORE)


@router.get("/experiments/{experiment_id}")
async def get_experiment(
    request: Request, experiment_id: ExperimentIdPath, project: ProjectParam
) -> ExactJSONResponse:
    document = await run_in_threadpool(_report, request, project, experiment_id)
    return ExactJSONResponse(document, headers=NO_STORE)


def _summary(r: Any) -> dict[str, Any]:
    finished = r.finished_at is not None
    return {
        "id": r.id,
        "dataset_id": r.dataset_id,
        "dataset_name": r.dataset_name,
        "prompt_version_a": r.prompt_version_a,
        "prompt_version_b": r.prompt_version_b,
        "model": r.model,
        "mode": r.mode,
        "budget_usd": report.money(r.budget_usd),
        "started_at": iso_from_datetime(r.started_at),
        "finished_at": iso_from_datetime(r.finished_at) if finished else None,
        "state": "finished" if finished else "running",
        "results": int(r.result_count),
    }


@router.get("/experiments")
async def list_experiments(
    request: Request, project: ProjectParam, limit: ListLimitParam = LIST_DEFAULT_LIMIT
) -> JSONResponse:
    """The project's experiments, newest first (Phase 4D, additive, read-only). Summaries
    only; the report of one experiment is `GET /api/v1/experiments/{id}`."""

    def read(session: Session) -> list[Any]:
        if not project_exists(session, project):
            raise not_found()
        return list(repo.list_experiments(session, project, limit))

    rows = await run_in_threadpool(read_snapshot, request, read)
    return JSONResponse(
        {
            "items": [_summary(r) for r in rows[:limit]],
            "truncated": len(rows) > limit,
            "limit": limit,
        },
        headers=NO_STORE,
    )


@router.get("/experiment-options")
async def experiment_options(request: Request, project: ProjectParam) -> JSONResponse:
    """What a run can use (Phase 4D, additive, read-only): mock mode only; the prompt
    versions the scripted mock answers (any other version would get
    `mock_no_scripted_output` for every case); the models with a stored price of the simulated
    provider in force now (any other model would leave every cost `price_missing`); the
    limits. Nothing here is invented: versions come from the script, models from
    `model_prices`."""

    def read(session: Session) -> tuple[Any, list[pricing.Price]]:
        if not project_exists(session, project):
            raise not_found()
        now = pricing_repo.database_now(session)
        return now, pricing_repo.prices_of_provider(session, SIMULATED_PROVIDER, MAX_PRICE_ROWS)

    now, prices = await run_in_threadpool(read_snapshot, request, read)
    truncated = len(prices) > MAX_PRICE_ROWS
    prices = prices[:MAX_PRICE_ROWS]
    models = []
    for model in sorted({p.model for p in prices}):
        price = pricing.select_price(prices, SIMULATED_PROVIDER, model, now)
        if price is None or len(model) > MODEL_MAX or not _MODEL.fullmatch(model):
            continue
        models.append({"model": model, "price_version": price.version})
    real_code = (
        "real_mode_unavailable"
        if request.app.state.settings.allow_real_llm
        else "real_llm_not_allowed"
    )
    return JSONResponse(
        {
            "contract_version": OPTIONS_CONTRACT,
            "modes": ["mock"],
            "real_mode": {"available": False, "code": real_code},
            "provider": {
                "kind": "scripted_mock",
                "simulates": SIMULATED_PROVIDER,
                "provenance": "synthetic",
            },
            "prompt_versions": [
                {"prompt_version": v, "scripted_inputs": n}
                for v, n in GOLDEN_PROVIDER.prompt_versions().items()
            ],
            "models": models,
            "models_truncated": truncated,
            "limits": {
                "max_cases": MAX_EXPERIMENT_CASES,
                "max_concurrent_runs": request.app.state.experiment_slots.size,
                "run_time_limit_seconds": RUN_TIME_LIMIT_S,
                "budget_usd_max": report.money(pricing.MAX_COST),
                "budget_usd_decimal_places": 10,
            },
        },
        headers=NO_STORE,
    )
