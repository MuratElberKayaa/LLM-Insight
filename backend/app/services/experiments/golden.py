"""Golden dataset `golden-synthetic-v1` and the scripted mock outputs for its two prompt versions
(Phase 4C, B2). Everything here is **SYNTHETIC**: invented for this project, not collected
from any provider, trace or person. Telemetry never stored prompts or completions (ADR-19).

Each case has a stable key (`G01`…`G12`), a synthetic input (prefixed `SYNTHETIC Gnn:`), an
approved expected output (or none, for T40), a rule of contract `llminsight.rule.v1`, and the
outcome the deterministic engine must produce for each prompt version. The mock provider
(`mock_provider.py`) answers a request by looking up (prompt version, input) here; the input
text is the lookup key, so the mock behaves identically whatever database ids the cases get.

Token counts and durations are scripted synthetic numbers, chosen once and never derived from
a real call. `None` usage (G12 under v2) is a deliberate timeout with no usage: its cost must
stay unknown, never 0.

| Key | Demonstrates | v1 (`golden-prompt-v1`) | v2 (`golden-prompt-v2`) |
|---|---|---|---|
| G01 | JSON Schema pass | pass | pass (other key order) |
| G02 | JSON Schema: missing required field | fail `schema_violation` | pass |
| G03 | prose instead of JSON | fail `output_not_json` | pass |
| G04 | JSON Schema: wrong type / enum | pass | fail `schema_violation` |
| G05 | field match, extra fields ignored | pass | pass |
| G06 | field match: value / string-vs-number | fail `field_mismatch` | fail `field_mismatch` |
| G07 | exact text | pass | fail `exact_mismatch` |
| G08 | exact JSON: key order, 3.0 == 3, array order | pass | fail `exact_mismatch` |
| G09 | RE2 full match | pass | fail `regex_no_match` |
| G10 | RE2 search, inline (?i) | pass | fail `regex_no_match` |
| G11 | no expected output (T40) | not evaluated | not evaluated |
| G12 | truncated JSON; timeout | fail `output_not_json` | not evaluated `provider_timeout` |
"""

import hashlib
from dataclasses import dataclass
from typing import Any

GOLDEN_VERSION = "golden.v1"
DATASET_NAME = "golden-synthetic-v1"
PROMPT_VERSION_A = "golden-prompt-v1"
PROMPT_VERSION_B = "golden-prompt-v2"
APPROVAL_LABEL = "golden-fixture-seed"  # a label, not a person: the seeding command approves

TICKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "priority"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "priority": {"enum": ["low", "medium", "high"]},
    },
    "additionalProperties": False,
}


def _schema_rule() -> dict[str, Any]:
    return {"version": 1, "type": "json_schema", "schema": TICKET_SCHEMA}


@dataclass(frozen=True)
class Scripted:
    """One scripted mock answer: `output` is None exactly when `error` is set."""

    output: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int | None
    error: str | None = None  # "provider_timeout" | "provider_error"


@dataclass(frozen=True)
class GoldenCase:
    key: str
    purpose: str
    sanitized_input: str
    expected_output: Any  # None: no approved expected output (T40)
    rule: dict[str, Any]
    answers: dict[str, Scripted]  # prompt version -> scripted answer
    expected: dict[str, tuple[bool | None, str]]  # prompt version -> (passed, outcome code)


A, B = PROMPT_VERSION_A, PROMPT_VERSION_B

CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        key="G01",
        purpose="valid structured output passes the JSON Schema rule",
        sanitized_input=(
            "SYNTHETIC G01: Turn the ticket 'Printer on floor 3 is offline' into JSON with"
            " title and priority."
        ),
        expected_output={"title": "Printer on floor 3 is offline", "priority": "high"},
        rule=_schema_rule(),
        answers={
            A: Scripted(
                '{"title": "Printer on floor 3 is offline", "priority": "high"}', 52, 18, 410
            ),
            B: Scripted('{"priority": "high", "title": "Printer offline (floor 3)"}', 61, 17, 380),
        },
        expected={A: (True, "pass"), B: (True, "pass")},
    ),
    GoldenCase(
        key="G02",
        purpose="a missing required field fails the JSON Schema rule",
        sanitized_input=(
            "SYNTHETIC G02: Turn the ticket 'VPN drops every hour' into JSON with title and"
            " priority."
        ),
        expected_output={"title": "VPN drops every hour", "priority": "medium"},
        rule=_schema_rule(),
        answers={
            A: Scripted('{"title": "VPN drops every hour"}', 49, 11, 350),
            B: Scripted('{"title": "VPN drops every hour", "priority": "medium"}', 58, 16, 395),
        },
        expected={A: (False, "schema_violation"), B: (True, "pass")},
    ),
    GoldenCase(
        key="G03",
        purpose="prose instead of JSON is malformed output (a failure, not unknown)",
        sanitized_input=(
            "SYNTHETIC G03: Turn the ticket 'Badge reader rejects new cards' into JSON with"
            " title and priority."
        ),
        expected_output={"title": "Badge reader rejects new cards", "priority": "low"},
        rule=_schema_rule(),
        answers={
            A: Scripted(
                'Sure! The title is "Badge reader rejects new cards" and the priority is low.',
                50,
                21,
                520,
            ),
            B: Scripted(
                '{"title": "Badge reader rejects new cards", "priority": "low"}', 59, 18, 402
            ),
        },
        expected={A: (False, "output_not_json"), B: (True, "pass")},
    ),
    GoldenCase(
        key="G04",
        purpose="a number where an enum string is required fails the JSON Schema rule",
        sanitized_input=(
            "SYNTHETIC G04: Turn the ticket 'Shared drive is read-only' into JSON with title"
            " and priority."
        ),
        expected_output={"title": "Shared drive is read-only", "priority": "medium"},
        rule=_schema_rule(),
        answers={
            A: Scripted(
                '{"title": "Shared drive is read-only", "priority": "medium"}', 51, 17, 365
            ),
            B: Scripted('{"title": "Shared drive is read-only", "priority": 2}', 60, 15, 371),
        },
        expected={A: (True, "pass"), B: (False, "schema_violation")},
    ),
    GoldenCase(
        key="G05",
        purpose="field match compares only the listed fields; key order and extras are ignored",
        sanitized_input=(
            "SYNTHETIC G05: Classify the message 'I was charged twice this month' as JSON with"
            " category, sentiment and meta.lang."
        ),
        expected_output={"category": "billing", "sentiment": "negative", "meta": {"lang": "en"}},
        rule={
            "version": 1,
            "type": "field_match",
            "fields": ["category", "sentiment", "meta.lang"],
        },
        answers={
            A: Scripted(
                '{"category": "billing", "sentiment": "negative", "meta": {"lang": "en"}}',
                47,
                22,
                330,
            ),
            B: Scripted(
                '{"meta": {"lang": "en", "confidence": 0.92}, "sentiment": "negative",'
                ' "category": "billing", "summary": "double charge"}',
                55,
                31,
                445,
            ),
        },
        expected={A: (True, "pass"), B: (True, "pass")},
    ),
    GoldenCase(
        key="G06",
        purpose="field match fails on a different value and on a string where a number is due",
        sanitized_input=(
            "SYNTHETIC G06: Classify the message 'Thanks, the refund arrived' as JSON with"
            " category, sentiment and urgency."
        ),
        expected_output={"category": "refund", "sentiment": "positive", "urgency": 1},
        rule={"version": 1, "type": "field_match", "fields": ["category", "sentiment", "urgency"]},
        answers={
            A: Scripted(
                '{"category": "refund", "sentiment": "neutral", "urgency": 1}', 46, 19, 315
            ),
            B: Scripted(
                '{"category": "refund", "sentiment": "positive", "urgency": "1"}', 54, 20, 338
            ),
        },
        expected={A: (False, "field_mismatch"), B: (False, "field_mismatch")},
    ),
    GoldenCase(
        key="G07",
        purpose="exact text match is code point equality (a trailing period differs)",
        sanitized_input="SYNTHETIC G07: Reply with exactly ACK to confirm receipt.",
        expected_output="ACK",
        rule={"version": 1, "type": "exact", "output_format": "text"},
        answers={
            A: Scripted("ACK", 31, 1, 120),
            B: Scripted("ACK.", 36, 2, 131),
        },
        expected={A: (True, "pass"), B: (False, "exact_mismatch")},
    ),
    GoldenCase(
        key="G08",
        purpose="exact JSON: key order ignored, 3.0 equals 3, array order matters",
        sanitized_input=(
            "SYNTHETIC G08: Return the status and the item numbers 1, 2 and 3 as JSON."
        ),
        expected_output={"status": "ok", "items": [1, 2, 3]},
        rule={"version": 1, "type": "exact", "output_format": "json"},
        answers={
            A: Scripted('{"items": [1, 2, 3.0], "status": "ok"}', 38, 14, 210),
            B: Scripted('{"status": "ok", "items": [3, 2, 1]}', 44, 14, 226),
        },
        expected={A: (True, "pass"), B: (False, "exact_mismatch")},
    ),
    GoldenCase(
        key="G09",
        purpose="RE2 full match: the whole output must match",
        sanitized_input=(
            "SYNTHETIC G09: Reply with only the reference of ticket 42 in the form ABC-1234."
        ),
        expected_output="TCK-0042",
        rule={"version": 1, "type": "regex", "pattern": "[A-Z]{3}-[0-9]{4}", "match": "full"},
        answers={
            A: Scripted("TCK-0042", 35, 4, 140),
            B: Scripted("Ticket TCK-0042", 41, 6, 152),
        },
        expected={A: (True, "pass"), B: (False, "regex_no_match")},
    ),
    GoldenCase(
        key="G10",
        purpose="RE2 search with an inline case-insensitive flag",
        sanitized_input="SYNTHETIC G10: Confirm to the customer that their refund was approved.",
        expected_output="Refund approved",
        rule={
            "version": 1,
            "type": "regex",
            "pattern": "(?i)refund (approved|issued)",
            "match": "search",
        },
        answers={
            A: Scripted("We have checked your request. REFUND APPROVED on Monday.", 37, 12, 260),
            B: Scripted("Your request is closed.", 43, 6, 198),
        },
        expected={A: (True, "pass"), B: (False, "regex_no_match")},
    ),
    GoldenCase(
        key="G11",
        purpose="no approved expected output: never evaluated (T40)",
        sanitized_input=(
            "SYNTHETIC G11: Turn the ticket 'Laptop fan is loud' into JSON with title and"
            " priority (no expected output approved yet)."
        ),
        expected_output=None,
        rule=_schema_rule(),
        answers={
            A: Scripted('{"title": "Laptop fan is loud", "priority": "low"}', 53, 16, 340),
            B: Scripted('{"title": "Laptop fan is loud", "priority": "low"}', 62, 16, 352),
        },
        expected={A: (None, "expected_output_missing"), B: (None, "expected_output_missing")},
    ),
    GoldenCase(
        key="G12",
        purpose="truncated JSON fails; a simulated timeout has no output, usage or cost",
        sanitized_input=(
            "SYNTHETIC G12: Classify the message 'My parcel is two weeks late' as JSON with"
            " category and sentiment."
        ),
        expected_output={"category": "shipping", "sentiment": "negative"},
        rule={"version": 1, "type": "field_match", "fields": ["category", "sentiment"]},
        answers={
            A: Scripted('{"category": "shipping", "sentiment": "neg', 48, 12, 300),
            B: Scripted(None, None, None, None, error="provider_timeout"),
        },
        expected={A: (False, "output_not_json"), B: (None, "provider_timeout")},
    ),
)


def input_key(sanitized_input: str) -> str:
    return hashlib.sha256(sanitized_input.encode("utf-8")).hexdigest()


SCRIPT: dict[tuple[str, str], Scripted] = {
    (version, input_key(case.sanitized_input)): answer
    for case in CASES
    for version, answer in case.answers.items()
}


def case_payloads(approved_by: str = APPROVAL_LABEL) -> list[dict[str, Any]]:
    """The cases as `POST /api/v1/datasets/{id}/cases` bodies (standalone: no source trace).
    Numbers are `int`/`Decimal` only, so the payload is exact JSON."""
    return [
        {
            "approved": True,
            "approved_by": approved_by,
            "sanitized_input": case.sanitized_input,
            "expected_output": case.expected_output,
            "rule": case.rule,
        }
        for case in CASES
    ]


def _no_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("golden fixtures hold exact numbers only")
    if isinstance(value, dict):
        for v in value.values():
            _no_floats(v)
    elif isinstance(value, list):
        for v in value:
            _no_floats(v)


for _case in CASES:  # import-time guard: a float would silently lose digits
    _no_floats(_case.expected_output)
    _no_floats(_case.rule)
del _case
