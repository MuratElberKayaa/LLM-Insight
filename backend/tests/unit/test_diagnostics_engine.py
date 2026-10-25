"""Phase 4A diagnostics engine (`llminsight.diagnostics.v1`): every rule has a positive case and
a counterexample that proves it does not fire without its evidence. All data is SYNTHETIC.

The engine is pure, so these tests build stored-row inputs directly; the same rules are
exercised through real ingest and PostgreSQL in tests/integration/test_diagnostics_api.py.
"""

import json
import random
import time
from typing import Any

import pytest

from app.services.diagnostics import SpanInput, TraceInput, UsageInput, catalog, diagnose
from app.services.diagnostics import engine as eng

T0 = 1_792_836_000_000_000_000
MS = 1_000_000
TRACE = "0af7651916cd43dd8448eb211c80319c"
CALL = "5f0c6e1a-3b7d-4c2e-9a41-000000000001"
CALL_2 = "5f0c6e1a-3b7d-4c2e-9a41-000000000002"


def sid(i: int) -> str:
    return f"{i + 1:016x}"


def span(
    i: int,
    parent: int | None = None,
    *,
    status: str = "UNSET",
    error_type: str | None = None,
    kind: str = "INTERNAL",
    parent_stored: bool | None = None,
    parent_id: str | None = None,
    provenance: str = "unknown",
    dropped: int = 0,
    attrs: Any = None,
    start: int | None = None,
) -> SpanInput:
    pid = parent_id if parent_id is not None else (sid(parent) if parent is not None else None)
    return SpanInput(
        span_id=sid(i),
        parent_span_id=pid,
        parent_stored=(pid is not None) if parent_stored is None else parent_stored,
        kind=kind,
        start_ns=T0 + i * MS if start is None else start,
        end_ns=(T0 + i * MS if start is None else start) + 10 * MS,
        status=status,
        error_type=error_type,
        attributes={} if attrs is None else attrs,
        provenance=provenance,
        server_dropped_attributes_count=dropped,
    )


def inference(
    i: int,
    parent: int | None = 0,
    *,
    call_id: str | None = CALL,
    attempt: Any = 1,
    provider: str = "openai",
    status: str = "UNSET",
    error_type: str | None = None,
    extra: dict[str, Any] | None = None,
    **kw: Any,
) -> SpanInput:
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": "gpt-4o-mini",
        "gen_ai.response.model": "gpt-4o-mini-2024-07-18",
        "llminsight.provenance": "synthetic",
    }
    if call_id is not None:
        attrs["llminsight.call_id"] = call_id
    if attempt is not None:
        attrs["llminsight.attempt"] = attempt
    if status != "ERROR":
        attrs["gen_ai.response.id"] = f"chatcmpl-SYNTHETIC-{i}"
    attrs.update(extra or {})
    return span(
        i,
        parent,
        status=status,
        error_type=error_type,
        kind="CLIENT",
        provenance=kw.pop("provenance", "synthetic"),
        attrs=attrs,
        **kw,
    )


def usage(
    s: SpanInput,
    *,
    tokens: tuple[int | None, int | None] = (100, 20),
    cost: str = "computed",
    reason: str | None = None,
    source: str | None = None,
    subsets: tuple[int | None, int | None, int | None] = (None, None, None),
) -> UsageInput:
    if tokens[0] is None or tokens[1] is None:
        source = source or "unknown"
    return UsageInput(
        span_id=s.span_id,
        usage_source=source or "provider_reported",
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cached_input_tokens=subsets[0],
        cache_write_input_tokens=subsets[1],
        reasoning_output_tokens=subsets[2],
        cost_status=cost,
        cost_unknown_reason=reason if cost == "unknown" else None,
    )


def failed_usage(s: SpanInput) -> UsageInput:
    return usage(s, tokens=(None, None), cost="unknown", reason="usage_missing")


def run(
    spans: list[SpanInput],
    rows: list[UsageInput] = (),  # type: ignore[assignment]
    *,
    status: str | None = None,
    provenance: str = "synthetic",
    span_count: int | None = None,
    spans_truncated: bool = False,
    usage_truncated: bool = False,
) -> dict[str, Any]:
    trace_status = status or ("error" if any(s.status == "ERROR" for s in spans) else "ok")
    trace = TraceInput(TRACE, trace_status, provenance, span_count or len(spans))
    return diagnose(
        trace,
        spans,
        list(rows),
        spans_truncated=spans_truncated,
        usage_truncated=usage_truncated,
    )


def cats(result: dict[str, Any]) -> list[str]:
    return [f["category"] for f in result["findings"]]


def only(result: dict[str, Any], category: str) -> dict[str, Any]:
    found = [f for f in result["findings"] if f["category"] == category]
    assert len(found) == 1, cats(result)
    return found[0]


def facts(finding: dict[str, Any]) -> dict[str, Any]:
    return {f["name"]: f["value"] for f in finding["observed_facts"]}


