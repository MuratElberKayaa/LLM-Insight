"""Import completed ticket A/B evidence. No executor, provider client or content storage.

Rule booleans are operator-imported evidence; raw responses were not retained, so semantic
scores cannot be independently recomputed here. Trace identity and measured usage ARE checked
against project-scoped PostgreSQL telemetry, and all displayed aggregates are derived locally.
"""

import hashlib
import json
import statistics
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.admin import NO_STORE, not_found, require_admin
from app.api.human_input import conflict, write_transaction
from app.api.json_body import parse_model, read_json
from app.api.traces import BIGINT_MAX, ProjectParam, iso_from_datetime, read_snapshot
from app.models.imported_experiment import ImportedExperiment
from app.models.telemetry import Span, Trace
from app.models.usage import UsageRecord
from app.repositories.traces import project_exists
from app.services import ab_statistics

router = APIRouter(
    prefix="/api/v1/imported-experiments",
    tags=["experiments"],
    dependencies=[Depends(require_admin)],
)
RULES = (
    "json_object",
    "no_markdown",
    "no_duplicate_keys",
    "exact_keys",
    "category_enum",
    "priority_enum",
    "expected_category",
    "expected_priority",
)
Model = Literal["openai/gpt-oss-20b", "gemini-3.1-flash-lite"]
CaseId = Literal["t01", "t02", "t03", "t04", "t05", "t06"]


class CaseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: CaseId
    variant: Literal["v1", "v2"]
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    rules: dict[str, bool]

    @model_validator(mode="after")
    def fixed_rules(self):
        if set(self.rules) != set(RULES):
            raise ValueError("exactly eight fixed rules required")
        return self


class ImportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    model: Model
    suite: Literal["ticket-ab-v1"]
    rule_version: Literal["ticket-category-priority-v1"]
    cases: list[CaseEvidence] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def complete_pairs(self):
        if self.run_id == "0" * 32:
            raise ValueError("nonzero run id required")
        expected = {(f"t{i:02d}", v) for i in range(1, 7) for v in ("v1", "v2")}
        if {(c.case_id, c.variant) for c in self.cases} != expected:
            raise ValueError("six complete pairs required")
        if len({c.trace_id for c in self.cases}) != 12:
            raise ValueError("trace reuse is not evidence of a second call")
        self.cases.sort(key=lambda c: (c.case_id, c.variant))
        return self


def verified_report(session: Session, project: int, body: ImportEvidence) -> dict:
    rows, starts, ends = [], [], []
    provider = "groq" if body.model == "openai/gpt-oss-20b" else "gcp.gemini"
    for case in body.cases:
        trace = session.scalar(
            select(Trace).where(Trace.project_id == project, Trace.trace_id == case.trace_id)
        )
        if trace is None:
            raise not_found()
        usage = list(
            session.scalars(
                select(UsageRecord).where(
                    UsageRecord.project_id == project, UsageRecord.trace_id == case.trace_id
                )
            )
        )
        spans = list(
            session.scalars(
                select(Span).where(
                    Span.project_id == project,
                    Span.trace_id == case.trace_id,
                    Span.kind == "CLIENT",
                )
            )
        )
        valid = (
            trace.provenance == "real"
            and trace.status == "ok"
            and trace.prompt_version == "live-ticket-" + case.variant
            and len(usage) == len(spans) == 1
        )
        if valid:
            u, span = usage[0], spans[0]
            valid = (
                u.span_id == span.span_id
                and u.requested_model == body.model
                and u.provider == provider
                and u.provenance == span.provenance == "real"
                and u.usage_source == "provider_reported"
                and span.status != "ERROR"
                and u.cost_status == "unknown"
                and u.cost_usd is None
            )
        if not valid:
            raise conflict("evidence_mismatch", "stored telemetry does not match the import")
        rows.append(
            {
                **case.model_dump(),
                "prompt_version": trace.prompt_version,
                "passed": all(case.rules.values()),
                "client_latency_ms": span.duration_ns / 1e6,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
            }
        )
        starts.append(trace.started_at)
        ends.append(trace.ended_at)
    groups = []
    for variant in ("v1", "v2"):
        cases = [r for r in rows if r["variant"] == variant]
        groups.append(
            {
                "variant": variant,
                "prompt_version": "live-ticket-" + variant,
                "passed": sum(r["passed"] for r in cases),
                "evaluated": 6,
                "client_p50_ms": statistics.median(r["client_latency_ms"] for r in cases),
                "input_tokens": sum(r["input_tokens"] or 0 for r in cases),
                "output_tokens": sum(r["output_tokens"] or 0 for r in cases),
                "missing_input_usage": sum(r["input_tokens"] is None for r in cases),
                "missing_output_usage": sum(r["output_tokens"] is None for r in cases),
            }
        )
    return {
        "run_id": body.run_id,
        "model": body.model,
        "provider": provider,
        "suite": body.suite,
        "rule_version": body.rule_version,
        "groups": groups,
        "cases": rows,
        "started_at": iso_from_datetime(min(starts)),
        "finished_at": iso_from_datetime(max(ends)),
        "cost_status": "unknown",
        "evaluation_source": "operator_import",
        "provenance": "real_asserted",
        "inputs": "synthetic",
        "winner": None,
        "statistical_significance": "not_computed",
    }


