"""`/api/v1` access control and error bodies (ARCHITECTURE.md §5, SECURITY.md §2–4, CR-06).

Every `/api/v1` route depends on `require_admin`, which runs before any parameter is
validated, so an unauthenticated caller learns nothing about parameters or records:
1. auth-failure bucket per client address (shared with ingest) -> 429
2. exactly one `Authorization: Bearer <LLMI_ADMIN_TOKEN>`, compared in constant time -> 401
3. `/api/v1` bucket per client address -> 429

The single admin token reads every project (CR-06), so there is no authenticated-but-
forbidden state and no 403: a record outside the requested project is a 404 that is byte-
identical to a record that does not exist.
"""

import hashlib
import hmac
import logging

from fastapi import Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import Settings
from app.core.ratelimit import KeyedTokenBucket

logger = logging.getLogger("app.api")

API_PREFIX = "/api/"
NO_STORE = {"Cache-Control": "no-store"}


class APIError(Exception):
    """An `/api/v1` error: a fixed code and a generic message, never a request value."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        fields: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields
        self.headers = headers or {}


def error_response(err: APIError) -> JSONResponse:
    payload: dict[str, object] = {"code": err.code, "message": err.message}
    if err.fields:
        payload["fields"] = err.fields
    return JSONResponse(
        {"error": payload}, status_code=err.status_code, headers={**NO_STORE, **err.headers}
    )


def invalid(field: str, message: str) -> APIError:
    return APIError(422, "invalid_request", message, fields=[field])


def not_found() -> APIError:
    return APIError(404, "not_found", "resource not found")


def storage_unavailable() -> APIError:
    return APIError(
        503, "storage_unavailable", "storage unavailable; retry later", headers={"Retry-After": "5"}
    )


def internal_error() -> APIError:
    return APIError(500, "internal_error", "internal error")


def _too_many_requests(retry_after: int) -> APIError:
    return APIError(
        429,
        "rate_limited",
        "too many requests; retry later",
        headers={"Retry-After": str(retry_after)},
    )


def _unauthorized() -> APIError:
    return APIError(
        401,
        "unauthorized",
        "missing or invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


class AdminGuard:
    def __init__(
        self,
        settings: Settings,
        authfail_limiter: KeyedTokenBucket,
        api_limiter: KeyedTokenBucket,
    ) -> None:
        # Digests of equal length: compare_digest then takes the same time for any input.
        self._token_digest = _digest(settings.admin_token.get_secret_value())
        self.authfail_limiter = authfail_limiter
        self.api_limiter = api_limiter

    def check(self, presented: list[str], client: str) -> None:
        retry_after = self.authfail_limiter.blocked(client)
        if retry_after is not None:
            logger.warning("api rejected reason=authfail_rate_limited client=%s", client)
            raise _too_many_requests(retry_after)
        reason = self._failure_reason(presented)
        if reason is not None:
            self.authfail_limiter.consume(client)
            logger.warning("api auth failed reason=%s client=%s", reason, client)
            raise _unauthorized()
        retry_after = self.api_limiter.acquire(client)
        if retry_after is not None:
            logger.warning("api rejected reason=api_rate_limited client=%s", client)
            raise _too_many_requests(retry_after)

    def _failure_reason(self, presented: list[str]) -> str | None:
        if not presented:
            return "missing"
        if len(presented) > 1:
            return "multiple"
        scheme, _, token = presented[0].partition(" ")
        if scheme.lower() != "bearer" or not token:
            # Still one digest + compare, so this path costs the same as a mismatch.
            hmac.compare_digest(_digest(presented[0]), self._token_digest)
            return "malformed"
        if not hmac.compare_digest(_digest(token), self._token_digest):
            return "mismatch"
        return None


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).digest()


async def require_admin(request: Request) -> None:
    guard: AdminGuard = request.app.state.admin_guard
    client = request.client.host if request.client else "unknown"
    guard.check(request.headers.getlist("authorization"), client)


# ---------------------------------------------------------------- exception handlers
async def api_error_handler(_request: Request, exc: APIError) -> Response:
    return error_response(exc)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> Response:
    """`/api/*`: 422 with the offending parameter *names* only. FastAPI's default body echoes
    the rejected input, which must never happen here. Other paths keep the default."""
    if not request.url.path.startswith(API_PREFIX):
        return await request_validation_exception_handler(request, exc)
    fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
    return error_response(
        APIError(422, "invalid_request", "request parameters are invalid", fields=fields)
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """Unknown `/api/*` routes and methods use the same JSON error shape."""
    if not request.url.path.startswith(API_PREFIX):
        return await http_exception_handler(request, exc)
    codes = {404: "not_found", 405: "method_not_allowed"}
    messages = {404: "resource not found", 405: "method not allowed"}
    return error_response(
        APIError(
            exc.status_code,
            codes.get(exc.status_code, "http_error"),
            messages.get(exc.status_code, "request failed"),
            headers=dict(exc.headers or {}),
        )
    )