def evidence_ids(finding: dict[str, Any]) -> list[str]:
    return [e["span_id"] for e in finding["evidence"]]


def limitation_codes(finding: dict[str, Any]) -> set[str]:
    return {lim["code"] for lim in finding["limitations"]}


def root() -> SpanInput:
    return span(0, kind="SERVER")


def ok_call(i: int = 1, call_id: str = CALL) -> tuple[SpanInput, UsageInput]:
    s = inference(i, call_id=call_id)
    return s, usage(s)


# ---------------------------------------------------------------- empty and clean traces
def test_clean_trace_has_no_findings_and_explicit_unknown_counts():
    s, u = ok_call()
    r = run([root(), s], [u])
    assert r["findings"] == [] and r["findings_total"] == 0 and not r["findings_truncated"]
    assert r["summary"] == {
        "inference_attempts": 1,
        "failed_inference_attempts": 0,
        "logical_calls": 1,
        "attempts_without_call_id": 0,
        "error_spans": 0,
        "usage_rows": 1,
        "cost_computed": 1,
        "cost_unknown": {"price_missing": 0, "usage_missing": 0, "usage_inconsistent": 0},
        "findings_by_category": {},
    }
    assert r["coverage"]["complete"] is True


def test_trace_without_inference_has_null_logical_calls_and_no_findings():
    r = run([root()], provenance="unknown")
    assert r["findings"] == []
    assert r["summary"]["inference_attempts"] == 0
    assert r["summary"]["logical_calls"] is None  # unknown, not 0 (M03 "n/a")
    assert {lim["code"] for lim in r["coverage"]["limitations"]} == {
        "export_may_be_incomplete",
        "provenance_unknown",
        "stored_metadata_only",
    }


def test_result_header_states_method_versions_and_unverified_provenance():
    r = run([root()])
    assert r["contract_version"] == "llminsight.diagnostics.v1"
    assert r["ruleset_version"] == "llminsight.diagnostics.rules.v1"
    assert r["method"] == {
        "kind": "deterministic_rules",
        "language_model_used": False,
        "content_used": False,
        "inputs": ["traces", "spans", "usage_records"],
    }
    assert r["trace"]["provenance_verified"] is False
    assert [n["category"] for n in r["not_assessed"]] == [
        "elevated_latency",
        "semantic_quality",
        "prompt_quality",
        "provider_outage",
        "root_cause",
    ]


# ---------------------------------------------------------------- retries and failures
def test_failed_after_retries_lists_every_attempt_and_interprets_the_status_code():
    attempts = [inference(i, attempt=i, status="ERROR", error_type="500") for i in (1, 2, 3)]
    r = run([root(), *attempts], [failed_usage(a) for a in attempts])
    f = only(r, "inference_call_failed_after_retries")
    assert f["subject"] == {"type": "logical_call", "call_id": CALL}
    assert evidence_ids(f) == [a.span_id for a in attempts]
    assert facts(f) == {
        "attempts_observed": 3,
        "attempt_numbers": [1, 2, 3],
        "failed_attempts": 3,
        "error_types": ["500"],
        "error_types_count": 1,
        "failed_attempts_without_error_type": 0,
        "error_classes": ["http_5xx"],
        "attempts_duration_ns": str(3 * 10 * MS),
        "trace_status": "error",
    }
    assert f["hypothesis"]["code"] == "persistent_failure_across_attempts"
    assert f["hypothesis"]["error_interpretation"]["code"] == "server_error_status_recorded"
    assert f["hypothesis"]["verified"] is False
    assert f["data_complete"] is True
    assert "inference_call_failed" not in cats(r)
    assert "inference_call_recovered_after_retry" not in cats(r)


def test_single_failed_attempt_is_not_reported_as_retries():
    a = inference(1, status="ERROR", error_type="timeout")
    r = run([root(), a], [failed_usage(a)])
    f = only(r, "inference_call_failed")
    assert facts(f)["attempts_observed"] == 1
    assert f["hypothesis"]["error_interpretation"]["code"] == "timeout_recorded"
    assert "inference_call_failed_after_retries" not in cats(r)


def test_failed_attempt_without_call_id_cannot_be_grouped():
    a = inference(1, call_id=None, attempt=None, status="ERROR", error_type="429")
    r = run([root(), a], [failed_usage(a)])
    f = only(r, "inference_call_failed")
    assert f["subject"] == {"type": "span", "span_id": a.span_id}
    assert facts(f)["call_id_recorded"] is False
    assert "no_call_id" in limitation_codes(f)
    assert r["summary"]["logical_calls"] is None
    assert r["summary"]["attempts_without_call_id"] == 1


