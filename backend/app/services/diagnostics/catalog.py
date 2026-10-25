"""Rule catalogue of `llminsight.diagnostics.v1` (ARCHITECTURE.md §5.6, reports/phase-4a.md).

Every text here is a constant. The engine selects texts by code and never interpolates a
stored value into them: span names, model names, error types and other client-supplied
metadata appear only as structured facts and evidence fields, never inside a sentence.

Vocabulary rules (Phase 4A instruction, ADR-10):
- an `established` line restates what the stored rows show, nothing more;
- a hypothesis always starts with "Possible explanation (not verified)" and is never a
  root cause;
- there are no confidence scores, and no text implies that a model reasoned about the data
  (the engine is a fixed rule set; no language model is used).
"""

from dataclasses import dataclass

CONTRACT_VERSION = "llminsight.diagnostics.v1"
RULESET_VERSION = "llminsight.diagnostics.rules.v1"


@dataclass(frozen=True)
class Category:
    group: str
    trigger: str
    established: tuple[str, ...]
    not_established: tuple[str, ...]
    hypotheses: dict[str, str]  # code -> statement
    steps: tuple[str, ...]
    limitations: tuple[str, ...] = ()  # limitation codes always attached


@dataclass(frozen=True)
class Interpretation:
    statement: str
    steps: tuple[str, ...] = ()


_NOT_CAUSE = "The cause of the failure(s)."
_LATER_ATTEMPT = "Whether a later attempt succeeded but has not been exported or stored."
_OTHER_REQUESTS = "Whether other requests or other projects were affected at the same time."
_NO_CONTENT = "Whether the output was correct or complete: this platform stores no content."

