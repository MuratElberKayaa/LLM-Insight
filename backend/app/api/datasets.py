"""Datasets and human-approved test cases (ARCHITECTURE.md §4.8, §4.10, §5; Phase 4B).

- `POST /api/v1/datasets/{dataset_id}/cases` is the contract endpoint (§5): a case is stored
  only with `approved: true` (the JSON boolean), `approved_by` and a non-empty input; anything
  else is `422` and nothing is written (T34). The input is masked by the server before insert
  and `redaction_applied` is set (T35).
- `GET /api/v1/datasets`, `POST /api/v1/datasets` and `GET /api/v1/datasets/{id}/cases` are
  additive (decision D2): the contract has no way to create or list datasets or to read cases.

Trace-to-testset (decisions D4, D5 as revised by the architectural review, ADR-24): a case
that cites a trace must also cite a review of that trace in the same project whose decision
is `hypothesis_supported`, or `hypothesis_rejected` together with an independently supplied
`expected_output`; `pending` and `inconclusive` reviews are not eligible. At most one case per
(dataset, review) exists (advisory lock here, partial unique index in the database). Once a
case cites a review, the review's decision is frozen (`api/reviews.py`, database trigger). The
content is typed by the human: telemetry holds no prompt or completion to copy (ADR-19), so
only the references come from the trace. No case is ever created implicitly; cases are
evaluated only by experiments (`api/experiments.py`).

`rule` follows contract `llminsight.rule.v1` and an `expected_output` must satisfy its own rule
(`services/evaluation/rules.py`). Numbers are exact: bodies are parsed with `Decimal`, stored
as exact JSON text and returned with their digits (`ExactJSONResponse`).
"""

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, APIError, invalid, not_found, require_admin
from app.api.human_input import conflict, free_text, label, write_transaction
from app.api.json_body import ExactJSONResponse, parse_model, read_json
from app.api.traces import BIGINT_MAX, ProjectParam, iso_from_datetime, read_snapshot
from app.core import exact_json
from app.models.dataset import CASE_REVIEW_INDEX, DATASET_NAME_MAX, SANITIZED_INPUT_MAX
from app.models.review import ACTOR_MAX
from app.repositories import datasets as repo
from app.repositories import reviews as review_repo
from app.repositories.traces import project_exists
from app.services import case_rules, redaction

router = APIRouter(prefix="/api/v1", tags=["datasets"], dependencies=[Depends(require_admin)])

MAX_LIMIT = 200
DEFAULT_LIMIT = 100
_HEX32 = frozenset("0123456789abcdef")
_MIN_SENSITIVE_INT_DIGITS = 10

DatasetIdPath = Annotated[int, Path(ge=1, le=BIGINT_MAX)]
LimitParam = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool | None = None
    approved_by: str
    sanitized_input: str
    expected_output: Any = None
    rule: Any
    origin_trace_id: str | None = None
    origin_review_id: int | None = None


CONVERTIBLE_DECISIONS = ("hypothesis_supported", "hypothesis_rejected")


def _duplicate_dataset() -> APIError:
    return conflict("duplicate_dataset", "this project already has a dataset with this name")


def _duplicate_test_case() -> APIError:
    return conflict("duplicate_test_case", "this review already has a test case in this dataset")


def _review_not_eligible() -> APIError:
    return conflict(
        "review_not_eligible",
        "only a review whose hypothesis was supported, or rejected with an independently"
        " supplied expected output, can become a test case",
    )


# Database-side guards (migration 0004) mapped to the same answers as the API checks, for the
# case where a concurrent change slips between the check and the insert.
CASE_CONFLICTS = {
    CASE_REVIEW_INDEX: _duplicate_test_case,
    "test_cases_review_eligibility": _review_not_eligible,
}


def dataset_json(r: Any, case_count: int) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "created_at": iso_from_datetime(r.created_at),
        "case_count": case_count,
    }


def _stored_json(text: str | None) -> Any:
    return None if text is None else exact_json.loads(text)


def case_json(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "dataset_id": r.dataset_id,
        "origin_trace_id": r.origin_trace_id,
        "origin_review_id": r.origin_review_id,
        "sanitized_input": r.sanitized_input,
        "expected_output": _stored_json(r.expected_output_json),
        "rule": _stored_json(r.rule_json),
        "redaction_applied": r.redaction_applied,
        "approved_by": r.approved_by,
        "approved_at": iso_from_datetime(r.approved_at),
        "created_at": iso_from_datetime(r.created_at),
    }


def _assertion_value(field: str, value: Any) -> None:
    """`expected_output` / `rule`: bounded JSON whose strings carry no credential, personal
    data or unsafe characters. Refused instead of masked (decision D9)."""
    try:
        case_rules.check_json_size(field, value)
    except case_rules.RuleError as exc:
        raise invalid(exc.field, exc.message) from None
    for s in redaction.iter_json_strings(value):
        if redaction.assertion_string_is_sensitive(s):
            raise invalid(
                field,
                f"{field} contains a value that looks like a credential, personal data or"
                " hidden characters; use synthetic content",
            )
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
        elif (type(v) is int or isinstance(v, Decimal)) and len(
            str(int(abs(v)))
        ) >= _MIN_SENSITIVE_INT_DIGITS:
            raise invalid(field, f"{field} contains a number that looks like an ID or phone")