def test_recovered_call_stays_distinct_from_the_trace_level_error():
    a1 = inference(1, attempt=1, status="ERROR", error_type="429")
    a2 = inference(2, attempt=2)
    r = run([root(), a1, a2], [failed_usage(a1), usage(a2)])
    assert r["trace"]["status"] == "error"  # by contract: any ERROR span (R27)
    f = only(r, "inference_call_recovered_after_retry")
    assert facts(f)["final_attempt"] == 2
    assert facts(f)["final_attempt_status"] == "UNSET"
    assert facts(f)["trace_status"] == "error"
    assert facts(f)["other_error_spans"] == 0  # the trace error comes only from the retry
    assert f["hypothesis"]["error_interpretation"]["code"] == "rate_limit_status_recorded"
    assert cats(r) == ["inference_call_recovered_after_retry"]  # not a failed call


def test_recovered_call_with_a_failing_root_counts_the_other_error():
    a1 = inference(1, attempt=1, status="ERROR", error_type="429")
    a2 = inference(2, attempt=2)
    r = run([span(0, kind="SERVER", status="ERROR"), a1, a2], [failed_usage(a1), usage(a2)])
    assert facts(only(r, "inference_call_recovered_after_retry"))["other_error_spans"] == 1
    root_finding = only(r, "non_inference_span_error")
    # a recovered retry below the root still counts as a failed attempt in its subtree
    assert facts(root_finding)["failed_inference_attempts_in_subtree"] == 1


def test_successful_calls_produce_no_reliability_findings():
    a, ua = ok_call(1, CALL)
    b, ub = ok_call(2, CALL_2)
    r = run([root(), a, b], [ua, ub])
    assert not {c for c in cats(r) if catalog.CATEGORIES[c].group == "reliability"}


def test_failure_after_success_is_inconsistent_metadata_not_a_recovery():
    a1 = inference(1, attempt=1)
    a2 = inference(2, attempt=2, status="ERROR", error_type="500")
    r = run([root(), a1, a2], [usage(a1), failed_usage(a2)])
    assert "inference_call_recovered_after_retry" not in cats(r)
    assert "inference_call_failed_after_retries" not in cats(r)
    f = only(r, "attempt_metadata_inconsistent")
    assert facts(f)["conditions"] == ["failure_after_success"]
    assert f["data_complete"] is False


def test_mixed_and_unrecorded_error_types_derive_no_provider_cause():
    a1 = inference(1, attempt=1, status="ERROR", error_type="429")
    a2 = inference(2, attempt=2, status="ERROR", error_type=None)
    r = run([root(), a1, a2], [failed_usage(a1), failed_usage(a2)])
    f = only(r, "inference_call_failed_after_retries")
    assert facts(f)["error_classes"] == ["http_429", "not_recorded"]
    assert facts(f)["failed_attempts_without_error_type"] == 1
    assert f["hypothesis"]["error_interpretation"]["code"] == "mixed_error_classes_recorded"


@pytest.mark.parametrize(
    ("error_type", "cls", "code"),
    [
        ("429", "http_429", "rate_limit_status_recorded"),
        ("openai.RateLimitError", "http_429", "rate_limit_status_recorded"),
        ("503", "http_5xx", "server_error_status_recorded"),
        ("openai.InternalServerError", "http_5xx", "server_error_status_recorded"),
        ("401", "http_4xx", "client_error_status_recorded"),
        ("timeout", "timeout", "timeout_recorded"),
        ("openai.APITimeoutError", "timeout", "timeout_recorded"),
        ("openai.APIConnectionError", "connection_error", "connection_error_recorded"),
        ("_OTHER", "not_interpreted", "error_type_not_interpreted"),
        ("ValueError", "not_interpreted", "error_type_not_interpreted"),
        ("openai.APIError", "not_interpreted", "error_type_not_interpreted"),
        ("RateLimit", "not_interpreted", "error_type_not_interpreted"),  # no substring match
        ("4299", "not_interpreted", "error_type_not_interpreted"),
        ("600", "not_interpreted", "error_type_not_interpreted"),
        ("５００", "not_interpreted", "error_type_not_interpreted"),  # non-ASCII digits
        (None, "not_recorded", "error_type_not_recorded"),
    ],
)
def test_error_type_interpretation_is_exact_match_only(error_type, cls, code):
    assert eng.error_class(error_type) == cls
    a = inference(1, status="ERROR", error_type=error_type)
    f = only(run([root(), a], [failed_usage(a)]), "inference_call_failed")
    assert f["hypothesis"]["error_interpretation"]["code"] == code


def test_no_provider_explanation_text_for_uninterpreted_errors():
    a = inference(1, status="ERROR", error_type="_OTHER")
    f = only(run([root(), a], [failed_usage(a)]), "inference_call_failed")
    statement = f["hypothesis"]["error_interpretation"]["statement"]
    for claim in ("throttl", "rate", "internal failure", "deadline", "outage"):
        assert claim not in statement.lower()
    provider_steps = {s for i in catalog.INTERPRETATIONS.values() for s in i.steps}
    assert not provider_steps & set(f["investigation_steps"])


