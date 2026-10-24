"""OTLP trace ingest pipeline (TELEMETRY_CONTRACT.md §1.1 steps 4–13).

Everything after the headers runs synchronously in a worker thread: decompression,
protobuf decode, validation and one database transaction. Success is reported only after
COMMIT returned (ADR-18).
"""

import logging
import threading
from dataclasses import dataclass

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core import security
from app.core.config import Settings
from app.core.db import RETRYABLE_DB_ERRORS
from app.core.ratelimit import KeyedTokenBucket
from app.repositories import api_keys, telemetry
from app.services.ingestion import errors
from app.services.ingestion.body import gunzip_bounded
from app.services.ingestion.normalize import NormalizedSpan, count_spans, normalize

logger = logging.getLogger("app.ingest")


@dataclass(frozen=True)
class AuthenticatedProject:
    project_id: int
    key_id: int


class IngestStats:
    """Process-local counters (no persistence; reset on restart)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.usage_key_collisions = 0
        self.spans_reexported = 0
        self.span_content_conflicts = 0
        self.batches_committed = 0

    def record(self, result: telemetry.PersistResult) -> None:
        with self._lock:
            self.usage_key_collisions += result.usage_key_collisions
            self.spans_reexported += result.spans_reexported
            self.span_content_conflicts += result.span_content_conflicts
            self.batches_committed += 1


class Ingestor:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        authfail_limiter: KeyedTokenBucket,
        ingest_limiter: KeyedTokenBucket,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.authfail_limiter = authfail_limiter
        self.ingest_limiter = ingest_limiter
        self.stats = IngestStats()
        self._pepper = settings.key_pepper.get_secret_value().encode("utf-8")

    # ------------------------------------------------------------ steps 4–6 (headers only)
    def check_authfail_limit(self, client: str) -> None:
        retry_after = self.authfail_limiter.blocked(client)
        if retry_after is not None:
            logger.warning("ingest rejected reason=authfail_rate_limited client=%s", client)
            raise errors.too_many_requests(retry_after)

    def authenticate(self, presented: list[str], client: str) -> AuthenticatedProject:
        """Every failure path returns the same 401 and counts toward the auth-failure bucket.
        Logs carry a reason code, the client address and (if parsable) the key prefix only."""
        value = presented[0] if len(presented) == 1 else None
        prefix = security.parse_prefix(value) if value is not None else None
        if value is None:
            reason = "missing" if not presented else "multiple"
        elif prefix is None:
            reason = "malformed"
            security.hash_matches(self._pepper, value, None)  # same work as a lookup miss
        else:
            try:
                with self.session_factory() as session:
                    found = api_keys.find_by_prefix(session, prefix)
            except RETRYABLE_DB_ERRORS as exc:
                logger.error("ingest auth lookup failed: %s", type(exc).__name__)
                raise errors.storage_unavailable() from None
            except SQLAlchemyError as exc:
                logger.error("ingest auth lookup failed: %s", type(exc).__name__)
                raise errors.internal_error() from None
            ok = security.hash_matches(self._pepper, value, found.key_hash if found else None)
            if found is None:
                reason = "unknown_prefix"
            elif not ok:
                reason = "hash_mismatch"
            elif found.revoked:
                reason = "revoked"
            else:
                return AuthenticatedProject(found.project_id, found.key_id)
        self.authfail_limiter.consume(client)
        logger.warning(
            "ingest auth failed reason=%s prefix=%s client=%s", reason, prefix or "-", client
        )
        raise errors.unauthorized()

    def check_ingest_limit(self, project: AuthenticatedProject) -> None:
        retry_after = self.ingest_limiter.acquire(project.key_id)
        if retry_after is not None:
            logger.warning("ingest rejected reason=key_rate_limited key_id=%s", project.key_id)
            raise errors.too_many_requests(retry_after)

    # ------------------------------------------------------------ steps 8–13 (body)
    def process(self, project: AuthenticatedProject, wire: bytes, gzipped: bool) -> int:
        """Decode, validate and persist. Returns the number of spans received."""
        raw = gunzip_bounded(wire, self.settings.otlp_max_decompressed_bytes) if gzipped else wire
        req = ExportTraceServiceRequest()
        try:
            req.ParseFromString(raw)
        except DecodeError:
            raise errors.invalid_protobuf() from None
        n = count_spans(req)
        if n > self.settings.otlp_max_spans_per_request:
            raise errors.too_many_spans()
        spans, violations = normalize(req)
        if violations:
            raise errors.invalid_spans(violations)
        if not spans:
            return 0  # step 12: an empty request writes nothing
        result = self._persist(project.project_id, spans)
        self.stats.record(result)
        if result.usage_key_collisions:
            logger.warning("usage call-key collisions=%d", result.usage_key_collisions)
        if result.span_content_conflicts:
            # Counts only: the differing values are never logged. The first write is kept.
            logger.warning("span re-export content conflicts=%d", result.span_content_conflicts)
        logger.info(
            "ingest committed spans=%d new_spans=%d new_usage=%d traces=%d",
            result.spans_received,
            result.spans_inserted,
            result.usage_inserted,
            result.traces_touched,
        )
        return n

    def _persist(self, project_id: int, spans: list[NormalizedSpan]) -> telemetry.PersistResult:
        session = self.session_factory()
        try:
            result = telemetry.persist_batch(session, project_id, spans)
            session.commit()  # the 200 is sent only after this returns
            return result
        except RETRYABLE_DB_ERRORS as exc:
            _rollback_quietly(session)
            logger.error("ingest transaction rolled back: %s", type(exc).__name__)
            raise errors.storage_unavailable() from None
        except SQLAlchemyError as exc:
            _rollback_quietly(session)
            logger.error("ingest transaction rolled back: %s", type(exc).__name__)
            raise errors.internal_error() from None
        except BaseException:
            _rollback_quietly(session)
            raise
        finally:
            session.close()


def _rollback_quietly(session: Session) -> None:
    """A lost connection can make ROLLBACK itself fail; the server discards the transaction
    either way, so the original error decides the response."""
    try:
        session.rollback()
    except SQLAlchemyError as exc:
        logger.error("rollback failed: %s", type(exc).__name__)
