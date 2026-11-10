"""The fixed SYNTHETIC scenarios of the multi-provider comparison runner (Phase 8A-4).

A closed registry in code, separate from `gemini_scenarios` (whose fixtures are Gemini's own and
whose records stay unchanged). No file is opened and no scenario comes from the command line, the
environment or a fixture. Every name, number and event below is invented. The texts are the ones
drafted in `reports/phase-8a-0.md` §6, and their composed SHA-256 values are pinned here.

**Identity.** Each scenario has its own `prompt_name` and its own `prompt_version`
(`cmp-v1.<scenario>`). `/models/compare` groups by provider, requested model, response model and
`prompt_version`, but not by prompt name, so a shared version would merge different scenarios into
one row (finding F2). The `cmp-` prefix keeps these rows apart from the Gemini demo's `v1`.

**One contract for every provider:** one user message, one attempt, no system prompt, tools or
structured output, the same output cap (1024 tokens; reasoning models can exhaust less), and the
provider's default temperature. Any text change needs a version bump and a new pin.

`check_scenario` refuses an entry whose text no longer matches its pin or exact size, or whose
fixture has an unexpected shape, before any policy is read or client exists. `input_sha256`
identifies the local text, not what a provider received.

Validators are deterministic and return rule codes and booleans only, never a value from the
answer. A pass shows that these rules held for one answer and nothing more. A failed validation
never changes the provider status and never causes a request. The answer-parsing helpers are
shared with the Gemini demo, so a rule means the same thing in both.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from demo_app import gemini_scenarios as shared
from demo_app.gemini_scenarios import PREAMBLE, ValidationResult, input_sha256, not_evaluated
from llm_insight_sdk.live import Refusal, input_token_bound

__all__ = [
    "PREAMBLE",
    "SCENARIOS",
    "ComparisonScenario",
    "ValidationResult",
    "check_scenario",
    "compose_input",
    "input_sha256",
    "not_evaluated",
    "prompt_version_for",
    "registry_problems",
    "validate_answer",
]

# Hard caps shared by every provider guard (Groq's ADR-44 and Gemini's ADR-35 hold the same
# values, which the tests check). A scenario must fit inside them as it is: nothing is trimmed.
INPUT_TOKEN_CAP = 1024
MIN_OUTPUT_TOKENS = 16
OUTPUT_TOKEN_CAP = 1024
UNIFORM_OUTPUT_TOKENS = 1024
MAX_ANSWER_CHARS = shared.MAX_ANSWER_CHARS
_SCENARIO_ID = re.compile(r"cmp-[a-z][a-z0-9-]{0,59}")


def prompt_version_for(scenario_id: str, version: int) -> str:
    """`cmp-v<version>.<scenario without its cmp- prefix>`: one Compare row per scenario text."""
    return f"cmp-v{version}.{scenario_id.removeprefix('cmp-')}"


@dataclass(frozen=True)
class ComparisonScenario:
    id: str
    version: int
    fixture_id: str
    prompt_name: str
    prompt_version: str
    label: str
    task: str
    fixture: str
    input_sha256: str
    input_utf8_bytes: int  # exact: a pinned text has exactly one size
    max_output_tokens: int
    expected_form: Literal["json_object", "plain_text"]
    validate: Callable[[str], ValidationResult] = field(repr=False, compare=False)
    fixture_problem: Callable[[str], bool] = field(repr=False, compare=False)


def compose_input(scenario: ComparisonScenario) -> str:
    """The exact text sent as the request's only message."""
    return f"{PREAMBLE}\n\n{scenario.task}\n\n{scenario.label}:\n{scenario.fixture}"


# ---------------------------------------------------------------- scenario 1: ticket triage
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
    parsed = shared._parse_object(text)
    obj = parsed.value
    if obj is None:
        return shared._finish([(name, False) for name in _TICKET_RULES], parsed.fence_stripped)
    category, priority = obj.get("category"), obj.get("priority")
    return shared._finish(
        [
            ("json_object", True),
            ("no_duplicate_keys", not parsed.duplicate_keys),
            ("exact_keys", set(obj) == _TICKET_KEYS),
            ("category_enum", isinstance(category, str) and category in _TICKET_CATEGORIES),
            ("priority_enum", isinstance(priority, str) and priority in _TICKET_PRIORITIES),
            ("reason_length", shared._sized_text(obj.get("reason"), 1, 120)),
            ("expected_category", category == "login"),
        ],
        parsed.fence_stripped,
    )


