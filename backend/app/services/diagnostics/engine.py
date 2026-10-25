"""Deterministic trace diagnostics over stored metadata (`llminsight.diagnostics.v1`).

Input: one trace's stored rows (trace summary, spans, usage rows) exactly as the read API
loads them. Output: a JSON-ready dict. The engine is pure (no I/O, no clock, no randomness),
so the same stored rows always give the same bytes (idempotent reads).

Principles (ADR-10, ADR-19, reports/phase-4a.md):
- a finding exists only when stored rows satisfy its trigger, and it lists those rows as
  evidence; a conclusion that rests on the *absence* of data is suppressed or marked
  incomplete when the analysis did not cover the whole trace
- facts and hypotheses are separate fields; every hypothesis is unverified
- unknown stays unknown (NULL tokens and costs are never treated as 0)
- span attributes are untrusted: they are read through type-checked accessors and never
  interpolated into text; only allowlisted fields are echoed (no span names, no attribute
  dump), so no content can leak even if a malformed row reached storage
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.diagnostics import catalog
from app.services.ingestion.attrmap import INFERENCE_OPERATIONS

MAX_FINDINGS = 100  # per response; `summary.findings_by_category` counts all of them
MAX_EVIDENCE = 25  # span references per finding; `evidence_total` counts all of them
MAX_LIST = 20  # values per list fact; a `*_count` fact gives the full number
INT32_MAX = 2**31 - 1


@dataclass(frozen=True)
class TraceInput:
    trace_id: str
    status: str
    provenance: str
    span_count: int


@dataclass(frozen=True)
class SpanInput:
    span_id: str
    parent_span_id: str | None
    parent_stored: bool  # computed over all stored spans, not only the analysed ones
    kind: str
    start_ns: int
    end_ns: int
    status: str
    error_type: str | None
    attributes: Any  # untrusted JSONB object
    provenance: str
    server_dropped_attributes_count: int


@dataclass(frozen=True)
class UsageInput:
    span_id: str
    usage_source: str
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cache_write_input_tokens: int | None
    reasoning_output_tokens: int | None
    cost_status: str
    cost_unknown_reason: str | None


# ---------------------------------------------------------------- untrusted attribute access
def _attrs(span: SpanInput) -> Mapping[str, Any]:
    return span.attributes if isinstance(span.attributes, Mapping) else {}


def _text(span: SpanInput, key: str, max_len: int = 256) -> str | None:
    v = _attrs(span).get(key)
    return v if isinstance(v, str) and 0 < len(v) <= max_len else None


def _int(span: SpanInput, key: str) -> int | None:
    v = _attrs(span).get(key)
    return v if type(v) is int else None  # bool is not a count


def _texts(span: SpanInput, key: str) -> list[str] | None:
    v = _attrs(span).get(key)
    if not isinstance(v, list):
        return None
    return [x for x in v if isinstance(x, str) and 0 < len(x) <= 64][:16]


def _call_id(span: SpanInput) -> str | None:
    return _text(span, "llminsight.call_id", 64)


def _attempt(span: SpanInput) -> int | None:
    v = _int(span, "llminsight.attempt")
    return v if v is not None and 1 <= v <= INT32_MAX else None


def _is_inference(span: SpanInput) -> bool:
    return _text(span, "gen_ai.operation.name", 64) in INFERENCE_OPERATIONS


def _is_error(span: SpanInput) -> bool:
    return span.status == "ERROR"


def _error_type(span: SpanInput) -> str | None:
    v = span.error_type
    return v if isinstance(v, str) and 0 < len(v) <= 256 else None


def _role(span: SpanInput) -> str:
    if span.parent_span_id is None:
        return "root"
    return "child" if span.parent_stored else "orphan"


def _order(span: SpanInput) -> tuple[int, str]:
    return span.start_ns, span.span_id


def _duration(span: SpanInput) -> int:
    return max(span.end_ns - span.start_ns, 0)


def error_class(error_type: str | None) -> str:
    """Exact-match interpretation of a recorded `error.type` (catalog.ERROR_TYPE_CLASSES)."""
    if error_type is None:
        return "not_recorded"
    if error_type in catalog.ERROR_TYPE_CLASSES:
        return catalog.ERROR_TYPE_CLASSES[error_type]
    if len(error_type) == 3 and error_type.isascii() and error_type.isdigit():
        if error_type[0] == "5":
            return "http_5xx"
        if error_type[0] == "4":
            return "http_4xx"
    return "not_interpreted"


def _capped(values: Iterable[Any]) -> list[Any]:
    return list(values)[:MAX_LIST]


# ---------------------------------------------------------------- context
class _Trace:
    def __init__(
        self,
        trace: TraceInput,
        spans: Sequence[SpanInput],
        usage: Sequence[UsageInput],
        spans_truncated: bool,
        usage_truncated: bool,
    ) -> None:
        self.trace = trace
        self.spans = sorted(spans, key=_order)
        self.by_id = {s.span_id: s for s in self.spans}
        self.usage = {u.span_id: u for u in usage}
        self.usage_rows = len(usage)
        self.spans_truncated = spans_truncated
        self.usage_truncated = usage_truncated
        self.complete = (
            not spans_truncated and not usage_truncated and len(self.spans) == trace.span_count
        )
        self.inference = [s for s in self.spans if _is_inference(s)]
        self.children: dict[str, list[SpanInput]] = defaultdict(list)
        for s in self.spans:
            if s.parent_span_id is not None and s.parent_span_id in self.by_id:
                self.children[s.parent_span_id].append(s)

    def descendants(self, span: SpanInput) -> list[SpanInput]:
        seen = {span.span_id}
        out: list[SpanInput] = []
        stack = list(self.children.get(span.span_id, ()))
        while stack:
            s = stack.pop()
            if s.span_id in seen:  # a client-made parent cycle
                continue
            seen.add(s.span_id)
            out.append(s)
            stack.extend(self.children.get(s.span_id, ()))
        return out


# ---------------------------------------------------------------- serialization
def _evidence(ctx: _Trace, span: SpanInput) -> dict[str, Any]:
    inference = None
    if _is_inference(span):
        inference = {
            "operation_name": _text(span, "gen_ai.operation.name", 64),
            "provider": _text(span, "gen_ai.provider.name", 64),
            "requested_model": _text(span, "gen_ai.request.model"),
            "response_model": _text(span, "gen_ai.response.model"),
            "call_id": _call_id(span),
            "attempt": _attempt(span),
            "has_response_id": _text(span, "gen_ai.response.id") is not None,
            "finish_reasons": _texts(span, "gen_ai.response.finish_reasons"),
            "request_max_tokens": _int(span, "gen_ai.request.max_tokens"),
        }
    u = ctx.usage.get(span.span_id)
    usage = None
    if u is not None:
        usage = {
            "usage_source": u.usage_source,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cached_input_tokens": u.cached_input_tokens,
            "cache_write_input_tokens": u.cache_write_input_tokens,
            "reasoning_output_tokens": u.reasoning_output_tokens,
            "cost_status": u.cost_status,
            "cost_unknown_reason": u.cost_unknown_reason,
        }
    passed = _attrs(span).get("llminsight.validation.passed")
    rule = _text(span, "llminsight.validation.rule", 128)
    validation = None
    if isinstance(passed, bool) or rule is not None:
        validation = {"rule": rule, "passed": passed if isinstance(passed, bool) else None}
    return {
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "role": _role(span),
        "kind": span.kind,
        "status": span.status,
        "error_type": _error_type(span),
        "start_time_unix_nano": str(span.start_ns),
        "duration_ns": str(_duration(span)),
        "provenance": span.provenance,
        "provenance_verified": False,
        "server_dropped_attributes_count": span.server_dropped_attributes_count,
        "inference": inference,
        "usage": usage,
        "validation": validation,
    }


def _fact(name: str, value: Any) -> dict[str, Any]:
    return {"name": name, "value": value}


@dataclass
class _Finding:
    category: str
    subject: dict[str, Any]
    facts: list[dict[str, Any]]
    spans: list[SpanInput]
    hypothesis: str
    error_classes: list[str] | None = None  # failed attempts' classes, for an interpretation
    limitations: tuple[str, ...] = ()
    data_complete: bool = True

    @property
    def subject_key(self) -> str:
        if self.subject["type"] == "logical_call":
            return f"call:{self.subject['call_id']}"
        if self.subject["type"] == "span":
            return f"span:{self.subject['span_id']}"
        return "trace"

    def sort_key(self) -> tuple[int, int, str, str]:
        first = min((_order(s) for s in self.spans), default=(0, ""))
        return catalog.CATEGORY_ORDER[self.category], first[0], first[1], self.subject_key

    def render(self, ctx: _Trace) -> dict[str, Any]:
        spec = catalog.CATEGORIES[self.category]
        spans = sorted({s.span_id: s for s in self.spans}.values(), key=_order)
        limitations = set(spec.limitations) | set(self.limitations)
        provenances = {s.provenance for s in spans if _is_inference(s)} or {ctx.trace.provenance}
        limitations |= {
            catalog.PROVENANCE_LIMITATION[p]
            for p in provenances
            if p in catalog.PROVENANCE_LIMITATION
        }
        data_complete = self.data_complete and ctx.complete
        if not ctx.complete:
            limitations.add("analysis_truncated")
        if self.category != "attributes_dropped_at_ingest" and any(
            s.server_dropped_attributes_count > 0 for s in spans
        ):
            limitations.add("evidence_attributes_dropped")
            data_complete = False
        interpretation = None
        steps = list(spec.steps)
        if self.error_classes is not None:
            distinct = sorted(set(self.error_classes))
            key = distinct[0] if len(distinct) == 1 else "mixed"
            chosen = catalog.INTERPRETATIONS[key]
            interpretation = {
                "code": catalog.INTERPRETATION_CODES[key],
                "statement": chosen.statement,
            }
            steps += [
                step
                for cls in distinct
                for step in catalog.INTERPRETATIONS[cls].steps
                if step not in steps
            ]
        return {
            "id": f"{self.category}:{self.subject_key}",
            "category": self.category,
            "group": spec.group,
            "subject": self.subject,
            "trigger": spec.trigger,
            "observed_facts": self.facts,
            "evidence": [_evidence(ctx, s) for s in spans[:MAX_EVIDENCE]],
            "evidence_total": len(spans),
            "evidence_truncated": len(spans) > MAX_EVIDENCE,
            "hypothesis": {
                "code": self.hypothesis,
                "statement": spec.hypotheses[self.hypothesis],
                "error_interpretation": interpretation,
                "verified": False,
            },
            "established": list(spec.established),
            "not_established": list(spec.not_established),
            "limitations": [
                {"code": code, "text": catalog.LIMITATIONS[code]} for code in sorted(limitations)
            ],
            "data_complete": data_complete,
            "investigation_steps": steps,
        }


# ---------------------------------------------------------------- rules: logical calls
def _gaps(numbers: list[int]) -> tuple[list[int], int]:
    """Missing numbers in 1..max(numbers) (sorted, distinct input): the first MAX_LIST of
    them and how many there are. Linear in the input, whatever the attempt numbers are."""
    missing: list[int] = []
    count, expected = 0, 1
    for n in numbers:
        if n > expected:
            count += n - expected
            missing.extend(range(expected, min(n, expected + MAX_LIST - len(missing))))
        expected = n + 1
    return missing[:MAX_LIST], count


def _attempt_order(span: SpanInput) -> tuple[int, int, str]:
    return _attempt(span) or 0, span.start_ns, span.span_id


def _failure_facts(attempts: list[SpanInput], failed: list[SpanInput]) -> list[dict[str, Any]]:
    types = sorted({t for s in failed if (t := _error_type(s)) is not None})
    numbers = sorted({n for s in attempts if (n := _attempt(s)) is not None})
    return [
        _fact("attempts_observed", len(attempts)),
        _fact("attempt_numbers", _capped(numbers)),
        _fact("failed_attempts", len(failed)),
        _fact("error_types", _capped(types)),
        _fact("error_types_count", len(types)),
        _fact("failed_attempts_without_error_type", sum(_error_type(s) is None for s in failed)),
        _fact("error_classes", sorted({error_class(_error_type(s)) for s in failed})),
    ]


def _logical_call(ctx: _Trace, call_id: str, group: list[SpanInput]) -> list[_Finding]:
    attempts = sorted(group, key=_attempt_order)
    counted = Counter(n for s in attempts if (n := _attempt(s)) is not None)
    numbers = sorted(counted)
    duplicates = [n for n in numbers if counted[n] > 1]
    unnumbered = [s for s in attempts if _attempt(s) is None]
    missing, missing_count = _gaps(numbers)
    failed = [s for s in attempts if _is_error(s)]
    succeeded = [s for s in attempts if not _is_error(s)]
    final = attempts[-1]

    conditions = []
    if duplicates:
        conditions.append("duplicate_attempt_number")
    if unnumbered:
        conditions.append("attempt_number_missing_or_invalid")
    if failed and succeeded and _is_error(final):
        conditions.append("failure_after_success")
    gap = missing_count > 0
    call_limits = ("attempt_sequence_gap",) if gap else ()
    complete = not gap and not conditions
    subject = {"type": "logical_call", "call_id": call_id}
    out: list[_Finding] = []

    if failed and not _is_error(final):
        member_ids = {s.span_id for s in attempts}
        other_errors = sum(_is_error(s) and s.span_id not in member_ids for s in ctx.spans)
        out.append(
            _Finding(
                "inference_call_recovered_after_retry",
                subject,
                [
                    *_failure_facts(attempts, failed),
                    _fact("final_attempt", _attempt(final)),
                    _fact("final_attempt_status", final.status),
                    _fact("failed_attempts_duration_ns", str(sum(map(_duration, failed)))),
                    _fact("trace_status", ctx.trace.status),
                    _fact("other_error_spans", other_errors),
                ],
                attempts,
                "transient_failure_recovered",
                error_classes=[error_class(_error_type(s)) for s in failed],
                limitations=call_limits,
                data_complete=complete,
            )
        )
    elif failed and not succeeded:
        category, hypothesis = (
            ("inference_call_failed_after_retries", "persistent_failure_across_attempts")
            if len(attempts) >= 2
            else ("inference_call_failed", "failed_single_attempt")
        )
        out.append(
            _Finding(
                category,
                subject,
                [
                    *_failure_facts(attempts, failed),
                    _fact("attempts_duration_ns", str(sum(map(_duration, attempts)))),
                    _fact("trace_status", ctx.trace.status),
                ],
                attempts,
                hypothesis,
                error_classes=[error_class(_error_type(s)) for s in failed],
                limitations=call_limits,
                data_complete=complete,
            )
        )

    if gap:
        out.append(
            _Finding(
                "retry_sequence_incomplete",
                subject,
                [
                    _fact("attempt_numbers_observed", _capped(numbers)),
                    _fact("attempt_numbers_missing", missing),
                    _fact("attempt_numbers_missing_count", missing_count),
                    _fact("highest_attempt_observed", numbers[-1]),
                ],
                attempts,
                "attempts_not_stored",
                limitations=call_limits,
                data_complete=False,
            )
        )
    if conditions:
        out.append(
            _Finding(
                "attempt_metadata_inconsistent",
                subject,
                [
                    _fact("conditions", conditions),
                    _fact("attempt_numbers", _capped(numbers)),
                    _fact("duplicate_attempt_numbers", _capped(duplicates)),
                    _fact("attempts_without_usable_number", len(unnumbered)),
                ],
                attempts,
                "call_metadata_not_sdk_shaped",
                data_complete=False,
            )
        )
    return out


def _standalone_attempt(span: SpanInput) -> list[_Finding]:
    """An inference span without a usable call ID: it cannot be grouped with retries."""
    out: list[_Finding] = []
    subject = {"type": "span", "span_id": span.span_id}
    if _is_error(span):
        out.append(
            _Finding(
                "inference_call_failed",
                subject,
                [*_failure_facts([span], [span]), _fact("call_id_recorded", False)],
                [span],
                "failed_single_attempt",
                error_classes=[error_class(_error_type(span))],
                limitations=("no_call_id",),
            )
        )
    conditions = []
    if "llminsight.call_id" in _attrs(span):
        conditions.append("call_id_unusable")
    if _attempt(span) is not None:
        conditions.append("attempt_without_call_id")
    if conditions:
        out.append(
            _Finding(
                "attempt_metadata_inconsistent",
                subject,
                [
                    _fact("conditions", conditions),
                    _fact("attempt_numbers", [n] if (n := _attempt(span)) else []),
                    _fact("duplicate_attempt_numbers", []),
                    _fact("attempts_without_usable_number", 0 if _attempt(span) else 1),
                ],
                [span],
                "call_metadata_not_sdk_shaped",
                limitations=("no_call_id",),
                data_complete=False,
            )
        )
    return out


def _call_findings(ctx: _Trace) -> list[_Finding]:
    groups: dict[str, list[SpanInput]] = defaultdict(list)
    out: list[_Finding] = []
    for s in ctx.inference:
        call_id = _call_id(s)
        if call_id is None:
            out += _standalone_attempt(s)
        else:
            groups[call_id].append(s)
    for call_id in sorted(groups):
        out += _logical_call(ctx, call_id, groups[call_id])
    return out


# ---------------------------------------------------------------- rules: other span signals
def _error_span_findings(ctx: _Trace) -> list[_Finding]:
    out = []
    for s in ctx.spans:
        if not _is_error(s) or _is_inference(s):
            continue
        below = ctx.descendants(s)
        failed_below = [d for d in below if _is_inference(d) and _is_error(d)]
        out.append(
            _Finding(
                "non_inference_span_error",
                {"type": "span", "span_id": s.span_id},
                [
                    _fact("kind", s.kind),
                    _fact("error_type", _error_type(s)),
                    _fact("http_response_status_code", _int(s, "http.response.status_code")),
                    _fact("descendants_analyzed", len(below)),
                    _fact("failed_inference_attempts_in_subtree", len(failed_below)),
                ],
                [s, *failed_below],
                "related_to_failed_inference_below"
                if failed_below
                else "outside_instrumented_inference",
            )
        )
    return out


def _validation_findings(ctx: _Trace) -> list[_Finding]:
    return [
        _Finding(
            "application_validation_failed",
            {"type": "span", "span_id": s.span_id},
            [
                _fact("rule", _text(s, "llminsight.validation.rule", 128)),
                _fact("span_status", s.status),
            ],
            [s],
            "application_check_failed",
        )
        for s in ctx.spans
        if _attrs(s).get("llminsight.validation.passed") is False
    ]


def _finish_reason_findings(ctx: _Trace) -> list[_Finding]:
    out = []
    openai = [s for s in ctx.inference if _text(s, "gen_ai.provider.name", 64) == "openai"]
    for reason, category, hypothesis in (
        ("length", "inference_output_truncated", "output_cut_at_token_limit"),
        ("content_filter", "inference_content_filtered", "provider_content_filter_applied"),
    ):
        hits = [s for s in openai if reason in (_texts(s, "gen_ai.response.finish_reasons") or ())]
        if hits:
            out.append(
                _Finding(
                    category,
                    {"type": "trace"},
                    [
                        _fact("attempts_affected", len(hits)),
                        _fact(
                            "attempts_with_request_max_tokens",
                            sum(_int(s, "gen_ai.request.max_tokens") is not None for s in hits),
                        ),
                    ],
                    hits,
                    hypothesis,
                )
            )
    return out


# ---------------------------------------------------------------- rules: usage and cost
def _usage_findings(ctx: _Trace) -> list[_Finding]:
    out = []
    trace = {"type": "trace"}
    if not ctx.usage_truncated:
        absent = [s for s in ctx.inference if s.span_id not in ctx.usage]
        if absent:
            # Same call identity as another inference span of this trace that has a row.
            def identity(s: SpanInput) -> tuple[str, str] | None:
                response_id = _text(s, "gen_ai.response.id")
                if response_id is None:
                    return None
                return _text(s, "gen_ai.provider.name", 64) or "unknown", response_id

            recorded = {identity(s) for s in ctx.inference if s.span_id in ctx.usage}
            duplicates = sum(identity(s) is not None and identity(s) in recorded for s in absent)
            out.append(
                _Finding(
                    "usage_ledger_row_missing",
                    trace,
                    [
                        _fact("inference_spans_without_usage_row", len(absent)),
                        _fact("duplicate_identity_in_trace", duplicates),
                    ],
                    absent,
                    "call_identity_already_recorded",
                )
            )

    missing_tokens = []
    for s in ctx.inference:
        u = ctx.usage.get(s.span_id)
        if u is None or _is_error(s):
            continue  # failed attempts normally carry no usage (TELEMETRY_CONTRACT.md §4)
        embeddings = _text(s, "gen_ai.operation.name", 64) == "embeddings"
        if u.input_tokens is None or (u.output_tokens is None and not embeddings):
            missing_tokens.append((s, u))
    if missing_tokens:
        out.append(
            _Finding(
                "token_usage_missing",
                trace,
                [
                    _fact("attempts_affected", len(missing_tokens)),
                    _fact(
                        "input_tokens_unknown",
                        sum(u.input_tokens is None for _, u in missing_tokens),
                    ),
                    _fact(
                        "output_tokens_unknown",
                        sum(u.output_tokens is None for _, u in missing_tokens),
                    ),
                ],
                [s for s, _ in missing_tokens],
                "usage_not_reported_by_instrumentation",
            )
        )

    by_reason: dict[str, list[tuple[SpanInput, UsageInput]]] = defaultdict(list)
    for s in ctx.inference:
        u = ctx.usage.get(s.span_id)
        if u is not None and u.cost_status == "unknown" and u.cost_unknown_reason is not None:
            by_reason[u.cost_unknown_reason].append((s, u))

    inconsistent = by_reason.get("usage_inconsistent", [])
    if inconsistent:

        def over(part: Iterable[int | None], total: int | None) -> bool:
            return total is not None and sum(p or 0 for p in part) > total

        out.append(
            _Finding(
                "usage_inconsistent",
                trace,
                [
                    _fact("attempts_affected", len(inconsistent)),
                    _fact(
                        "input_subsets_exceed_input",
                        sum(
                            over(
                                (u.cached_input_tokens, u.cache_write_input_tokens), u.input_tokens
                            )
                            for _, u in inconsistent
                        ),
                    ),
                    _fact(
                        "reasoning_exceeds_output",
                        sum(
                            over((u.reasoning_output_tokens,), u.output_tokens)
                            for _, u in inconsistent
                        ),
                    ),
                ],
                [s for s, _ in inconsistent],
                "instrumentation_reported_inconsistent_usage",
            )
        )

    unpriced = by_reason.get("price_missing", [])
    if unpriced:
        models = sorted(
            {
                (
                    _text(s, "gen_ai.provider.name", 64) or "unknown",
                    _text(s, "gen_ai.request.model") or "",
                    _text(s, "gen_ai.response.model") or "",
                )
                for s, _ in unpriced
            }
        )
        tiers = sorted({_text(s, "openai.response.service_tier", 64) or "" for s, _ in unpriced})
        hosts = sorted({_text(s, "server.address", 253) or "" for s, _ in unpriced})
        out.append(
            _Finding(
                "cost_unknown_price_missing",
                trace,
                [
                    _fact("attempts_affected", len(unpriced)),
                    _fact(
                        "models",
                        [
                            {
                                "provider": p,
                                "requested_model": r or None,
                                "response_model": m or None,
                            }
                            for p, r, m in models[:MAX_LIST]
                        ],
                    ),
                    _fact("models_count", len(models)),
                    _fact("service_tiers", _capped(t or None for t in tiers)),
                    _fact("server_addresses", _capped(h or None for h in hosts)),
                ],
                [s for s, _ in unpriced],
                "no_matching_stored_price",
            )
        )
    return out


# ---------------------------------------------------------------- rules: trace structure
def _cycle_members(ctx: _Trace) -> list[SpanInput]:
    """Spans on a parent-link cycle among the analysed spans (each span has <= 1 parent)."""
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    on_cycle: set[str] = set()
    for start in ctx.spans:
        path: list[str] = []
        node: str | None = start.span_id
        while node is not None and node in ctx.by_id and state.get(node) is None:
            state[node] = 1
            path.append(node)
            node = ctx.by_id[node].parent_span_id
        if node is not None and state.get(node) == 1:  # back on the current path
            on_cycle.update(path[path.index(node) :])
        for n in path:
            state[n] = 2
    return [s for s in ctx.spans if s.span_id in on_cycle]


def _structure_findings(ctx: _Trace) -> list[_Finding]:
    out = []
    trace = {"type": "trace"}
    orphans = [s for s in ctx.spans if _role(s) == "orphan"]
    roots = [s for s in ctx.spans if s.parent_span_id is None]
    cycle = _cycle_members(ctx)
    if orphans:
        out.append(
            _Finding(
                "orphan_spans",
                trace,
                [
                    _fact("orphan_spans", len(orphans)),
                    _fact("missing_parent_span_ids", len({s.parent_span_id for s in orphans})),
                ],
                orphans,
                "parent_not_received",
            )
        )
    if len(roots) >= 2:
        out.append(
            _Finding(
                "multiple_root_spans",
                trace,
                [_fact("root_spans", len(roots))],
                roots,
                "several_entry_points",
            )
        )
    if not roots and ctx.spans and ctx.complete:  # absence: only on a complete analysis
        out.append(
            _Finding(
                "no_root_span",
                trace,
                [
                    _fact("spans_analyzed", len(ctx.spans)),
                    _fact("orphan_spans", len(orphans)),
                    _fact("spans_on_parent_cycles", len(cycle)),
                ],
                orphans + cycle,
                "root_not_received",
            )
        )
    if cycle:
        out.append(
            _Finding(
                "parent_cycle",
                trace,
                [_fact("spans_on_parent_cycles", len(cycle))],
                cycle,
                "client_assigned_inconsistent_ids",
            )
        )
    dropped = [s for s in ctx.spans if s.server_dropped_attributes_count > 0]
    if dropped:
        out.append(
            _Finding(
                "attributes_dropped_at_ingest",
                trace,
                [
                    _fact("spans_affected", len(dropped)),
                    _fact(
                        "attributes_dropped_total",
                        sum(s.server_dropped_attributes_count for s in dropped),
                    ),
                ],
                dropped,
                "attributes_not_allowlisted_or_invalid",
            )
        )
    return out


# ---------------------------------------------------------------- entry point
def _summary(ctx: _Trace, findings: list[_Finding]) -> dict[str, Any]:
    call_ids = {c for s in ctx.inference if (c := _call_id(s)) is not None}
    rows = [ctx.usage[s.span_id] for s in ctx.inference if s.span_id in ctx.usage]
    reasons = Counter(u.cost_unknown_reason for u in rows if u.cost_status == "unknown")
    return {
        "inference_attempts": len(ctx.inference),
        "failed_inference_attempts": sum(_is_error(s) for s in ctx.inference),
        "logical_calls": len(call_ids) if call_ids else None,
        "attempts_without_call_id": sum(_call_id(s) is None for s in ctx.inference),
        "error_spans": sum(_is_error(s) for s in ctx.spans),
        "usage_rows": len(rows),
        "cost_computed": sum(u.cost_status == "computed" for u in rows),
        "cost_unknown": {
            reason: reasons.get(reason, 0)
            for reason in ("price_missing", "usage_missing", "usage_inconsistent")
        },
        "findings_by_category": dict(sorted(Counter(f.category for f in findings).items())),
    }


def diagnose(
    trace: TraceInput,
    spans: Sequence[SpanInput],
    usage: Sequence[UsageInput],
    *,
    spans_truncated: bool,
    usage_truncated: bool,
) -> dict[str, Any]:
    """Diagnostics of one trace from its stored rows (the read API's bounded detail load)."""
    ctx = _Trace(trace, spans, usage, spans_truncated, usage_truncated)
    findings = sorted(
        [
            *_call_findings(ctx),
            *_error_span_findings(ctx),
            *_validation_findings(ctx),
            *_finish_reason_findings(ctx),
            *_usage_findings(ctx),
            *_structure_findings(ctx),
        ],
        key=_Finding.sort_key,
    )
    coverage_limits = {"stored_metadata_only", "export_may_be_incomplete"}
    if trace.provenance in catalog.PROVENANCE_LIMITATION:
        coverage_limits.add(catalog.PROVENANCE_LIMITATION[trace.provenance])
    if not ctx.complete:
        coverage_limits.add("analysis_truncated")
    return {
        "contract_version": catalog.CONTRACT_VERSION,
        "ruleset_version": catalog.RULESET_VERSION,
        "method": {
            "kind": "deterministic_rules",
            "language_model_used": False,
            "content_used": False,
            "inputs": ["traces", "spans", "usage_records"],
        },
        "trace": {
            "trace_id": trace.trace_id,
            "status": trace.status,
            "provenance": trace.provenance,
            "provenance_verified": False,
            "span_count": trace.span_count,
        },
        "coverage": {
            "complete": ctx.complete,
            "spans_stored": trace.span_count,
            "spans_analyzed": len(ctx.spans),
            "spans_truncated": spans_truncated,
            "usage_rows_analyzed": ctx.usage_rows,
            "usage_truncated": usage_truncated,
            "limitations": [
                {"code": c, "text": catalog.LIMITATIONS[c]} for c in sorted(coverage_limits)
            ],
        },
        "summary": _summary(ctx, findings),
        "findings": [f.render(ctx) for f in findings[:MAX_FINDINGS]],
        "findings_total": len(findings),
        "findings_truncated": len(findings) > MAX_FINDINGS,
        "not_assessed": [
            {"category": name, "reason": reason} for name, reason in catalog.NOT_ASSESSED
        ],
    }
