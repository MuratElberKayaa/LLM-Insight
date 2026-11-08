"""The fixed SYNTHETIC scenarios of the Gemini content demo (Phase 7B, ADR-39).

A closed registry in code: no file is opened and no scenario comes from the command line,
the environment or a fixture. Every name, number and event below is invented. The composed
input is one string, passed as the request's only content, so fixture data can never reach a
request field or a generation setting.

Any text change needs a version bump and a new pinned SHA-256 here and in the tests.
`check_scenario` refuses a registry entry whose text no longer matches its pin, whose size is
out of bounds, or whose fixture has an unexpected shape, before any Gemini client exists.

`input_sha256` identifies the local text. It is not proof of what the provider received.

Validators are deterministic and return rule codes and booleans only, never a value from the
answer. A pass shows that these rules held for one answer; it says nothing else about
quality. A failed validation never changes the provider status and never causes a request.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from llm_insight_sdk import gemini_live
from llm_insight_sdk.live import Refusal, input_token_bound

PREAMBLE = "SYNTHETIC LLM Insight demo data. Every name, number and event below is invented."
# input_token_bound = UTF-8 bytes + 64, and the session's hard input cap is 1024.
MAX_COMPOSED_BYTES = gemini_live.MAX_INPUT_TOKENS_CAP - 64
MAX_ANSWER_CHARS = 4096

ValidationStatus = Literal["passed", "failed", "not_evaluated"]


@dataclass(frozen=True)
class ValidationResult:
    status: ValidationStatus
    reason: str | None = None
    rules: tuple[tuple[str, bool], ...] = ()
    fence_stripped: bool = False

    def to_doc(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "rules": [{"rule": name, "passed": ok} for name, ok in self.rules],
            "fence_stripped": self.fence_stripped,
        }


def not_evaluated(reason: str) -> ValidationResult:
    return ValidationResult("not_evaluated", reason)


@dataclass(frozen=True)
class Scenario:
    id: str
    version: int
    fixture_id: str
    prompt_name: str
    prompt_version: str
    label: str
    task: str
    fixture: str
    input_sha256: str
    min_input_bytes: int
    max_input_bytes: int
    max_output_tokens: int
    expected_form: Literal["json_object", "plain_text"]
    validate: Callable[[str], ValidationResult] = field(repr=False, compare=False)
    fixture_problem: Callable[[str], bool] = field(repr=False, compare=False)


def compose_input(scenario: Scenario) -> str:
    """The exact text sent as the request's content."""
    return f"{PREAMBLE}\n\n{scenario.task}\n\n{scenario.label}:\n{scenario.fixture}"


def input_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- answer parsing
_FENCE = re.compile(r"\A\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n?[ \t]*```\s*\Z", re.DOTALL)
_MARKDOWN = re.compile(
    r"\*\*|__|`|~~|\[[^\]]*\]\(|^\s{0,3}(?:#{1,6}\s|[-*+]\s|>\s|\d+[.)]\s)", re.MULTILINE
)
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|\Z)")


@dataclass(frozen=True)
class _Parsed:
    value: dict[str, Any] | None
    duplicate_keys: bool
    fence_stripped: bool


def _reject_constant(name: str) -> None:
    raise ValueError("non-standard JSON constant")