# ---------------------------------------------------------------- scenario 2: JSON aggregate
_USAGE_FIXTURE = (
    '{"dataset":"SYNTHETIC-comparison-usage","days":['
    '{"day":"Mon","errors":4,"requests":200},'
    '{"day":"Tue","errors":6,"requests":300},'
    '{"day":"Wed","errors":15,"requests":150},'
    '{"day":"Thu","errors":5,"requests":350}'
    '],"unit":"requests"}'
)
_USAGE_TOP_KEYS = frozenset({"dataset", "days", "unit"})
_USAGE_DAY_KEYS = frozenset({"day", "errors", "requests"})
_USAGE_ANSWER_KEYS = frozenset(
    {"total_requests", "highest_error_day", "error_rate_percent", "observation"}
)
_USAGE_RULES = (
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


def _usage_fixture_problem(fixture: str) -> bool:
    """True unless the fixture is exactly the expected data shape, with one unambiguous
    highest-error day: extra keys, such as anything resembling a provider setting, are refused."""
    parsed = shared._parse_object(fixture)
    data = parsed.value
    if data is None or parsed.duplicate_keys or parsed.fence_stripped:
        return True
    if set(data) != _USAGE_TOP_KEYS or not isinstance(data["days"], list):
        return True
    days = data["days"]
    if not 1 <= len(days) <= 7:
        return True
    if not all(
        isinstance(day, dict)
        and set(day) == _USAGE_DAY_KEYS
        and isinstance(day["day"], str)
        and shared._integer(day["errors"])
        and shared._integer(day["requests"])
        and day["errors"] >= 0
        and day["requests"] > 0
        for day in days
    ):
        return True
    errors = sorted((day["errors"] for day in days), reverse=True)
    return len(errors) > 1 and errors[0] == errors[1]


def _usage_expected() -> tuple[int, str, float]:
    days = json.loads(_USAGE_FIXTURE)["days"]
    total = sum(d["requests"] for d in days)
    errors = sum(d["errors"] for d in days)
    worst = max(days, key=lambda d: d["errors"])
    return total, worst["day"], errors / total * 100


def _validate_usage(text: str) -> ValidationResult:
    parsed = shared._parse_object(text)
    obj = parsed.value
    if obj is None:
        return shared._finish([(name, False) for name in _USAGE_RULES], parsed.fence_stripped)
    total, day, rate = _usage_expected()
    value = obj.get("total_requests")
    return shared._finish(
        [
            ("json_object", True),
            ("no_duplicate_keys", not parsed.duplicate_keys),
            ("exact_keys", set(obj) == _USAGE_ANSWER_KEYS),
            ("total_requests_integer", shared._integer(value)),
            ("expected_total", shared._integer(value) and value == total),
            ("expected_day", obj.get("highest_error_day") == day),
            (
                "expected_rate",
                shared._close_to(obj.get("error_rate_percent"), rate, _RATE_TOLERANCE),
            ),
            ("observation_length", shared._sized_text(obj.get("observation"), 1, 120)),
        ],
        parsed.fence_stripped,
    )


# ---------------------------------------------------------------- scenario 3: release summary
def _validate_summary(text: str) -> ValidationResult:
    return shared._finish(
        [
            ("non_empty", bool(text.strip())),
            ("max_chars", len(text.strip()) <= 280),
            ("max_two_sentences", shared._sentence_count(text) <= 2),
            ("no_markdown", shared._MARKDOWN.search(text) is None),
            ("mentions_known_issue", "import" in text.casefold()),
        ],
        False,
    )


def _no_shape_problem(fixture: str) -> bool:
    return False


# ---------------------------------------------------------------- the registry
_REGISTERED: tuple[ComparisonScenario, ...] = (
    ComparisonScenario(
        id="cmp-ticket-triage",
        version=1,
        fixture_id="cmp-ticket-2044",
        prompt_name="cmp.ticket-triage",
        prompt_version="cmp-v1.ticket-triage",
        label="TICKET",
        task=(
            "Classify the support ticket below. Reply with only one JSON object with exactly "
            'these keys: "category" (one of "billing", "login", "bug", "feature_request", '
            '"other"), "priority" (one of "low", "medium", "high"), "reason" (one sentence, '
            "at most 120 characters). Do not use Markdown."
        ),
        fixture=(
            "Ticket T-2044 from a fictional customer of the fictional Lumen Tasks app: after "
            "resetting my account this morning, the sign-in page keeps rejecting me and I cannot "
            "get in. My invoices and the rest of the app look fine from what my teammate can see."
        ),
        input_sha256="5d9040f3a67d37e5edaf4dab90a9a59ff236061c729c7b62dcd0a39812c267ff",
        input_utf8_bytes=621,
        max_output_tokens=UNIFORM_OUTPUT_TOKENS,
        expected_form="json_object",
        validate=_validate_ticket,
        fixture_problem=_no_shape_problem,
    ),
    ComparisonScenario(
        id="cmp-json-aggregate",
        version=1,
        fixture_id="cmp-usage-week-01",
        prompt_name="cmp.json-aggregate",
        prompt_version="cmp-v1.json-aggregate",
        label="DATA",
        task=(
            "Read the JSON data below. Reply with only one JSON object with exactly these "
            'keys: "total_requests" (integer), "highest_error_day" (the "day" value with the '
            'most errors), "error_rate_percent" (total errors divided by total requests, '
            'times 100, rounded to one decimal), "observation" (one sentence, at most 120 '
            "characters). Do not use Markdown."
        ),
        fixture=_USAGE_FIXTURE,
        input_sha256="89684d9402c71ad6681fbd77ad6199473612a5859da9580fa18dd537bcb31a37",
        input_utf8_bytes=661,
        max_output_tokens=UNIFORM_OUTPUT_TOKENS,
        expected_form="json_object",
        validate=_validate_usage,
        fixture_problem=_usage_fixture_problem,
    ),
    ComparisonScenario(
        id="cmp-release-summary",
        version=1,
        fixture_id="cmp-release-7-1",
        prompt_name="cmp.release-summary",
        prompt_version="cmp-v1.release-summary",
        label="RELEASE NOTE",
        task=(
            "Summarize the release note below in at most two sentences and at most 280 "
            "characters, in plain text without Markdown. Mention the known issue."
        ),
        fixture=(
            "Release note for the fictional Lumen Tasks app, version 7.1. Boards now load twice "
            "as fast on slow connections. Recurring tasks can be paused for a chosen period. The "
            "legacy calendar widget is removed; existing reminders are kept. A known issue "
            "remains: importing a project with more than 300 tasks can stall, and the team plans "
            "a fix in the next release."
        ),
        input_sha256="815eef603170295620d27a787ff3e8345384cc43e56b016bb4d6976ee135ea47",
        input_utf8_bytes=595,
        max_output_tokens=UNIFORM_OUTPUT_TOKENS,
        expected_form="plain_text",
        validate=_validate_summary,
        fixture_problem=_no_shape_problem,
    ),
)

SCENARIOS: Mapping[str, ComparisonScenario] = MappingProxyType({s.id: s for s in _REGISTERED})


def check_scenario(key: str, scenario: ComparisonScenario) -> list[Refusal]:
    """Every reason this registry entry may not be used; empty when it may. Runs before any
    policy is read or client exists. Messages name the rule and never quote the text."""
    problems: list[Refusal] = []
    text = compose_input(scenario)
    size = len(text.encode("utf-8"))

    def refuse(code: str, message: str) -> None:
        problems.append(Refusal(code, message))

    if key != scenario.id or not _SCENARIO_ID.fullmatch(scenario.id) or scenario.version < 1:
        refuse("scenario_identity_invalid", "the scenario ID or version is invalid")
    if (
        not shared._LABEL_SAFE.fullmatch(scenario.prompt_name)
        or not shared._LABEL_SAFE.fullmatch(scenario.prompt_version)
        or scenario.prompt_version != prompt_version_for(scenario.id, scenario.version)
    ):
        refuse("scenario_identity_invalid", "the prompt name or version is invalid")
    if input_sha256(text) != scenario.input_sha256:
        refuse("scenario_hash_mismatch", "the composed input does not match its pinned SHA-256")
    if size != scenario.input_utf8_bytes or input_token_bound(text) > INPUT_TOKEN_CAP:
        refuse("scenario_input_out_of_bounds", "the composed input is outside its size limits")
    if (
        shared._CONTROL.search(scenario.task)
        or shared._CONTROL.search(scenario.fixture)
        or shared._CREDENTIAL_OR_PII_SHAPE.search(scenario.fixture)
        or not scenario.fixture.strip()
        or scenario.fixture_problem(scenario.fixture)
    ):
        refuse("scenario_fixture_invalid", "the fixture does not have the expected safe form")
    if scenario.max_output_tokens != UNIFORM_OUTPUT_TOKENS or not (
        MIN_OUTPUT_TOKENS <= scenario.max_output_tokens <= OUTPUT_TOKEN_CAP
    ):
        refuse("scenario_output_cap_invalid", "the scenario's output cap is not the uniform cap")
    return problems


def registry_problems() -> list[Refusal]:
    """The registry as a whole: distinct prompt versions, so no two scenarios share a row."""
    versions = [s.prompt_version for s in _REGISTERED]
    names = [s.prompt_name for s in _REGISTERED]
    problems = [p for s in _REGISTERED for p in check_scenario(s.id, s)]
    if len(set(versions)) != len(versions) or len(set(names)) != len(names):
        problems.append(
            Refusal("scenario_identity_invalid", "two scenarios share a prompt name or version")
        )
    return problems


def validate_answer(scenario: ComparisonScenario, text: str) -> ValidationResult:
    """Run the scenario's rules on the answer text. Oversized text is never parsed."""
    if len(text) > MAX_ANSWER_CHARS:
        return ValidationResult("failed", "output_too_large")
    return scenario.validate(text)
