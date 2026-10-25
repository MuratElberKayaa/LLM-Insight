"""Deterministic evaluation of test cases (Phase 4C; ADR-10, ADR-23).

- `rules`: rule contract `llminsight.rule.v1` (payloads, validation, expected-output checks);
- `engine`: `evaluate(rule, expected, output) -> Outcome`, pure and deterministic;
- `safe_regex`: RE2-only patterns with bounded program size and input;
- `safe_schema`: JSON Schema draft 2020-12 subset, local references, no network, bounded;
- `json_values`: exact structural equality of JSON values.

No language model, similarity score or invented quality metric is involved anywhere.
"""