def _origin(body: CaseCreate) -> tuple[str | None, int | None]:
    trace_id, review_id = body.origin_trace_id, body.origin_review_id
    if (trace_id is None) != (review_id is None):
        raise APIError(
            422,
            "invalid_request",
            "a case from a trace needs both origin_trace_id and origin_review_id",
            fields=["origin_review_id", "origin_trace_id"],
        )
    if trace_id is not None and (
        len(trace_id) != 32 or not set(trace_id) <= _HEX32 or set(trace_id) == {"0"}
    ):
        raise invalid("origin_trace_id", "origin_trace_id must be 32 lowercase hex characters")
    if review_id is not None and not 1 <= review_id <= BIGINT_MAX:
        raise invalid("origin_review_id", "origin_review_id must be a positive id")
    return trace_id, review_id


# ---------------------------------------------------------------- datasets
@router.get("/datasets")
async def list_datasets(
    request: Request, project: ProjectParam, limit: LimitParam = DEFAULT_LIMIT
) -> JSONResponse:
    def read(session: Session) -> list[Any]:
        if not project_exists(session, project):
            raise not_found()
        return list(repo.list_datasets(session, project, limit))

    rows = await run_in_threadpool(read_snapshot, request, read)
    return JSONResponse(
        {
            "items": [dataset_json(r, int(r.case_count)) for r in rows[:limit]],
            "truncated": len(rows) > limit,
            "limit": limit,
        },
        headers=NO_STORE,
    )


@router.post("/datasets")
async def create_dataset(request: Request, project: ProjectParam) -> JSONResponse:
    body = parse_model(DatasetCreate, await read_json(request))
    name = label("name", body.name, DATASET_NAME_MAX)

    def write(session: Session) -> Any:
        if not project_exists(session, project):
            raise not_found()
        return repo.insert_dataset(session, project, name)

    row = await run_in_threadpool(
        write_transaction,
        request,
        write,
        {"uq_test_datasets_project_id_name": _duplicate_dataset},
    )
    return JSONResponse(dataset_json(row, 0), status_code=201, headers=NO_STORE)


# ---------------------------------------------------------------- test cases
@router.get("/datasets/{dataset_id}/cases")
async def list_cases(
    request: Request,
    dataset_id: DatasetIdPath,
    project: ProjectParam,
    limit: LimitParam = DEFAULT_LIMIT,
    after: Annotated[int | None, Query(ge=0, le=BIGINT_MAX)] = None,
) -> JSONResponse:
    """Cases of one dataset by id (stable order, keyset `after`), exactly as stored."""

    def read(session: Session) -> tuple[Any, list[Any]]:
        dataset = repo.get_dataset(session, project, dataset_id)
        if dataset is None:
            raise not_found()
        return dataset, list(repo.list_cases(session, dataset_id, after, limit))

    dataset, rows = await run_in_threadpool(read_snapshot, request, read)
    page = rows[:limit]
    return ExactJSONResponse(
        {
            "dataset": dataset_json(dataset, int(dataset.case_count)),
            "items": [case_json(r) for r in page],
            "next_after": page[-1].id if len(rows) > limit else None,
            "limit": limit,
        },
        headers=NO_STORE,
    )


@router.post("/datasets/{dataset_id}/cases")
async def create_case(
    request: Request, dataset_id: DatasetIdPath, project: ProjectParam
) -> JSONResponse:
    """Store one human-approved, redacted test case. Order: approval, then every field, then
    the references in the database; the first failure stops the request with nothing stored."""
    body = parse_model(CaseCreate, await read_json(request))
    if body.approved is not True:
        raise invalid("approved", "a test case is stored only with approved: true")
    approved_by = label("approved_by", body.approved_by, ACTOR_MAX)
    text = free_text("sanitized_input", body.sanitized_input, SANITIZED_INPUT_MAX)
    expected = body.expected_output
    if expected is not None:
        _assertion_value("expected_output", expected)
    _assertion_value("rule", body.rule)
    try:
        rule = case_rules.validate_rule(body.rule, expected)
    except case_rules.RuleError as exc:
        raise invalid(exc.field, exc.message) from None
    trace_id, review_id = _origin(body)

    def write(session: Session) -> Any:
        if not repo.dataset_exists(session, project, dataset_id):
            raise not_found()
        if trace_id is not None and review_id is not None:
            if not review_repo.trace_exists(session, project, trace_id):
                raise not_found()
            review = review_repo.get_review(session, project, review_id, lock="share")
            if review is None:
                raise not_found()
            if review.trace_id != trace_id:
                raise invalid("origin_review_id", "the review belongs to a different trace")
            if review.reviewer_decision == "pending":
                raise conflict(
                    "review_not_decided",
                    "record a reviewer decision before converting the review to a test case",
                )
            if review.reviewer_decision not in CONVERTIBLE_DECISIONS:
                raise _review_not_eligible()  # inconclusive
            if review.reviewer_decision == "hypothesis_rejected" and expected is None:
                raise invalid(
                    "expected_output",
                    "a case from a rejected hypothesis needs an independently supplied"
                    " expected_output",
                )
            repo.lock_review_conversion(session, dataset_id, review_id)
            if repo.case_exists_for_review(session, dataset_id, review_id) is not None:
                raise _duplicate_test_case()
        return repo.insert_case(
            session,
            {
                "dataset_id": dataset_id,
                "origin_trace_id": trace_id,
                "origin_review_id": review_id,
                "sanitized_input": text.text,
                "redaction_applied": True,
                "approved_by": approved_by,
            },
            None if expected is None else exact_json.dumps(expected),
            exact_json.dumps(rule),
        )

    row = await run_in_threadpool(write_transaction, request, write, CASE_CONFLICTS)
    return ExactJSONResponse(
        {
            **case_json(row),
            "redaction": {"applied": True, "masked": {"sanitized_input": text.masked}},
        },
        status_code=201,
        headers=NO_STORE,
    )