def test_outage_is_never_established():
    """A 5xx is interpreted as a server-side failure, explicitly not as a provider outage."""
    attempts = [inference(i, attempt=i, status="ERROR", error_type="503") for i in (1, 2, 3)]
    r = run([root(), *attempts], [failed_usage(a) for a in attempts])
    f = only(r, "inference_call_failed_after_retries")
    assert (
        "does not establish a provider outage"
        in f["hypothesis"]["error_interpretation"]["statement"]
    )
    assert all("outage" not in line for line in f["established"])
    assert "provider_outage" in {n["category"] for n in r["not_assessed"]}


# ---------------------------------------------------------------- retry metadata integrity
def test_attempt_gap_marks_the_call_incomplete():
    a1 = inference(1, attempt=1, status="ERROR", error_type="429")
    a3 = inference(3, attempt=3)
    r = run([root(), a1, a3], [failed_usage(a1), usage(a3)])
    gap = only(r, "retry_sequence_incomplete")
    assert facts(gap)["attempt_numbers_missing"] == [2]
    assert facts(gap)["attempt_numbers_missing_count"] == 1
    recovered = only(r, "inference_call_recovered_after_retry")
    assert recovered["data_complete"] is False
    assert "attempt_sequence_gap" in limitation_codes(recovered)


def test_contiguous_attempts_have_no_gap_finding():
    a1 = inference(1, attempt=1, status="ERROR", error_type="429")
    a2 = inference(2, attempt=2)
    assert "retry_sequence_incomplete" not in cats(
        run([root(), a1, a2], [failed_usage(a1), usage(a2)])
    )


def test_huge_attempt_number_is_bounded_work_and_output():
    a = inference(1, attempt=2**31 - 1)
    started = time.perf_counter()
    r = run([root(), a], [usage(a)])
    assert time.perf_counter() - started < 1.0
    gap = only(r, "retry_sequence_incomplete")
    assert facts(gap)["attempt_numbers_missing"] == list(range(1, eng.MAX_LIST + 1))
    assert facts(gap)["attempt_numbers_missing_count"] == 2**31 - 2


def test_duplicate_attempt_numbers_are_inconsistent():
    a = inference(1, attempt=1)
    b = inference(2, attempt=1)
    f = only(run([root(), a, b], [usage(a), usage(b)]), "attempt_metadata_inconsistent")
    assert facts(f)["conditions"] == ["duplicate_attempt_number"]
    assert facts(f)["duplicate_attempt_numbers"] == [1]


def test_attempt_number_without_call_id_is_inconsistent():
    a = inference(1, call_id=None, attempt=2)
    f = only(run([root(), a], [usage(a)]), "attempt_metadata_inconsistent")
    assert f["subject"] == {"type": "span", "span_id": a.span_id}
    assert facts(f)["conditions"] == ["attempt_without_call_id"]


def test_attempt_without_any_retry_metadata_is_not_inconsistent():
    a = inference(1, call_id=None, attempt=None)
    assert "attempt_metadata_inconsistent" not in cats(run([root(), a], [usage(a)]))


# ---------------------------------------------------------------- malformed client metadata
@pytest.mark.parametrize("bad_attempt", [True, "2", 0, -1, 2**31, 1.5, None, [1], {"n": 1}])
def test_malformed_attempt_numbers_never_count_as_attempts(bad_attempt):
    a = inference(1, attempt=bad_attempt)
    r = run([root(), a], [usage(a)])
    f = only(r, "attempt_metadata_inconsistent")
    assert facts(f)["conditions"] == ["attempt_number_missing_or_invalid"]
    assert facts(f)["attempts_without_usable_number"] == 1
    assert "retry_sequence_incomplete" not in cats(r)


@pytest.mark.parametrize("bad_call_id", [123, "", ["x"], {"a": 1}, "x" * 65, True])
def test_malformed_call_ids_are_flagged_and_not_grouped(bad_call_id):
    a = inference(1, call_id=None, attempt=None, status="ERROR", error_type="500")
    a.attributes["llminsight.call_id"] = bad_call_id
    r = run([root(), a], [failed_usage(a)])
    assert facts(only(r, "attempt_metadata_inconsistent"))["conditions"] == ["call_id_unusable"]
    assert only(r, "inference_call_failed")["subject"]["type"] == "span"


@pytest.mark.parametrize("bad_attrs", [None, [], "chat", 42, ["gen_ai.operation.name", "chat"]])
def test_non_object_attributes_are_treated_as_empty(bad_attrs):
    s = span(1, 0, kind="CLIENT", attrs=bad_attrs)
    r = run([root(), s])
    assert r["summary"]["inference_attempts"] == 0
    assert r["findings"] == []


@pytest.mark.parametrize(
    "op", [["chat"], {"chat": 1}, 7, "Chat", "chat ", "", "x" * 65, "completion"]
)
def test_unhashable_or_unknown_operation_names_are_not_inference(op):
    s = inference(1, extra={"gen_ai.operation.name": op})
    r = run([root(), s], [usage(s)])
    assert r["summary"]["inference_attempts"] == 0


