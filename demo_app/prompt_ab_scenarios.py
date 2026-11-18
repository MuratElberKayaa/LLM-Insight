"""Six invented tickets, two actual instructions, one frozen scoring rubric.

This is a LIVE-call input suite, not the scripted golden mock. Expected labels never
enter a provider request. Both versions use identical cases, output caps and rules.
V2 adds the business taxonomy and priority rubric that V1 leaves underspecified.
The comparison measures this specific instruction change, not general model quality.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType

from demo_app.comparison_scenarios import ComparisonScenario, compose_input
from demo_app.gemini_scenarios import ValidationResult, _finish, _parse_object
from llm_insight_sdk.live import Refusal, input_token_bound

SUITE = "ticket-ab-v1"
DATASET = "live-ticket-six-v1"
RULE_VERSION = "ticket-category-priority-v1"
CATEGORIES = ("billing", "login", "bug", "feature_request", "other")
PRIORITIES = ("low", "medium", "high")
RULE_NAMES = (
    "json_object",
    "no_markdown",
    "no_duplicate_keys",
    "exact_keys",
    "category_enum",
    "priority_enum",
    "expected_category",
    "expected_priority",
)
PROMPTS = MappingProxyType(
    {
        "v1": (
            "Classify the support ticket. Return only one JSON object with exactly two keys: "
            '"category" (billing, login, bug, feature_request, other) and "priority" '
            "(low, medium, high)."
        ),
        "v2": (
            "Classify the support ticket using this policy. Category: billing for charges or "
            "invoices; login for authentication; bug for broken existing functionality; "
            "feature_request for new capabilities; other for general questions. Priority: "
            "high only if existing work is blocked with no workaround; medium if functionality "
            "is degraded but a workaround exists; low for a new feature or information request. "
            "Choose from impact, not urgency words. Return only one JSON object, no Markdown "
            'or explanation, with exactly "category" and "priority". '
            "Use the lowercase labels above."
        ),
    }
)


@dataclass(frozen=True)
class Ticket:
    id: str
    text: str
    category: str
    priority: str


CASES = (
    Ticket(
        "t01",
        "Every sign-in attempt fails. I cannot access any work and have no workaround.",
        "login",
        "high",
    ),
    Ticket(
        "t02",
        "The invoice download button fails. I can still download the invoice from email.",
        "billing",
        "medium",
    ),
    Ticket(
        "t03",
        "URGENT: Please add a dark theme next month. All existing functions work.",
        "feature_request",
        "low",
    ),
    Ticket(
        "t04",
        "Saving any document crashes the app. No work can be saved by any other method.",
        "bug",
        "high",
    ),
    Ticket(
        "t05",
        "Sign-in fails in one browser, but I can do all my work using another browser.",
        "login",
        "medium",
    ),
    Ticket(
        "t06",
        "ASAP: Where can I read the user guide? The application is working normally.",
        "other",
        "low",
    ),
)


def validate(ticket: Ticket, text: str) -> ValidationResult:
    parsed = _parse_object(text)
    obj = parsed.value
    if obj is None:
        return _finish([(name, False) for name in RULE_NAMES], parsed.fence_stripped)
    category, priority = obj.get("category"), obj.get("priority")
    return _finish(
        [
            ("json_object", True),
            ("no_markdown", not parsed.fence_stripped),
            ("no_duplicate_keys", not parsed.duplicate_keys),
            ("exact_keys", set(obj) == {"category", "priority"}),
            ("category_enum", isinstance(category, str) and category in CATEGORIES),
            ("priority_enum", isinstance(priority, str) and priority in PRIORITIES),
            ("expected_category", category == ticket.category),
            ("expected_priority", priority == ticket.priority),
        ],
        parsed.fence_stripped,
    )


def _scenario(ticket: Ticket, variant: str) -> ComparisonScenario:
    sc = ComparisonScenario(
        id=f"cmp-ab-{ticket.id}-{variant}",
        version=1,
        fixture_id=f"{DATASET}.{ticket.id}",
        prompt_name="ab.ticket-triage",
        prompt_version=f"live-ticket-{variant}",
        label="TICKET",
        task=PROMPTS[variant],
        fixture=ticket.text,
        input_sha256="",
        input_utf8_bytes=0,
        max_output_tokens=1024,
        expected_form="json_object",
        validate=partial(validate, ticket),
        fixture_problem=lambda _: False,
    )
    from dataclasses import replace

    content = compose_input(sc).encode("utf-8")
    return replace(
        sc, input_sha256=hashlib.sha256(content).hexdigest(), input_utf8_bytes=len(content)
    )


SCENARIOS = MappingProxyType(
    {sc.id: sc for ticket in CASES for variant in PROMPTS for sc in [_scenario(ticket, variant)]}
)
# Filled from the reviewed text once; runtime recomputation must match this independent pin.
SUITE_SHA256 = "0362bac9b6a7806ccb855a1372e269ed9bcbb5d5e26ab41b6680e8b4c93e2c99"


def suite_digest() -> str:
    import json

    payload = {
        "dataset": DATASET,
        "rules": RULE_VERSION,
        "prompts": dict(PROMPTS),
        "cases": [(t.id, t.text, t.category, t.priority) for t in CASES],
        "inputs": {k: s.input_sha256 for k, s in SCENARIOS.items()},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check_scenario(key: str, scenario: ComparisonScenario) -> list[Refusal]:
    canonical = SCENARIOS.get(key)
    if canonical is None or scenario != canonical or suite_digest() != SUITE_SHA256:
        return [Refusal("ab_suite_changed", "the reviewed A/B suite does not match its pin")]
    content = compose_input(scenario)
    if (
        hashlib.sha256(content.encode()).hexdigest() != scenario.input_sha256
        or len(content.encode()) != scenario.input_utf8_bytes
        or input_token_bound(content) > 1024
        or scenario.max_output_tokens != 1024
    ):
        return [Refusal("ab_input_invalid", "the A/B input exceeds its pin or bounds")]
    return []


def ordered_scenarios() -> tuple[str, ...]:
    """Alternate AB / BA per case to reduce always-first-version bias; no extra calls."""
    return tuple(
        f"cmp-ab-{ticket.id}-{variant}"
        for i, ticket in enumerate(CASES)
        for variant in (("v1", "v2") if i % 2 == 0 else ("v2", "v1"))
    )