# Catalogue order is the output order of findings (then by subject).
CATEGORIES: dict[str, Category] = {
    "inference_call_failed_after_retries": Category(
        group="reliability",
        trigger=(
            "Two or more stored inference attempts share one llminsight.call_id and every one"
            " of them has span status ERROR."
        ),
        established=(
            "Every stored attempt of this logical call has span status ERROR.",
            "The attempts are grouped by the llminsight.call_id recorded by the instrumentation.",
        ),
        not_established=(
            _NOT_CAUSE,
            "Whether the retry budget was exhausted: the configured maximum number of"
            " attempts is not recorded.",
            _LATER_ATTEMPT,
            _OTHER_REQUESTS,
        ),
        hypotheses={
            "persistent_failure_across_attempts": (
                "Possible explanation (not verified): the condition that made the first"
                " attempt fail persisted across the retried attempts of this call."
            ),
        },
        steps=(
            "Compare the recorded error.type of each attempt in the evidence.",
            "Check whether other traces in the same time window show failed attempts for the"
            " same provider and model (Trace Explorer, status filter).",
            "Check the application's retry configuration (maximum attempts, backoff); it is"
            " not recorded in telemetry.",
        ),
        limitations=(
            "call_id_client_asserted",
            "error_type_client_classified",
            "export_may_be_incomplete",
        ),
    ),
    "inference_call_failed": Category(
        group="reliability",
        trigger=(
            "Exactly one stored inference attempt of a logical call has span status ERROR and"
            " it is the call's only stored attempt, or an inference attempt without a usable"
            " llminsight.call_id has span status ERROR."
        ),
        established=("The stored attempt has span status ERROR.",),
        not_established=(
            _NOT_CAUSE,
            "Whether the application retried the call: retries are recognisable only"
            " through llminsight.call_id and llminsight.attempt.",
            _LATER_ATTEMPT,
            _OTHER_REQUESTS,
        ),
        hypotheses={
            "failed_single_attempt": (
                "Possible explanation (not verified): the call failed on its only stored"
                " attempt and was not retried, or its retries are not stored."
            ),
        },
        steps=(
            "Read the recorded error.type of the attempt in the evidence.",
            "Check whether other traces in the same time window show failed attempts for the"
            " same provider and model (Trace Explorer, status filter).",
            "Check whether the application retries this call; retries are not visible"
            " without llminsight.call_id.",
        ),
        limitations=("error_type_client_classified", "export_may_be_incomplete"),
    ),
    "inference_call_recovered_after_retry": Category(
        group="reliability",
        trigger=(
            "A logical call (one llminsight.call_id) has at least one inference attempt with"
            " span status ERROR, and its highest-numbered stored attempt does not have span"
            " status ERROR."
        ),
        established=(
            "At least one attempt of this logical call failed and a later attempt completed"
            " without span status ERROR.",
            "A trace has status error whenever any stored span has status ERROR"
            " (ARCHITECTURE.md §4.4), so a recovered call alone makes the trace status"
            " error (R27).",
        ),
        not_established=(
            _NOT_CAUSE,
            "Whether the output of the successful attempt was correct or complete: this"
            " platform stores no content.",
            "What the application returned to its caller (see the root span status).",
        ),
        hypotheses={
            "transient_failure_recovered": (
                "Possible explanation (not verified): the condition behind the failed"
                " attempt(s) was transient and had cleared by the retried attempt."
            ),
        },
        steps=(
            "Read the recorded error.type of the failed attempt(s) in the evidence.",
            "Check the fact other_error_spans: when it is 0, the trace status error comes"
            " only from attempts that were later retried successfully.",
            "Track how often this call needs retries over time (Overview: inference attempts"
            " versus logical calls, inference error rate).",
        ),
        limitations=("call_id_client_asserted", "error_type_client_classified"),
    ),
    "non_inference_span_error": Category(
        group="reliability",
        trigger=(
            "A span that is not an inference span (no inference gen_ai.operation.name) has"
            " span status ERROR."
        ),
        established=(
            "This span has span status ERROR.",
            "It is not an inference attempt (detection by gen_ai.operation.name, ADR-13).",
        ),
        not_established=(
            _NOT_CAUSE,
            "Whether a failed inference attempt caused it: stored parent links are not"
            " causal links.",
            "The error description: OTLP status messages are never stored (R21).",
        ),
        hypotheses={
            "related_to_failed_inference_below": (
                "Possible explanation (not verified): this span failed because an inference"
                " attempt in its subtree failed."
            ),
            "outside_instrumented_inference": (
                "Possible explanation (not verified): the failure arose in application code"
                " or another dependency, not in an instrumented model call; no stored"
                " inference attempt in this span's subtree failed."
            ),
        },
        steps=(
            "Inspect the span and its subtree in the trace waterfall.",
            "Check the application's own logs for this trace ID; error descriptions are not"
            " stored here.",
            "If the subtree has failed inference attempts, review their findings first.",
        ),
        limitations=("error_description_not_stored", "subtree_from_stored_links"),
    ),
    "application_validation_failed": Category(
        group="application_signal",
        trigger="A span records llminsight.validation.passed as false.",
        established=(
            "The instrumented application recorded a failed validation result on this span.",
        ),
        not_established=(
            "What the check verifies: only its rule identifier is recorded.",
            "Whether the model output was semantically wrong: this platform stores no"
            " content and did not run the check.",
        ),
        hypotheses={
            "application_check_failed": (
                "Possible explanation (not verified): the output of this request did not"
                " satisfy the application's own check named in llminsight.validation.rule."
            ),
        },
        steps=(
            "Look up the rule identifier in the application code.",
            "Reproduce with a safe, synthetic input and a deterministic golden-set check"
            " before drawing conclusions about output quality.",
        ),
        limitations=("client_declared_result",),
    ),
    "inference_output_truncated": Category(
        group="provider_signal",
        trigger=(
            "An inference attempt with gen_ai.provider.name openai records the finish reason"
            " length."
        ),
        established=(
            "The instrumentation recorded the OpenAI finish reason length for these attempts.",
        ),
        not_established=(
            "Whether the incomplete output mattered to the application.",
            _NO_CONTENT,
        ),
        hypotheses={
            "output_cut_at_token_limit": (
                "Possible explanation (not verified): generation stopped at a token limit"
                " (the request's max_tokens or the model's context limit), so the output of"
                " these attempts may be incomplete."
            ),
        },
        steps=(
            "Compare the reported output tokens with gen_ai.request.max_tokens where it is"
            " recorded.",
            "Check the application's max_tokens setting for this prompt version.",
        ),
        limitations=("finish_reasons_openai_vocabulary",),
    ),
    "inference_content_filtered": Category(
        group="provider_signal",
        trigger=(
            "An inference attempt with gen_ai.provider.name openai records the finish reason"
            " content_filter."
        ),
        established=(
            "The instrumentation recorded the OpenAI finish reason content_filter for these"
            " attempts.",
        ),
        not_established=(
            "Which content triggered the filter, or whether it was in the input or the"
            " output: no content is stored.",
            "Whether the filter decision was correct.",
        ),
        hypotheses={
            "provider_content_filter_applied": (
                "Possible explanation (not verified): the provider's content filtering"
                " omitted or stopped part of the output of these attempts."
            ),
        },
        steps=(
            "Reproduce with a safe, synthetic input to see whether the filter triggers"
            " consistently.",
            "Review the provider's content-filtering documentation for the recorded model.",
        ),
        limitations=("finish_reasons_openai_vocabulary",),
    ),
    "token_usage_missing": Category(
        group="usage_and_cost",
        trigger=(
            "An inference attempt without span status ERROR has a usage row whose"
            " input_tokens, or whose output_tokens (except for embeddings), is NULL."
        ),
        established=(
            "Token counts of these attempts are unknown (NULL), not zero.",
            "Their cost is unknown and excluded from known-cost totals.",
        ),
        not_established=(
            "How many tokens these attempts actually used.",
            "Whether the provider returned usage that the instrumentation did not map.",
        ),
        hypotheses={
            "usage_not_reported_by_instrumentation": (
                "Possible explanation (not verified): the provider response carried no usage"
                " block, or the instrumentation did not map it to gen_ai.usage.*."
            ),
        },
        steps=(
            "Check whether the instrumentation emits gen_ai.usage.input_tokens and"
            " gen_ai.usage.output_tokens on successful attempts.",
            "If server_dropped_attributes_count is above 0 on these spans, check for usage"
            " attributes sent with the wrong type (TELEMETRY_CONTRACT.md §3).",
        ),
        limitations=("unknown_is_not_zero",),
    ),
    "usage_ledger_row_missing": Category(
        group="usage_and_cost",
        trigger="An inference span of this trace has no usage_records row.",
        established=(
            "No usage row is stored for these inference spans, so they add no tokens or cost"
            " to this trace.",
        ),
        not_established=(
            "Which other span holds the matching usage row when it is outside this trace.",
        ),
        hypotheses={
            "call_identity_already_recorded": (
                "Possible explanation (not verified): the span's call identity (provider"
                " response ID, or SDK call ID and attempt) matched a usage row already stored"
                " in this project, so the call is counted once, on that other span (for"
                " example double instrumentation or a re-used identifier)."
            ),
        },
        steps=(
            "Look for another span with the same provider response ID; the fact"
            " duplicate_identity_in_trace counts those inside this trace.",
            "Check whether two instrumentations report the same model call.",
        ),
        limitations=("single_trace_scope",),
    ),
    "usage_inconsistent": Category(
        group="usage_and_cost",
        trigger="A usage row has the cost_unknown_reason usage_inconsistent.",
        established=(
            "The ingest marked the reported token subsets as exceeding their totals (cached"
            " plus cache-write input above input, or reasoning output above output); the"
            " cost of these attempts is unknown, not zero.",
        ),
        not_established=("Which of the reported numbers is wrong.",),
        hypotheses={
            "instrumentation_reported_inconsistent_usage": (
                "Possible explanation (not verified): the instrumentation mapped token"
                " subsets inconsistently (for example a subset counted against the wrong"
                " total)."
            ),
        },
        steps=(
            "Compare the token fields in the evidence with the provider's usage object for"
            " one reproduced call.",
            "Check the instrumentation's mapping against the token rules of"
            " TELEMETRY_CONTRACT.md §4.",
        ),
        limitations=("unknown_is_not_zero",),
    ),
    "cost_unknown_price_missing": Category(
        group="usage_and_cost",
        trigger="A usage row has the cost_unknown_reason price_missing.",
        established=(
            "The cost of these attempts is unknown (NULL), not zero, and excluded from"
            " known-cost totals.",
            "No stored price was applied to them at ingest.",
        ),
        not_established=("What these attempts actually cost.",),
        hypotheses={
            "no_matching_stored_price": (
                "Possible explanation (not verified): no stored price row matched the"
                " attempt's provider and exact model at its start time, or the attempt used a"
                " service tier or endpoint the stored prices do not cover (PRICING.md)."
            ),
        },
        steps=(
            "Compare the recorded response model with the model_prices catalogue.",
            "Check the facts service_tiers and server_addresses: only the standard tier on"
            " api.openai.com is priced.",
            "Stored costs are never recomputed: a later catalogue does not change these rows.",
        ),
        limitations=("unknown_is_not_zero",),
    ),
    "orphan_spans": Category(
        group="trace_structure",
        trigger=("A stored span references a parent span ID that is not stored for this trace."),
        established=("These spans reference parent spans that are not stored.",),
        not_established=("Why the parent spans are missing.",),
        hypotheses={
            "parent_not_received": (
                "Possible explanation (not verified): the parent span was not exported yet,"
                " was dropped by its exporter, or was in a batch the ingest rejected."
            ),
        },
        steps=(
            "Re-open the trace later: a parent exported in a later batch attaches automatically.",
            "Check the exporter of the parent's service for export failures (exporters fail"
            " open and drop spans without telling the application).",
        ),
        limitations=("export_may_be_incomplete",),
    ),
    "multiple_root_spans": Category(
        group="trace_structure",
        trigger="Two or more stored spans of this trace have no parent span ID.",
        established=("More than one span of this trace has no parent.",),
        not_established=("Which root represents the request that started the trace.",),
        hypotheses={
            "several_entry_points": (
                "Possible explanation (not verified): more than one component started a span"
                " without a parent while sharing this trace ID (for example instrumentation"
                " that set the trace context but not the parent)."
            ),
        },
        steps=(
            "Inspect the roots and their services in the trace waterfall.",
            "Check context propagation between the services involved.",
        ),
    ),
    "no_root_span": Category(
        group="trace_structure",
        trigger=(
            "Every stored span of this trace was analysed and none has an empty parent span ID."
        ),
        established=(
            "No stored span of this trace is a root, so trace latency (M07) excludes this trace.",
        ),
        not_established=("Which span the trace started with.",),
        hypotheses={
            "root_not_received": (
                "Possible explanation (not verified): the root span was not exported yet, was"
                " dropped by its exporter, or was in a batch the ingest rejected."
            ),
        },
        steps=(
            "Re-open the trace later.",
            "Check the exporter of the service that starts the request.",
        ),
        limitations=("export_may_be_incomplete",),
    ),
    "parent_cycle": Category(
        group="trace_structure",
        trigger=(
            "Following stored parent span IDs among the analysed spans returns to a span"
            " already visited."
        ),
        established=(
            "The stored parent links of these spans form a cycle, which one request cannot"
            " produce.",
        ),
        not_established=("Which of the links is wrong.",),
        hypotheses={
            "client_assigned_inconsistent_ids": (
                "Possible explanation (not verified): an instrumentation assigned span or"
                " parent IDs incorrectly (for example re-used span IDs)."
            ),
        },
        steps=("Check how the instrumentation generates span IDs and sets parents.",),
    ),
    "retry_sequence_incomplete": Category(
        group="metadata_integrity",
        trigger=(
            "The stored attempt numbers of a logical call do not form a gap-free sequence"
            " starting at 1."
        ),
        established=("Attempt numbers are missing from the stored attempts of this logical call.",),
        not_established=("Whether the missing attempts happened.",),
        hypotheses={
            "attempts_not_stored": (
                "Possible explanation (not verified): the missing attempts were not exported"
                " yet, were dropped by the exporter, or were in a batch the ingest rejected."
            ),
        },
        steps=(
            "Re-open the trace later.",
            "Check exporter health of the calling service at the time of the call.",
        ),
        limitations=("call_id_client_asserted", "export_may_be_incomplete"),
    ),
    "attempt_metadata_inconsistent": Category(
        group="metadata_integrity",
        trigger=(
            "Attempt metadata contradicts the LLM Insight SDK retry model: a repeated"
            " attempt number in one call, an attempt without a usable number, a failure"
            " after an earlier attempt of the same call succeeded, an attempt number"
            " without a call ID, or an unusable call ID."
        ),
        established=(
            "The recorded llminsight.call_id and llminsight.attempt values of these spans do"
            " not describe one logical call retried in order.",
        ),
        not_established=(
            "Which recorded value is wrong.",
            "How far the retry counts of this trace (attempts versus logical calls) are affected.",
        ),
        hypotheses={
            "call_metadata_not_sdk_shaped": (
                "Possible explanation (not verified): the instrumentation re-used a call ID"
                " across distinct calls, numbers attempts differently, or a value was"
                " dropped at ingest."
            ),
        },
        steps=(
            "Check how the instrumentation generates llminsight.call_id (one UUID per"
            " logical call) and llminsight.attempt (1, 2, ...).",
            "Check server_dropped_attributes_count on these spans.",
        ),
        limitations=("call_id_client_asserted",),
    ),
    "attributes_dropped_at_ingest": Category(
        group="metadata_integrity",
        trigger="A span has server_dropped_attributes_count above 0.",
        established=(
            "The ingest dropped attributes of these spans before storage; the dropped values"
            " are not stored anywhere.",
        ),
        not_established=("Which attributes were dropped: only their number is stored.",),
        hypotheses={
            "attributes_not_allowlisted_or_invalid": (
                "Possible explanation (not verified): the instrumentation sent attributes"
                " outside the allowlist, content or credential keys that are always denied,"
                " values of the wrong type or format, or aliases shadowed by their standard"
                " name (TELEMETRY_CONTRACT.md §3). A field reported here as not recorded may"
                " have been sent and dropped."
            ),
        },
        steps=(
            "Compare the instrumentation's attributes with the allowlist in"
            " TELEMETRY_CONTRACT.md §3.",
            "Never put prompt or response content into span attributes: it is dropped by"
            " design (ADR-19).",
        ),
    ),
}

CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORIES)}

LIMITATIONS: dict[str, str] = {
    "analysis_truncated": (
        "Only the first 2000 spans (by start time) and 2000 usage rows of this trace were"
        " analysed; findings that depend on the rest may be missing or incomplete."
    ),
    "attempt_sequence_gap": (
        "Attempt numbers of this call are missing from storage; its attempt history is incomplete."
    ),
    "call_id_client_asserted": (
        "Attempts are grouped by llminsight.call_id and llminsight.attempt as asserted by the"
        " instrumentation; the server cannot verify them."
    ),
    "client_declared_result": (
        "The validation result is declared by the application; this platform did not run or"
        " re-check it."
    ),
    "error_description_not_stored": (
        "Error descriptions (OTLP status messages, exception messages) are never stored; only"
        " the span status and the error.type identifier are available."
    ),
    "error_type_client_classified": (
        "error.type is a low-cardinality identifier chosen by the instrumentation; it is not"
        " verified against the provider."
    ),
    "evidence_attributes_dropped": (
        "The ingest dropped attributes of at least one evidence span"
        " (server_dropped_attributes_count above 0); a field reported as not recorded may"
        " have been sent and dropped."
    ),
    "export_may_be_incomplete": (
        "Only spans stored at the time of this request are analysed; spans exported later,"
        " dropped by an exporter or rejected by the ingest are not visible."
    ),
    "finish_reasons_openai_vocabulary": (
        "Only the OpenAI finish reasons length and content_filter on attempts with"
        " gen_ai.provider.name openai are interpreted; other providers' values are not."
    ),
    "no_call_id": (
        "The attempt has no usable llminsight.call_id, so it cannot be grouped with retries"
        " of the same call."
    ),
    "provenance_mixed": (
        "The trace mixes telemetry labelled real, synthetic or unknown; findings may describe"
        " test traffic."
    ),
    "provenance_reported_real": (
        "The telemetry is reported real by the instrumentation; provenance is not"
        " cryptographically verified (provenance_verified is false)."
    ),
    "provenance_unknown": (
        "The telemetry carries no real or synthetic label; it is not counted as real."
    ),
    "single_trace_scope": (
        "Only this trace is analysed; matching records in other traces are not looked up."
    ),
    "stored_metadata_only": (
        "Only stored, allowlisted metadata is used. Prompts, completions and error"
        " descriptions are never stored (ADR-19, R21) and were not used."
    ),
    "subtree_from_stored_links": (
        "The subtree is built from stored parent links among the analysed spans; missing and"
        " orphaned spans are not part of it."
    ),
    "synthetic_telemetry": (
        "The telemetry is labelled SYNTHETIC: findings describe test or mock traffic, not"
        " observed behaviour of a real provider."
    ),
    "unknown_is_not_zero": (
        "Unknown values stay unknown: they are never counted as zero tokens or zero cost."
    ),
}