@pytest.mark.parametrize(
    "reasons", ["length", ["length", 3], [["length"]], [{"r": "length"}], None, 7]
)
def test_malformed_finish_reasons_are_read_defensively(reasons):
    s = inference(1, extra={"gen_ai.response.finish_reasons": reasons})
    r = run([root(), s], [usage(s)])
    expected = ["inference_output_truncated"] if reasons == ["length", 3] else []
    assert cats(r) == expected  # only a list's valid string items count


# ---------------------------------------------------------------- non-inference errors
def test_root_error_above_a_failed_call_is_related_not_independent():
    a = inference(1, status="ERROR", error_type="500")
    r = run([span(0, kind="SERVER", status="ERROR", error_type="500"), a], [failed_usage(a)])
    f = only(r, "non_inference_span_error")
    assert f["hypothesis"]["code"] == "related_to_failed_inference_below"
    assert f["hypothesis"]["error_interpretation"] is None  # a server's own 500 is not a provider's
    assert evidence_ids(f) == [sid(0), a.span_id]


def test_root_error_without_failed_inference_points_outside_inference():
    s, u = ok_call()
    r = run(
        [span(0, kind="SERVER", status="ERROR", attrs={"http.response.status_code": 500}), s], [u]
    )
    f = only(r, "non_inference_span_error")
    assert f["hypothesis"]["code"] == "outside_instrumented_inference"
    assert facts(f)["http_response_status_code"] == 500
    assert facts(f)["failed_inference_attempts_in_subtree"] == 0


def test_ok_spans_produce_no_error_findings():
    assert "non_inference_span_error" not in cats(run([root(), span(1, 0)]))


# ---------------------------------------------------------------- application validation
def test_failed_application_validation_is_reported_as_declared():
    v = span(
        2,
        0,
        attrs={
            "llminsight.validation.rule": "json_schema.v1",
            "llminsight.validation.passed": False,
        },
    )
    f = only(run([root(), v]), "application_validation_failed")
    assert facts(f)["rule"] == "json_schema.v1"
    assert "client_declared_result" in limitation_codes(f)
    assert f["evidence"][0]["validation"] == {"rule": "json_schema.v1", "passed": False}


@pytest.mark.parametrize("passed", [True, "false", 0, None, "False"])
def test_validation_rule_does_not_fire_without_a_boolean_false(passed):
    v = span(
        2, 0, attrs={"llminsight.validation.rule": "r1", "llminsight.validation.passed": passed}
    )
    assert "application_validation_failed" not in cats(run([root(), v]))


# ---------------------------------------------------------------- provider finish reasons
def test_openai_length_and_content_filter_are_reported():
    a = inference(
        1,
        call_id=CALL,
        extra={"gen_ai.response.finish_reasons": ["length"], "gen_ai.request.max_tokens": 50},
    )
    b = inference(
        2, call_id=CALL_2, extra={"gen_ai.response.finish_reasons": ["stop", "content_filter"]}
    )
    r = run([root(), a, b], [usage(a), usage(b)])
    truncated = only(r, "inference_output_truncated")
    assert evidence_ids(truncated) == [a.span_id]
    assert facts(truncated) == {"attempts_affected": 1, "attempts_with_request_max_tokens": 1}
    assert evidence_ids(only(r, "inference_content_filtered")) == [b.span_id]


def test_finish_reasons_of_other_providers_or_stop_are_not_interpreted():
    a = inference(
        1,
        call_id=CALL,
        provider="llminsight.mock",
        extra={"gen_ai.response.finish_reasons": ["length"]},
    )
    b = inference(2, call_id=CALL_2, extra={"gen_ai.response.finish_reasons": ["stop"]})
    assert run([root(), a, b], [usage(a), usage(b)])["findings"] == []


# ---------------------------------------------------------------- usage and cost
def test_missing_tokens_on_a_successful_attempt_are_unknown_not_zero():
    s = inference(1)
    u = usage(s, tokens=(100, None), cost="unknown", reason="usage_missing")
    r = run([root(), s], [u])
    f = only(r, "token_usage_missing")
    assert facts(f) == {
        "attempts_affected": 1,
        "input_tokens_unknown": 0,
        "output_tokens_unknown": 1,
    }
    assert f["evidence"][0]["usage"]["output_tokens"] is None  # never 0
    assert "unknown_is_not_zero" in limitation_codes(f)
    assert r["summary"]["cost_unknown"]["usage_missing"] == 1


def test_missing_usage_on_failed_attempts_is_expected_and_not_a_finding():
    a = inference(1, status="ERROR", error_type="500")
    r = run([root(), a], [failed_usage(a)])
    assert "token_usage_missing" not in cats(r)
    assert r["summary"]["cost_unknown"]["usage_missing"] == 1  # still counted, never hidden


