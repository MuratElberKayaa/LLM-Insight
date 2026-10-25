"""Request bodies of the `/api/v1` write endpoints (Phase 4B, decision D7).

FastAPI's own body parameters read the whole body before any limit applies and echo rejected
input in their 422 body. Write endpoints therefore read their JSON here:
- `Content-Type: application/json` only (`415` otherwise);
- at most `MAX_BODY_BYTES`, checked on the declared length and again while streaming (`413`);
- strict UTF-8 and strict JSON: no NaN/Infinity, no duplicate object keys (ambiguous input);
- then a Pydantic model with `extra="forbid"` and strict types, whose errors are reduced to
  the offending top-level field *names* (`422 invalid_request`), never their values.
"""

import json
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ValidationError

from app.api.admin import APIError

MAX_BODY_BYTES = 64 * 1024


def _too_large() -> APIError:
    return APIError(413, "payload_too_large", f"request body exceeds {MAX_BODY_BYTES} bytes")


def _invalid_body() -> APIError:
    return APIError(422, "invalid_request", "request body is not valid JSON", fields=["body"])


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


async def read_json(request: Request) -> Any:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise APIError(415, "unsupported_media_type", "Content-Type must be application/json")
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    try:
        return json.loads(
            b"".join(chunks).decode("utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_keys,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _invalid_body() from None


def parse_model[M: BaseModel](model: type[M], data: Any) -> M:
    if not isinstance(data, dict):
        raise _invalid_body()
    try:
        return model.model_validate(data, strict=True)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")}) or ["body"]
        raise APIError(422, "invalid_request", "request body is invalid", fields=fields) from None
