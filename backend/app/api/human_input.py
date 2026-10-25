"""Write-side plumbing shared by the review and dataset endpoints (Phase 4B).

- `write_transaction`: one read-write transaction per request. It commits only when the whole
  unit succeeded, so a request either stores everything it validated or nothing (a failure
  after an insert rolls the insert back). Storage errors map to the read API's codes.
- `free_text` / `label`: the human-input rules of `services/redaction.py` turned into `422`
  errors that name the field and never echo its value.
"""

import logging
from collections.abc import Callable

from fastapi import Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.admin import APIError, internal_error, invalid, storage_unavailable
from app.core.db import RETRYABLE_DB_ERRORS
from app.services import redaction

logger = logging.getLogger("app.api")

# Integrity failures mapped by constraint name; anything else is unexpected (500).
ConstraintMap = dict[str, Callable[[], APIError]]


def constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def write_transaction[T](
    request: Request, fn: Callable[[Session], T], conflicts: ConstraintMap | None = None
) -> T:
    factory = request.app.state.session_factory
    try:
        with factory() as session, session.begin():
            return fn(session)
    except IntegrityError as exc:
        name = constraint_name(exc)
        if conflicts and name in conflicts:
            raise conflicts[name]() from None
        logger.error("api write failed: IntegrityError constraint=%s", name)
        raise internal_error() from None
    except RETRYABLE_DB_ERRORS as exc:
        logger.error("api write failed: %s", type(exc).__name__)
        raise storage_unavailable() from None
    except SQLAlchemyError as exc:
        logger.error("api write failed: %s", type(exc).__name__)
        raise internal_error() from None


def conflict(code: str, message: str) -> APIError:
    return APIError(409, code, message)


def free_text(field: str, value: str, max_len: int) -> redaction.Redacted:
    """Multi-line human text, masked before storage. The length bound applies to the stored
    (masked) text, which can be longer than the input."""
    text = redaction.normalize_newlines(value).strip()
    if not text:
        raise invalid(field, f"{field} must not be empty")
    if len(text) > max_len:
        raise invalid(field, f"{field} must be at most {max_len} characters")
    if redaction.has_unsafe_text_characters(text):
        raise invalid(field, f"{field} contains control or invisible formatting characters")
    result = redaction.redact_free_text(text)
    if len(result.text) > max_len:
        raise invalid(field, f"{field} exceeds {max_len} characters after redaction")
    return result


def label(field: str, value: str, max_len: int) -> str:
    """A single-line label: a self-declared name or a dataset name."""
    text = value.strip()
    if not 1 <= len(text) <= max_len:
        raise invalid(field, f"{field} must be 1-{max_len} characters")
    if redaction.has_unsafe_label_characters(text):
        raise invalid(field, f"{field} must be a single line without control characters")
    if redaction.label_is_sensitive(text):
        raise invalid(field, f"{field} looks like contact data or a secret; use a name or handle")
    return text