def test_embeddings_without_output_tokens_are_not_missing_usage():
    s = inference(1, extra={"gen_ai.operation.name": "embeddings"})
    assert "token_usage_missing" not in cats(
        run([root(), s], [usage(s, tokens=(30, None), cost="unknown", reason="usage_missing")])
    )
    t = inference(2, call_id=CALL_2, extra={"gen_ai.operation.name": "embeddings"})
    r = run([root(), t], [usage(t, tokens=(None, None), cost="unknown", reason="usage_missing")])
    assert facts(only(r, "token_usage_missing"))["input_tokens_unknown"] == 1


def test_price_missing_reports_models_tiers_and_endpoint():
    s = inference(
        1, extra={"openai.response.service_tier": "flex", "server.address": "api.openai.com"}
    )
    r = run([root(), s], [usage(s, cost="unknown", reason="price_missing")])
    f = only(r, "cost_unknown_price_missing")
    assert facts(f)["models"] == [
        {
            "provider": "openai",
            "requested_model": "gpt-4o-mini",
            "response_model": "gpt-4o-mini-2024-07-18",
        }
    ]
    assert facts(f)["service_tiers"] == ["flex"]
    assert facts(f)["server_addresses"] == ["api.openai.com"]
    assert "cost_usd" not in json.dumps(r)  # the engine never renders a cost, so never a 0


def test_computed_cost_produces_no_cost_finding():
    s, u = ok_call()
    assert "cost_unknown_price_missing" not in cats(run([root(), s], [u]))


def test_inconsistent_usage_reports_which_subset_exceeds_its_total():
    s = inference(1)
    u = usage(s, tokens=(10, 5), subsets=(8, 4, 6), cost="unknown", reason="usage_inconsistent")
    f = only(run([root(), s], [u]), "usage_inconsistent")
    assert facts(f) == {
        "attempts_affected": 1,
        "input_subsets_exceed_input": 1,
        "reasoning_exceeds_output": 1,
    }


def test_inference_span_without_usage_row_points_to_its_duplicate_identity():
    a = inference(1, call_id=CALL)
    b = inference(
        2, call_id=CALL_2, extra={"gen_ai.response.id": a.attributes["gen_ai.response.id"]}
    )
    r = run([root(), a, b], [usage(a)])
    f = only(r, "usage_ledger_row_missing")
    assert evidence_ids(f) == [b.span_id]
    assert facts(f) == {"inference_spans_without_usage_row": 1, "duplicate_identity_in_trace": 1}


def test_usage_row_rule_is_suppressed_when_usage_rows_were_truncated():
    a = inference(1)
    r = run([root(), a], [], usage_truncated=True)
    assert "usage_ledger_row_missing" not in cats(r)
    assert r["coverage"]["complete"] is False


# ---------------------------------------------------------------- trace structure
def test_orphans_and_multiple_roots():
    orphan = span(2, parent_id="00000000000000ff", parent_stored=False)
    r = run([root(), span(1), orphan])
    f = only(r, "orphan_spans")
    assert evidence_ids(f) == [orphan.span_id]
    assert f["evidence"][0]["role"] == "orphan"
    assert f["evidence"][0]["parent_span_id"] == "00000000000000ff"
    assert facts(only(r, "multiple_root_spans")) == {"root_spans": 2}


def test_single_root_tree_has_no_structure_findings():
    assert run([root(), span(1, 0), span(2, 1)])["findings"] == []


def test_no_root_is_reported_only_when_the_whole_trace_was_analysed():
    orphans = [span(i, parent_id="00000000000000ff", parent_stored=False) for i in (1, 2)]
    assert "no_root_span" in cats(run(orphans))
    truncated = run(orphans, span_count=5, spans_truncated=True)
    assert "no_root_span" not in cats(truncated)  # the root may be among the unread spans
    assert "orphan_spans" in cats(truncated)  # presence-based rules still fire


def test_parent_cycle_is_detected_without_looping():
    a = span(1, parent_id=sid(2), parent_stored=True)
    b = span(2, parent_id=sid(1), parent_stored=True)
    r = run([root(), a, b, span(3, 2)])
    f = only(r, "parent_cycle")
    assert evidence_ids(f) == [a.span_id, b.span_id]  # the child of the cycle is not on it


def test_dropped_attributes_finding_and_evidence_limitation():
    a = inference(1, status="ERROR", error_type="500", dropped=2)
    r = run([root(), a], [failed_usage(a)])
    assert facts(only(r, "attributes_dropped_at_ingest")) == {
        "spans_affected": 1,
        "attributes_dropped_total": 2,
    }
    failed = only(r, "inference_call_failed")
    assert "evidence_attributes_dropped" in limitation_codes(failed)
    assert failed["data_complete"] is False


def test_no_dropped_attributes_no_finding():
    s, u = ok_call()
    assert "attributes_dropped_at_ingest" not in cats(run([root(), s], [u]))