def derived_statistics(cases: list[dict]) -> dict:
    """Phase 9D (ADR-53): computed at read time from the stored, immutable case evidence; the
    snapshot itself is never changed. Rates are JSON numbers with 6 decimals (never money)."""
    passed = {(c["case_id"], c["variant"]): bool(c["passed"]) for c in cases}
    variants = []
    for variant in ("v1", "v2"):
        k = sum(1 for (_, v), p in passed.items() if v == variant and p)
        n = sum(1 for (_, v) in passed if v == variant)
        bounds = ab_statistics.wilson_interval(k, n)
        variants.append(
            {
                "variant": variant,
                "passed": k,
                "evaluated": n,
                "wilson_95": None
                if bounds is None
                else {"low": float(bounds[0]), "high": float(bounds[1])},
            }
        )
    case_ids = sorted({case for case, _ in passed})
    v1_only = sum(1 for c in case_ids if passed[(c, "v1")] and not passed[(c, "v2")])
    v2_only = sum(1 for c in case_ids if passed[(c, "v2")] and not passed[(c, "v1")])
    test = ab_statistics.exact_mcnemar(v1_only, v2_only)
    return {
        "derived_at_read": True,
        "variants": variants,
        "paired_test": {
            "method": test["method"],
            "paired_cases": len(case_ids),
            "v1_only": v1_only,
            "v2_only": v2_only,
            "p_value": float(test["p_value"]),
            "significant_at_0_05": test["significant_at_0_05"],
            "min_discordant_for_0_05": test["min_discordant_for_0_05"],
        },
    }


def document(row: ImportedExperiment, *, detail: bool = True) -> dict:
    report = dict(row.report)
    # Phase 9D: derived from the stored cases; `winner` stays null (ADR-51).
    report["statistics"] = derived_statistics(report["cases"])
    report["statistical_significance"] = "exact_mcnemar"
    if not detail:
        report.pop("cases")
    return {
        **report,
        "id": row.id,
        "project": row.project_id,
        "imported_at": iso_from_datetime(row.imported_at),
    }


@router.post("")
async def import_completed(request: Request, project: ProjectParam) -> JSONResponse:
    body = parse_model(ImportEvidence, await read_json(request))
    digest = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()

    def write(session: Session):
        if not project_exists(session, project):
            raise not_found()
        # Scope every lookup; ON CONFLICT handles simultaneous identical imports atomically.
        report = verified_report(session, project, body)
        session.execute(
            insert(ImportedExperiment)
            .values(
                project_id=project,
                run_id=body.run_id,
                model=body.model,
                payload_sha256=digest,
                report=report,
            )
            .on_conflict_do_nothing(index_elements=["project_id", "run_id", "model"])
        )
        row = session.scalar(
            select(ImportedExperiment).where(
                ImportedExperiment.project_id == project,
                ImportedExperiment.run_id == body.run_id,
                ImportedExperiment.model == body.model,
            )
        )
        if row.payload_sha256 != digest:
            raise conflict("import_conflict", "this run already has different immutable evidence")
        return document(row)

    return JSONResponse(
        await run_in_threadpool(write_transaction, request, write), headers=NO_STORE
    )


@router.get("")
async def list_completed(request: Request, project: ProjectParam) -> JSONResponse:
    def read(session: Session):
        if not project_exists(session, project):
            raise not_found()
        rows = list(
            session.scalars(
                select(ImportedExperiment)
                .where(ImportedExperiment.project_id == project)
                .order_by(ImportedExperiment.id.desc())
                .limit(201)
            )
        )
        return {
            "items": [document(r, detail=False) for r in rows[:200]],
            "truncated": len(rows) > 200,
        }

    return JSONResponse(await run_in_threadpool(read_snapshot, request, read), headers=NO_STORE)


@router.get("/{experiment_id}")
async def get_completed(
    request: Request,
    project: ProjectParam,
    experiment_id: Annotated[int, Path(ge=1, le=BIGINT_MAX)],
) -> JSONResponse:
    def read(session: Session):
        row = session.scalar(
            select(ImportedExperiment).where(
                ImportedExperiment.project_id == project, ImportedExperiment.id == experiment_id
            )
        )
        if row is None:
            raise not_found()
        return document(row)

    return JSONResponse(await run_in_threadpool(read_snapshot, request, read), headers=NO_STORE)