def _parse_object(text: str) -> _Parsed:
    """One JSON object, optionally inside a single Markdown fence. Duplicate keys are
    recorded; `NaN` and `Infinity` are refused; anything else around the object fails."""
    fence = _FENCE.match(text)
    body = fence.group(1) if fence else text
    seen_duplicate = [False]

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len({key for key, _ in pairs}) != len(pairs):
            seen_duplicate[0] = True
        return dict(pairs)

    try:
        value = json.loads(body, object_pairs_hook=pairs_hook, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        return _Parsed(None, seen_duplicate[0], fence is not None)
    parsed = value if isinstance(value, dict) else None
    return _Parsed(parsed, seen_duplicate[0], fence is not None)


def _finish(rules: list[tuple[str, bool]], fence_stripped: bool) -> ValidationResult:
    passed = all(ok for _, ok in rules)
    return ValidationResult(
        "passed" if passed else "failed",
        None if passed else "rules_failed",
        tuple(rules),
        fence_stripped,
    )


def _sized_text(value: object, low: int, high: int) -> bool:
    return isinstance(value, str) and low <= len(value.strip()) <= high


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _close_to(value: object, expected: float, tolerance: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        number = float(value)
    except OverflowError:
        return False
    return math.isfinite(number) and abs(number - expected) <= tolerance


# ---------------------------------------------------------------- scenario A: classification
_TICKET_KEYS = frozenset({"category", "priority", "reason"})
_TICKET_CATEGORIES = ("billing", "login", "bug", "feature_request", "other")
_TICKET_PRIORITIES = ("low", "medium", "high")
_TICKET_RULES = (
    "json_object",
    "no_duplicate_keys",
    "exact_keys",
    "category_enum",
    "priority_enum",
    "reason_length",
    "expected_category",
)


def _validate_ticket(text: str) -> ValidationResult:
    parsed = _parse_object(text)
    obj = parsed.value
    if obj is None:
        return _finish([(name, False) for name in _TICKET_RULES], parsed.fence_stripped)
    category, priority = obj.get("category"), obj.get("priority")
    return _finish(
        [
            ("json_object", True),
            ("no_duplicate_keys", not parsed.duplicate_keys),
            ("exact_keys", set(obj) == _TICKET_KEYS),
            ("category_enum", isinstance(category, str) and category in _TICKET_CATEGORIES),
            ("priority_enum", isinstance(priority, str) and priority in _TICKET_PRIORITIES),
            ("reason_length", _sized_text(obj.get("reason"), 1, 120)),
            ("expected_category", category == "billing"),
        ],
        parsed.fence_stripped,
    )


# ---------------------------------------------------------------- scenario B: JSON reading
_WEEKLY_FIXTURE = (
    '{"dataset":"SYNTHETIC-weekly-usage","days":['
    '{"day":"Mon","errors":3,"requests":120},'
    '{"day":"Tue","errors":2,"requests":180},'
    '{"day":"Wed","errors":12,"requests":240},'
    '{"day":"Thu","errors":4,"requests":160}'
    '],"unit":"requests"}'
)
_WEEKLY_TOP_KEYS = frozenset({"dataset", "days", "unit"})
_WEEKLY_DAY_KEYS = frozenset({"day", "errors", "requests"})
_WEEKLY_ANSWER_KEYS = frozenset(
    {"total_requests", "highest_error_day", "error_rate_percent", "observation"}
)
_WEEKLY_RULES = (
    "json_object",
    "no_duplicate_keys",
    "exact_keys",
    "total_requests_integer",
    "expected_total",
    "expected_day",
    "expected_rate",
    "observation_length",
)
_RATE_TOLERANCE = 0.05


def _weekly_fixture_problem(fixture: str) -> bool:
    """True unless the fixture is exactly the expected data shape: extra keys (for example
    anything resembling a provider setting) are refused."""
    parsed = _parse_object(fixture)
    data = parsed.value
    if data is None or parsed.duplicate_keys or parsed.fence_stripped:
        return True
    if set(data) != _WEEKLY_TOP_KEYS or not isinstance(data["days"], list):
        return True
    days = data["days"]
    if not 1 <= len(days) <= 7:
        return True
    return not all(
        isinstance(day, dict)
        and set(day) == _WEEKLY_DAY_KEYS
        and isinstance(day["day"], str)
        and _integer(day["errors"])
        and _integer(day["requests"])
        and day["errors"] >= 0
        and day["requests"] > 0
        for day in days
    )


def _weekly_expected() -> tuple[int, str, float]:
    days = json.loads(_WEEKLY_FIXTURE)["days"]
    total = sum(d["requests"] for d in days)
    errors = sum(d["errors"] for d in days)
    worst = max(days, key=lambda d: d["errors"])
    return total, worst["day"], errors / total * 100


def _validate_weekly(text: str) -> ValidationResult:
    parsed = _parse_object(text)
    obj = parsed.value
    if obj is None:
        return _finish([(name, False) for name in _WEEKLY_RULES], parsed.fence_stripped)
    total, day, rate = _weekly_expected()
    value = obj.get("total_requests")
    return _finish(
        [
            ("json_object", True),
            ("no_duplicate_keys", not parsed.duplicate_keys),
            ("exact_keys", set(obj) == _WEEKLY_ANSWER_KEYS),
            ("total_requests_integer", _integer(value)),
            ("expected_total", _integer(value) and value == total),
            ("expected_day", obj.get("highest_error_day") == day),
            ("expected_rate", _close_to(obj.get("error_rate_percent"), rate, _RATE_TOLERANCE)),
            ("observation_length", _sized_text(obj.get("observation"), 1, 120)),
        ],
        parsed.fence_stripped,
    )


# ---------------------------------------------------------------- scenario C: summary
def _sentence_count(text: str) -> int:
    return sum(1 for part in _SENTENCE_END.split(text.strip()) if part.strip())


def _validate_summary(text: str) -> ValidationResult:
    return _finish(
        [
            ("non_empty", bool(text.strip())),
            ("max_chars", len(text.strip()) <= 280),
            ("max_two_sentences", _sentence_count(text) <= 2),
            ("no_markdown", _MARKDOWN.search(text) is None),
            ("mentions_known_issue", "export" in text.casefold()),
        ],
        False,
    )


def _no_shape_problem(fixture: str) -> bool:
    return False


# ---------------------------------------------------------------- the registry
_REGISTERED: tuple[Scenario, ...] = (
    Scenario(
        id="support-ticket-classification",
        version=1,
        fixture_id="ticket-001",
        prompt_name="demo.ticket-classify",
        prompt_version="v1",
        label="TICKET",
        task=(
            "Classify the support ticket below. Reply with only one JSON object with exactly "
            'these keys: "category" (one of "billing", "login", "bug", "feature_request", '
            '"other"), "priority" (one of "low", "medium", "high"), "reason" (one sentence, '
            "at most 120 characters). Do not use Markdown."
        ),
        fixture=(
            "Ticket T-1001 from a fictional customer of the fictional Acme Notes app: I was "
            "charged twice for the same monthly plan on my last invoice. The app works fine, "
            "but I need the duplicate charge reversed before my next billing date."
        ),
        input_sha256="18d3061e26e4b09759b469d674c0aba18a87a3610e83534ef679d758c2cbdf0d",
        min_input_bytes=64,
        max_input_bytes=800,
        max_output_tokens=512,
        expected_form="json_object",
        validate=_validate_ticket,
        fixture_problem=_no_shape_problem,
    ),
    Scenario(
        id="json-data-interpretation",
        version=1,
        fixture_id="weekly-usage-001",
        prompt_name="demo.json-interpret",
        prompt_version="v1",
        label="DATA",
        task=(
            "Read the JSON data below. Reply with only one JSON object with exactly these "
            'keys: "total_requests" (integer), "highest_error_day" (the "day" value with the '
            'most errors), "error_rate_percent" (total errors divided by total requests, '
            'times 100, rounded to one decimal), "observation" (one sentence, at most 120 '
            "characters). Do not use Markdown."
        ),
        fixture=_WEEKLY_FIXTURE,
        input_sha256="1e9f1ba31ae538115071dc95e5fd81aeef150685ccbc27112d157116105d4951",
        min_input_bytes=64,
        max_input_bytes=860,
        max_output_tokens=768,
        expected_form="json_object",
        validate=_validate_weekly,
        fixture_problem=_weekly_fixture_problem,
    ),
    Scenario(
        id="short-text-summary",
        version=1,
        fixture_id="release-note-001",
        prompt_name="demo.short-summary",
        prompt_version="v1",
        label="RELEASE NOTE",
        task=(
            "Summarize the release note below in at most two sentences and at most 280 "
            "characters, in plain text without Markdown. Mention the known issue."
        ),
        fixture=(
            "Release note for the fictional Acme Notes app, version 4.2. Search is now three "
            "times faster on large notebooks. Offline edits sync automatically when the "
            "connection returns. The old desktop theme is removed; users keep their colour "
            "settings. A known issue remains: exporting a notebook with more than 500 pages "
            "can time out, and the team plans a fix in the next release."
        ),
        input_sha256="740179ed99445b9564589b5c07518f4cea890fbb98dfa4ed112a680728be61b5",
        min_input_bytes=64,
        max_input_bytes=800,
        max_output_tokens=512,
        expected_form="plain_text",
        validate=_validate_summary,
        fixture_problem=_no_shape_problem,
    ),
)

SCENARIOS: Mapping[str, Scenario] = MappingProxyType({s.id: s for s in _REGISTERED})

_SCENARIO_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_LABEL_SAFE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CREDENTIAL_OR_PII_SHAPE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|\d(?:[ -]?\d){6,}|\bAIza|\bsk-|\blli_|Bearer\s|-----BEGIN|://"
)


def check_scenario(key: str, scenario: Scenario) -> list[Refusal]:
    """Every reason this registry entry may not be used; empty when it may. Runs before any
    Gemini client exists. Messages name the rule and never quote the text."""
    problems: list[Refusal] = []
    text = compose_input(scenario)
    size = len(text.encode("utf-8"))

    def refuse(code: str, message: str) -> None:
        problems.append(Refusal(code, message))

    if key != scenario.id or not _SCENARIO_ID.fullmatch(scenario.id) or scenario.version < 1:
        refuse("scenario_identity_invalid", "the scenario ID or version is invalid")
    if not (
        _LABEL_SAFE.fullmatch(scenario.prompt_name)
        and _LABEL_SAFE.fullmatch(scenario.prompt_version)
    ):
        refuse("scenario_identity_invalid", "the prompt name or version is invalid")
    if input_sha256(text) != scenario.input_sha256:
        refuse("scenario_hash_mismatch", "the composed input does not match its pinned SHA-256")
    if (
        not (
            scenario.min_input_bytes <= size <= scenario.max_input_bytes
            and size <= MAX_COMPOSED_BYTES
        )
        or input_token_bound(text) > gemini_live.MAX_INPUT_TOKENS_CAP
    ):
        refuse("scenario_input_out_of_bounds", "the composed input is outside its size limits")
    if (
        _CONTROL.search(scenario.task)
        or _CONTROL.search(scenario.fixture)
        or _CREDENTIAL_OR_PII_SHAPE.search(scenario.fixture)
        or not scenario.fixture.strip()
        or scenario.fixture_problem(scenario.fixture)
    ):
        refuse("scenario_fixture_invalid", "the fixture does not have the expected safe form")
    if not (
        gemini_live.MIN_OUTPUT_TOKENS
        <= scenario.max_output_tokens
        <= gemini_live.MAX_OUTPUT_TOKENS_CAP
    ):
        refuse("scenario_output_cap_invalid", "the scenario's output cap is outside the hard caps")
    return problems


def validate_answer(scenario: Scenario, text: str) -> ValidationResult:
    """Run the scenario's rules on the answer text. Oversized text is never parsed."""
    if len(text) > MAX_ANSWER_CHARS:
        return ValidationResult("failed", "output_too_large")
    return scenario.validate(text)