# ---------------------------------------------------------------- coverage and bounds
def test_truncated_analysis_marks_every_finding_incomplete():
    attempts = [inference(i, attempt=i, status="ERROR", error_type="500") for i in (1, 2)]
    r = run(
        [root(), *attempts],
        [failed_usage(a) for a in attempts],
        span_count=3000,
        spans_truncated=True,
    )
    coverage = r["coverage"]
    assert coverage["complete"] is False and coverage["spans_truncated"] is True
    assert (coverage["spans_stored"], coverage["spans_analyzed"]) == (3000, 3)
    assert r["findings"]
    for f in r["findings"]:
        assert f["data_complete"] is False
        assert "analysis_truncated" in limitation_codes(f)


def test_findings_and_evidence_are_bounded_and_counted():
    failing = [
        inference(i, call_id=None, attempt=None, status="ERROR", error_type="500")
        for i in range(1, 151)
    ]
    r = run([root(), *failing], [failed_usage(a) for a in failing])
    assert len(r["findings"]) == eng.MAX_FINDINGS
    assert r["findings_total"] == 150 and r["findings_truncated"] is True
    assert r["summary"]["findings_by_category"] == {"inference_call_failed": 150}
    orphans = [span(i, parent_id="00000000000000ff", parent_stored=False) for i in range(1, 41)]
    f = only(run([root(), *orphans]), "orphan_spans")
    assert len(f["evidence"]) == eng.MAX_EVIDENCE
    assert f["evidence_total"] == 40 and f["evidence_truncated"] is True


def test_no_finding_without_evidence_on_a_mixed_trace():
    r = run(*_kitchen_sink())
    assert len(set(cats(r))) >= 12
    for f in r["findings"]:
        assert f["evidence_total"] >= 1 and f["evidence"]
        assert f["observed_facts"]
        assert f["category"] in catalog.CATEGORIES


# ---------------------------------------------------------------- determinism
def _kitchen_sink() -> tuple[list[SpanInput], list[UsageInput]]:
    r1 = inference(1, attempt=1, status="ERROR", error_type="429")
    r2 = inference(2, attempt=2)
    f1, f2 = (
        inference(i, call_id=CALL_2, attempt=i - 2, status="ERROR", error_type="500")
        for i in (3, 4)
    )
    lone = inference(5, call_id=None, attempt=None, status="ERROR", error_type="_OTHER", dropped=1)
    priced = inference(
        6,
        call_id="5f0c6e1a-3b7d-4c2e-9a41-000000000003",
        extra={"gen_ai.response.finish_reasons": ["length"]},
    )
    nousage = inference(7, call_id="5f0c6e1a-3b7d-4c2e-9a41-000000000004")
    gap = inference(8, call_id="5f0c6e1a-3b7d-4c2e-9a41-000000000005", attempt=3)
    validation = span(9, 0, attrs={"llminsight.validation.passed": False})
    orphan = span(10, parent_id="00000000000000ff", parent_stored=False)
    second_root = span(11)
    spans = [
        span(0, kind="SERVER", status="ERROR"),
        r1,
        r2,
        f1,
        f2,
        lone,
        priced,
        nousage,
        gap,
        validation,
        orphan,
        second_root,
    ]
    rows = [
        failed_usage(r1),
        usage(r2),
        failed_usage(f1),
        failed_usage(f2),
        failed_usage(lone),
        usage(priced, cost="unknown", reason="price_missing"),
        usage(gap, tokens=(5, None), cost="unknown", reason="usage_missing"),
    ]
    return spans, rows


def test_output_is_identical_for_any_input_order_and_repeated_runs():
    spans, rows = _kitchen_sink()
    expected = json.dumps(run(spans, rows), sort_keys=False)
    rng = random.Random(4)  # noqa: S311 - a fixed seed for reproducible shuffles
    for _ in range(25):
        s, u = spans[:], rows[:]
        rng.shuffle(s)
        rng.shuffle(u)
        assert json.dumps(run(s, u), sort_keys=False) == expected


def test_findings_follow_catalogue_order_then_subject():
    r = run(*_kitchen_sink())
    order = [catalog.CATEGORY_ORDER[c] for c in cats(r)]
    assert order == sorted(order)
    failed = [f["id"] for f in r["findings"] if f["category"] == "inference_call_failed"]
    assert failed == [f"inference_call_failed:span:{sid(5)}"]


def test_engine_does_not_mutate_its_inputs():
    spans, rows = _kitchen_sink()
    before = json.dumps([s.attributes for s in spans])
    run(spans, rows)
    assert json.dumps([s.attributes for s in spans]) == before


# ---------------------------------------------------------------- privacy and wording
FAKE_KEY = "sk-" + "SENTINEL" + "0" * 22  # built at runtime: no key-shaped literal in source
SENTINELS = ("PROMPT-SENTINEL-7f3a", FAKE_KEY, "user@example.com")


