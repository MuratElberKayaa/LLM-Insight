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
"""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, APIError, invalid, not_found, require_admin
from app.api.human_input import conflict, write_transaction
from app.api.json_body import ExactJSONResponse, parse_model, read_json
from app.api.traces import BIGINT_MAX, ProjectParam, iso_from_datetime, read_snapshot
from app.core import exact_json
from app.models.experiment import MODEL_MAX, PROMPT_VERSION_MAX
from app.repositories import datasets as dataset_repo
from app.repositories import experiments as repo
from app.repositories import pricing as pricing_repo
from app.services import pricing, redaction
from app.services.experiments import report, runner
from app.services.experiments.mock_provider import GOLDEN_PROVIDER, SIMULATED_PROVIDER
from app.services.ingestion import attrmap

router = APIRouter(prefix="/api/v1", tags=["experiments"], dependencies=[Depends(require_admin)])

MAX_EXPERIMENT_CASES = 200  # at most 400 results per experiment
_PROMPT_VERSION = attrmap.PROMPT_VERSION.pattern
_MODEL = attrmap.MODEL.pattern
_BUDGET_QUANTUM = Decimal("1E-10")  # NUMERIC(20,10)

ExperimentIdPath = Annotated[int, Path(ge=1, le=BIGINT_MAX)]


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
        try:
            planned = runner.run(
                cases,
                (values["prompt_version_a"], values["prompt_version_b"]),
                values["model"],
                GOLDEN_PROVIDER,
                prices,
                experiment.started_at,
                values["budget_usd"],
            )
        except runner.BudgetRefused as exc:
            raise conflict(exc.code, exc.message) from None  # rolls back the experiment row
        repo.insert_results(session, [p.as_row(experiment.id) for p in planned])
        repo.finish_experiment(session, experiment.id)
        return experiment.id

    experiment_id = await run_in_threadpool(write_transaction, request, write)
    document = await run_in_threadpool(_report, request, project, experiment_id)
    return ExactJSONResponse(document, status_code=201, headers=NO_STORE)


@router.get("/experiments/{experiment_id}")
async def get_experiment(
    request: Request, experiment_id: ExperimentIdPath, project: ProjectParam
) -> ExactJSONResponse:
    document = await run_in_threadpool(_report, request, project, experiment_id)
    return ExactJSONResponse(document, headers=NO_STORE)
