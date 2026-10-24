"""`POST /v1/traces`: OTLP/HTTP binary-protobuf trace receiver (TELEMETRY_CONTRACT.md §1).

Steps 1–6 of the processing order read headers only, so no unauthenticated body is ever
buffered. The 200 `ExportTraceServiceResponse` (with `partial_success` unset) is returned
only after the ingest transaction has committed.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from starlette.concurrency import run_in_threadpool

from app.services.ingestion import body, errors
from app.services.ingestion.errors import PROTOBUF, IngestError
from app.services.ingestion.service import Ingestor

logger = logging.getLogger("app.ingest")

router = APIRouter(tags=["otlp"])

PATH = "/v1/traces"
# Empty message: partial_success unset (spec MUST on full success).
_OK_BODY = ExportTraceServiceResponse().SerializeToString()


def _error_response(err: IngestError, content_type: str | None) -> Response:
    media = body.response_media_type(content_type)
    headers = dict(err.headers)
    if err.retry_after is not None:
        headers["Retry-After"] = str(err.retry_after)
    return Response(
        content=errors.status_body(err, media),
        status_code=err.status_code,
        media_type=media,
        headers=headers,
    )


@router.post(PATH, response_class=Response, include_in_schema=True)
async def export_traces(request: Request) -> Response:
    ingestor: Ingestor = request.app.state.ingestor
    settings = ingestor.settings
    content_type = request.headers.get("content-type")
    client = request.client.host if request.client else "unknown"
    try:
        body.check_media_type(content_type)  # step 2
        gzipped = body.is_gzip(request.headers.get("content-encoding"))  # step 3
        ingestor.check_authfail_limit(client)  # step 4
        project = await run_in_threadpool(
            ingestor.authenticate, request.headers.getlist("x-project-key"), client
        )  # step 5
        ingestor.check_ingest_limit(project)  # step 6
        try:
            wire = await body.read_bounded(  # step 7: size limit and overall read deadline
                request.headers.get("content-length"),
                request.stream(),
                settings.otlp_max_body_bytes,
                settings.otlp_body_read_timeout_s,
            )
        except IngestError as err:
            logger.warning(
                "ingest body rejected status=%d key_id=%s client=%s",
                err.status_code,
                project.key_id,
                client,
            )
            raise
        await run_in_threadpool(ingestor.process, project, wire, gzipped)  # steps 8–13
    except IngestError as err:
        return _error_response(err, content_type)
    except Exception as exc:  # never leak details; the transaction is already rolled back
        logger.error("ingest failed unexpectedly: %s", type(exc).__name__)
        return _error_response(errors.internal_error(), content_type)
    return Response(content=_OK_BODY, status_code=200, media_type=PROTOBUF)


@router.api_route(
    PATH,
    methods=["GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def method_not_allowed(request: Request) -> Response:  # step 1
    return _error_response(errors.method_not_allowed(), request.headers.get("content-type"))
