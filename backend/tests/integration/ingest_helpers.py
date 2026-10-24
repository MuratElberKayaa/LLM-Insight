"""Shared helpers for the ingest integration tests (real PostgreSQL)."""

from typing import Any

from google.rpc import error_details_pb2, status_pb2
from sqlalchemy import Engine, text

from tests.otlp import PROTOBUF

TABLES = ("traces", "spans", "usage_records")


def post(client, payload: bytes, key: str | None, **headers: str):
    h = {"Content-Type": PROTOBUF}
    if key is not None:
        h["X-Project-Key"] = key
    h.update({k.replace("_", "-"): v for k, v in headers.items()})
    return client.post("/v1/traces", content=payload, headers=h)


def counts(engine: Engine, project_id: int | None = None) -> tuple[int, int, int]:
    where = "" if project_id is None else " WHERE project_id = :p"
    with engine.connect() as c:
        return tuple(
            c.execute(text(f"SELECT count(*) FROM {t}{where}"), {"p": project_id}).scalar_one()  # noqa: S608 - constant table names
            for t in TABLES
        )


def rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params)]


def status_of(response) -> status_pb2.Status:
    assert response.headers["content-type"] == PROTOBUF
    return status_pb2.Status.FromString(response.content)


def violations_of(response) -> list[error_details_pb2.BadRequest.FieldViolation]:
    status = status_of(response)
    assert len(status.details) == 1
    bad = error_details_pb2.BadRequest()
    assert status.details[0].Unpack(bad)
    return list(bad.field_violations)
