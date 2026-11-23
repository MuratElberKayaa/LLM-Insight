"""Threshold alert rules (Phase 10F, ADR-59). Evaluated at read time; nothing is sent anywhere.

Rules live in `backend/alerts/rules.json` (contract `llminsight.alert_rules.v1`) and are checked
strictly at load. Each rule compares one overview metric of the window with a threshold
(`value >= threshold` fires) and needs at least `min_n` observations; below that the state is
`insufficient_data`, never `ok` or `firing`. Metrics come from `metrics.overview` (the same
numbers the dashboard shows), so no new query or estimate is introduced:

| metric | source (overview `total` bucket) | n |
|---|---|---|
| `inference_error_rate` | M04 `inference.errors.total.rate` | attempts |
| `inference_latency_p95_ms` | M06 `inference.latency.total.p95_ms` | latency n |
| `time_to_first_chunk_p95_ms` | M20 `inference.time_to_first_chunk.total.p95_ms` | TTFC n |
| `known_cost_usd` | M10 `inference.cost.total.known_cost_usd` | priced calls |

A known cost with unknown-cost calls is a lower bound: a rule that is not firing says so
(`lower_bound`), because the unknown part could cross the threshold. Delivery (webhook, Slack,
email) is deliberately absent: it needs a destination allowlist and its own security review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CONTRACT = "llminsight.alert_rules.v1"
METRICS = (
    "inference_error_rate",
    "inference_latency_p95_ms",
    "time_to_first_chunk_p95_ms",
    "known_cost_usd",
)
RULES_FILE = Path(__file__).resolve().parents[2] / "alerts" / "rules.json"
MAX_RULES = 20


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    metric: str
    threshold: Decimal
    min_n: int


def parse_rules(doc: Any) -> tuple[Rule, ...]:
    if not isinstance(doc, dict) or set(doc) != {"version", "rules"} or doc["version"] != CONTRACT:
        raise RulesError(f"a rules document needs exactly version={CONTRACT} and rules")
    raw = doc["rules"]
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_RULES:
        raise RulesError(f"rules must be a list of 1-{MAX_RULES} rules")
    rules = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict) or set(r) != {"id", "metric", "threshold", "min_n"}:
            raise RulesError(f"rule {i}: exactly id, metric, threshold, min_n")
        if (
            not isinstance(r["id"], str)
            or not r["id"].replace("_", "").isalnum()
            or len(r["id"]) > 64
        ):
            raise RulesError(f"rule {i}: id must be [A-Za-z0-9_], at most 64 characters")
        if r["metric"] not in METRICS:
            raise RulesError(f"rule {i}: unknown metric")
        if not isinstance(r["threshold"], str):
            raise RulesError(f"rule {i}: threshold must be a decimal string")
        try:
            threshold = Decimal(r["threshold"])
        except InvalidOperation:
            raise RulesError(f"rule {i}: threshold must be a decimal string") from None
        if not threshold.is_finite() or threshold < 0:
            raise RulesError(f"rule {i}: threshold must be finite and non-negative")
        if r["metric"] == "inference_error_rate" and threshold > 1:
            raise RulesError(f"rule {i}: an error-rate threshold is at most 1")
        if type(r["min_n"]) is not int or r["min_n"] < 1:
            raise RulesError(f"rule {i}: min_n must be an integer >= 1")
        rules.append(Rule(r["id"], r["metric"], threshold, r["min_n"]))
    if len({r.id for r in rules}) != len(rules):
        raise RulesError("rule ids must be distinct")
    return tuple(rules)


def load_rules(path: Path = RULES_FILE) -> tuple[Rule, ...]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RulesError(f"cannot read the rules file: {type(exc).__name__}") from None
    return parse_rules(doc)


def _observation(metric: str, inference: dict[str, Any]) -> tuple[Decimal | None, int, bool]:
    """(value, n, lower_bound) from the overview's `inference` block."""
    if metric == "inference_error_rate":
        e = inference["errors"]["total"]
        return (None if e["rate"] is None else Decimal(str(e["rate"]))), int(e["n"]), False
    if metric == "inference_latency_p95_ms":
        lat = inference["latency"]["total"]
        return (
            (None if lat["p95_ms"] is None else Decimal(str(lat["p95_ms"]))),
            int(lat["n"]),
            False,
        )
    if metric == "time_to_first_chunk_p95_ms":
        t = inference["time_to_first_chunk"]["total"]
        return (None if t["p95_ms"] is None else Decimal(str(t["p95_ms"]))), int(t["n"]), False
    c = inference["cost"]["total"]
    known = None if c["known_cost_usd"] is None else Decimal(c["known_cost_usd"])
    return known, int(c["computed_cost_calls"]), int(c["unknown_cost_calls"]) > 0


def evaluate(rules: tuple[Rule, ...], inference: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rule in rules:
        value, n, lower_bound = _observation(rule.metric, inference)
        if value is None or n < rule.min_n:
            state = "insufficient_data"
        else:
            state = "firing" if value >= rule.threshold else "ok"
        out.append(
            {
                "id": rule.id,
                "metric": rule.metric,
                "comparison": ">=",
                "threshold": format(rule.threshold, "f"),
                "min_n": rule.min_n,
                "n": n,
                "value": None if value is None else format(value, "f"),
                "state": state,
                # A known cost with unknown calls could still cross the threshold.
                "lower_bound": lower_bound and state != "firing",
            }
        )
    return out