PROVENANCE_LIMITATION = {
    "real": "provenance_reported_real",
    "synthetic": "synthetic_telemetry",
    "mixed": "provenance_mixed",
    "unknown": "provenance_unknown",
}

# error.type -> class. Exact identifiers only: those the LLM Insight SDK and its mock emit
# (sdk/src/llm_insight_sdk/{openai_adapter,mock}.py), HTTP status codes (semconv: error.type
# is the status code when one was received) and four OpenAI Python SDK 3.28.0 exception
# class names whose status or condition is fixed by the library. Everything else is
# `not_interpreted`; nothing is matched by substring.
ERROR_TYPE_CLASSES: dict[str, str] = {
    "429": "http_429",
    "openai.RateLimitError": "http_429",
    "openai.InternalServerError": "http_5xx",
    "timeout": "timeout",
    "openai.APITimeoutError": "timeout",
    "openai.APIConnectionError": "connection_error",
}

INTERPRETATIONS: dict[str, Interpretation] = {
    "http_429": Interpretation(
        "The recorded error.type is HTTP 429 (too many requests) or openai.RateLimitError:"
        " this suggests the provider or an intermediary throttled these attempts. The stored"
        " data does not show which limit applied.",
        (
            "Check the provider account's rate and usage limits in the provider's own console"
            " (not stored here).",
        ),
    ),
    "http_5xx": Interpretation(
        "The recorded error.type is an HTTP 5xx status or openai.InternalServerError: the"
        " server that answered reported an internal failure. The stored data does not show"
        " whether that was the provider or a proxy, or whether other requests were affected;"
        " it does not establish a provider outage.",
        (
            "Check the provider's status page for the time of the evidence; this platform cannot"
            " confirm an incident.",
        ),
    ),
    "http_4xx": Interpretation(
        "The recorded error.type is an HTTP 4xx status other than 429: the request was"
        " refused, for example for its parameters, credentials, permissions or model access."
        " The stored data does not show which.",
        (
            "Check the request parameters, the configured model and the credentials'"
            " permissions for this call (never put secrets into telemetry).",
        ),
    ),
    "timeout": Interpretation(
        "The recorded error.type is timeout or openai.APITimeoutError: the attempt reached a"
        " client-side deadline. The stored data does not show whether the provider or the"
        " network was slow, or whether the deadline was short.",
        ("Compare the attempt durations in the evidence with the client's configured timeout.",),
    ),
    "connection_error": Interpretation(
        "The recorded error.type is openai.APIConnectionError: no response was received from"
        " the provider endpoint. The stored data does not show whether the network, DNS, a"
        " proxy or the provider caused it.",
        ("Check the network and proxy configuration of the calling service at that time.",),
    ),
    "mixed": Interpretation(
        "The failed attempts recorded different error classes; they may have different causes.",
    ),
    "not_interpreted": Interpretation(
        "The recorded error.type is not one of the identifiers this ruleset interprets; no"
        " provider-related explanation is derived from it.",
    ),
    "not_recorded": Interpretation(
        "No error.type was recorded for the failed attempt(s); no provider-related"
        " explanation is derived.",
    ),
}