def test_unlisted_attribute_values_never_reach_the_output():
    """Even if content had reached storage, only allowlisted fields are echoed."""
    hostile = {
        "gen_ai.input.messages": SENTINELS[0],
        "gen_ai.output.messages": [SENTINELS[0]],
        "gen_ai.system_instructions": SENTINELS[0],
        "exception.message": SENTINELS[1],
        "enduser.id": SENTINELS[2],
        "gen_ai.request.model": "gpt-4o-mini",
    }
    a = inference(1, status="ERROR", error_type="500", extra=hostile)
    r = run([span(0, kind="SERVER", status="ERROR", attrs=hostile), a], [failed_usage(a)])
    text = json.dumps(r)
    assert r["findings"]
    for sentinel in SENTINELS:
        assert sentinel not in text


def _texts(result: dict[str, Any]) -> list[str]:
    out = [lim["text"] for lim in result["coverage"]["limitations"]]
    out += [n["reason"] for n in result["not_assessed"]]
    for f in result["findings"]:
        out += [
            f["trigger"],
            f["hypothesis"]["statement"],
            *f["established"],
            *f["not_established"],
        ]
        out += [lim["text"] for lim in f["limitations"]] + f["investigation_steps"]
        if f["hypothesis"]["error_interpretation"]:
            out.append(f["hypothesis"]["error_interpretation"]["statement"])
    return out


def _catalogue_texts() -> set[str]:
    out = set(catalog.LIMITATIONS.values()) | {reason for _, reason in catalog.NOT_ASSESSED}
    for c in catalog.CATEGORIES.values():
        out |= {c.trigger, *c.established, *c.not_established, *c.hypotheses.values(), *c.steps}
    for i in catalog.INTERPRETATIONS.values():
        out |= {i.statement, *i.steps}
    return out


def test_every_sentence_is_a_catalogue_constant():
    """No stored value is interpolated into prose: client metadata cannot shape the text."""
    spans, rows = _kitchen_sink()
    allowed = _catalogue_texts()
    assert set(_texts(run(spans, rows))) <= allowed


def test_hypotheses_are_unverified_and_carry_no_scores():
    r = run(*_kitchen_sink())
    for f in r["findings"]:
        h = f["hypothesis"]
        assert h["verified"] is False
        assert h["statement"].startswith("Possible explanation (not verified)")
        assert set(h) == {"code", "statement", "error_interpretation", "verified"}
    flat = json.dumps(r).lower()
    for word in ("confidence", "probability", "score", "root cause:"):
        assert word not in flat


def test_wording_never_implies_a_model_reasoned():
    for text in _catalogue_texts():
        lowered = text.lower()
        for phrase in (
            "i think",
            "we believe",
            "the ai",
            "llm determined",
            "analysed by",
            "likely",
        ):
            assert phrase not in lowered, text


def test_no_semantic_quality_category_exists_without_evaluation_data():
    semantic = {"hallucination", "semantic", "prompt_quality", "relevance", "correctness"}
    assert not any(word in name for name in catalog.CATEGORIES for word in semantic)
    assert {"semantic_quality", "prompt_quality"} <= {n for n, _ in catalog.NOT_ASSESSED}


def test_synthetic_provenance_is_never_presented_as_real():
    a = inference(1, status="ERROR", error_type="500")
    r = run([root(), a], [failed_usage(a)], provenance="synthetic")
    assert "synthetic_telemetry" in {lim["code"] for lim in r["coverage"]["limitations"]}
    f = only(r, "inference_call_failed")
    assert "synthetic_telemetry" in limitation_codes(f)
    assert "provenance_reported_real" not in limitation_codes(f)
    assert all(e["provenance_verified"] is False for e in f["evidence"])


def test_reported_real_is_labelled_unverified():
    a = inference(1, status="ERROR", error_type="500", provenance="real")
    f = only(run([root(), a], [failed_usage(a)], provenance="real"), "inference_call_failed")
    assert "provenance_reported_real" in limitation_codes(f)


# ---------------------------------------------------------------- catalogue integrity
def test_catalogue_is_complete_and_consistent():
    for name, c in catalog.CATEGORIES.items():
        assert c.hypotheses and c.steps and c.established and c.not_established, name
        assert set(c.limitations) <= set(catalog.LIMITATIONS), name
        assert c.group in {
            "reliability",
            "application_signal",
            "provider_signal",
            "usage_and_cost",
            "trace_structure",
            "metadata_integrity",
        }
        for statement in c.hypotheses.values():
            assert statement.startswith("Possible explanation (not verified)"), name
    assert set(catalog.INTERPRETATIONS) == set(catalog.INTERPRETATION_CODES)
    assert set(catalog.ERROR_TYPE_CLASSES.values()) <= set(catalog.INTERPRETATIONS)
    assert set(catalog.PROVENANCE_LIMITATION.values()) <= set(catalog.LIMITATIONS)
