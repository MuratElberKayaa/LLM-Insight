"""`POST /api/v1/failure-reviews` and `PATCH /api/v1/failure-reviews/{id}` (ARCHITECTURE.md §5,
§4.9, CR-03; Phase 4B).

A review is a human record: these two handlers are the only code that writes
`failure_reviews`, and each runs only on an explicit request. Nothing derives a review from
telemetry, from an inference error or from a diagnostic finding (Phase 4A categories are a
separate vocabulary and are never mapped to `issue_type`). The `hypothesis` is the reviewer's
own text; `hypothesis_supported` means the evidence supports it, not that a root cause is
proven (ADR-10).

Access is that of the read API (§5.1): `require_admin` first, then the mandatory `project`;
an unknown project, an unknown trace or review and another project's record are one
indistinguishable `404`. Creation needs the trace to exist in the project at insert time
(soft reference, §4.1) and, when a span is named, that span in that trace.
"""

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, APIError, invalid, not_found, require_admin
from app.api.human_input import conflict, free_text, label, write_transaction
from app.api.json_body import parse_model, read_json
from app.api.traces import BIGINT_MAX, ProjectParam, review_json
from app.models.review import (
    ACTOR_MAX,
    EVIDENCE_MAX,
    HYPOTHESIS_MAX,
    ISSUE_TYPES,
    OPEN_REVIEW_INDEX,
    REVIEW_STATUSES,
    REVIEWER_DECISIONS,
)
from app.repositories import reviews as repo

router = APIRouter(prefix="/api/v1", tags=["reviews"], dependencies=[Depends(require_admin)])

_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")


class ReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str | None = None
    issue_type: str
    evidence: str
    hypothesis: str
    created_by: str


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: str | None = None
    reviewer_decision: str | None = None


def _duplicate_open_review() -> APIError:
    return conflict(
        "duplicate_open_review",
        "this trace already has a review of this issue type that is not closed",
    )


CONFLICTS = {OPEN_REVIEW_INDEX: _duplicate_open_review}


def _hex(field: str, value: str, pattern: re.Pattern[str]) -> str:
    """OTel ids as stored (§4.1): lowercase hex of the exact length, not all zero."""
    if not pattern.fullmatch(value) or set(value) == {"0"}:
        raise invalid(field, f"{field} must be lowercase hexadecimal of the OTel length")
    return value


def _validated_create(body: ReviewCreate) -> tuple[dict[str, Any], dict[str, Any]]:
    trace_id = _hex("trace_id", body.trace_id, _TRACE_ID)
    span_id = None if body.span_id is None else _hex("span_id", body.span_id, _SPAN_ID)
    if body.issue_type not in ISSUE_TYPES:
        raise invalid("issue_type", f"issue_type must be one of {', '.join(ISSUE_TYPES)}")
    evidence = free_text("evidence", body.evidence, EVIDENCE_MAX)
    hypothesis = free_text("hypothesis", body.hypothesis, HYPOTHESIS_MAX)
    values = {
        "trace_id": trace_id,
        "span_id": span_id,
        "issue_type": body.issue_type,
        "evidence": evidence.text,
        "hypothesis": hypothesis.text,
        "created_by": label("created_by", body.created_by, ACTOR_MAX),
    }
    report = {"evidence": evidence.masked, "hypothesis": hypothesis.masked}
    return values, report


@router.post("/failure-reviews")
async def create_review(request: Request, project: ProjectParam) -> JSONResponse:
    """Record one human failure review (`open` / `pending`). Status and decision change only
    through `PATCH`, which is a separate explicit action."""
    body = parse_model(ReviewCreate, await read_json(request))
    values, masked = _validated_create(body)

    def write(session: Session) -> Any:
        if not repo.trace_exists(session, project, values["trace_id"]):
            raise not_found()  # also an unknown project or another project's trace
        if values["span_id"] is not None and not repo.span_exists(
            session, project, values["trace_id"], values["span_id"]
        ):
            raise invalid("span_id", "span_id is not a span of this trace")
        return repo.insert_review(session, project, values)

    row = await run_in_threadpool(write_transaction, request, write, CONFLICTS)
    return JSONResponse(
        {**review_json(row, []), "redaction": {"applied": True, "masked": masked}},
        status_code=201,
        headers=NO_STORE,
    )


@router.patch("/failure-reviews/{review_id}")
async def update_review(
    request: Request,
    review_id: Annotated[int, Path(ge=1, le=BIGINT_MAX)],
    project: ProjectParam,
) -> JSONResponse:
    """Change `review_status` and/or `reviewer_decision` (decision D3). The result must
    satisfy §4.9: a closed review has a decision. Text fields are immutable."""
    body = parse_model(ReviewUpdate, await read_json(request))
    if body.review_status is None and body.reviewer_decision is None:
        raise APIError(
            422,
            "invalid_request",
            "give review_status and/or reviewer_decision",
            fields=["review_status", "reviewer_decision"],
        )
    if body.review_status is not None and body.review_status not in REVIEW_STATUSES:
        raise invalid("review_status", f"review_status must be one of {', '.join(REVIEW_STATUSES)}")
    if body.reviewer_decision is not None and body.reviewer_decision not in REVIEWER_DECISIONS:
        raise invalid(
            "reviewer_decision",
            f"reviewer_decision must be one of {', '.join(REVIEWER_DECISIONS)}",
        )

    def write(session: Session) -> Any:
        current = repo.get_review(session, project, review_id, lock="update")
        if current is None:
            raise not_found()
        status = body.review_status or current.review_status
        decision = body.reviewer_decision or current.reviewer_decision
        if status == "closed" and decision == "pending":
            raise APIError(
                422,
                "invalid_request",
                "a closed review needs a reviewer decision other than pending",
                fields=["review_status", "reviewer_decision"],
            )
        if (status, decision) == (current.review_status, current.reviewer_decision):
            return current  # nothing changes: no write, updated_at stays
        return repo.update_review(session, project, review_id, status, decision)

    row = await run_in_threadpool(write_transaction, request, write, CONFLICTS)
    return JSONResponse(review_json(row), headers=NO_STORE)