INTERPRETATION_CODES = {
    "http_429": "rate_limit_status_recorded",
    "http_5xx": "server_error_status_recorded",
    "http_4xx": "client_error_status_recorded",
    "timeout": "timeout_recorded",
    "connection_error": "connection_error_recorded",
    "mixed": "mixed_error_classes_recorded",
    "not_interpreted": "error_type_not_interpreted",
    "not_recorded": "error_type_not_recorded",
}

# Returned with every result: what this ruleset deliberately does not assess, and why.
NOT_ASSESSED: tuple[tuple[str, str], ...] = (
    (
        "elevated_latency",
        "No approved comparison basis exists: the contracts define no latency objective or"
        " threshold (threshold alerts, M21, are P1), and a percentile of peer attempts would"
        " flag a fixed share of attempts by construction. Durations are reported as facts"
        " only.",
    ),
    (
        "semantic_quality",
        "Correctness, relevance and hallucination cannot be assessed from metadata: no prompt"
        " or completion is stored (ADR-19) and no evaluation result is linked to this trace.",
    ),
    (
        "prompt_quality",
        "Prompt content is never stored (ADR-19) and is not reconstructed.",
    ),
    (
        "provider_outage",
        "One trace cannot establish a provider-wide incident, and no provider status data is"
        " stored.",
    ),
    (
        "root_cause",
        "Findings are hypotheses; a root-cause decision needs human review (Failure Review,"
        " ADR-10).",
    ),
)
