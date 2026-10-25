"""Static boundaries of Phase 4B (Step 4 of the instruction, ADR-10, ADR-19).

- Automatic diagnostics and human reviews do not import each other, so no code path can turn
  a diagnostic finding into a review or map a diagnostic category to an `issue_type`.
- Only the review endpoint inserts or updates `failure_reviews`, and only the dataset
  endpoint inserts `test_cases`; ingest never touches either table.
- Neither reads telemetry content attributes: there is nothing to copy into a test case.
"""

import ast
from pathlib import Path

import app

APP = Path(app.__file__).parent


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _sources(*parts: str) -> list[Path]:
    root = APP.joinpath(*parts)
    return [root] if root.is_file() else sorted(root.rglob("*.py"))


REVIEW_SIDE = [
    *_sources("api", "reviews.py"),
    *_sources("api", "datasets.py"),
    *_sources("api", "human_input.py"),
    *_sources("repositories", "reviews.py"),
    *_sources("repositories", "datasets.py"),
    *_sources("services", "redaction.py"),
    *_sources("services", "case_rules.py"),
    *_sources("models", "review.py"),
    *_sources("models", "dataset.py"),
]


def test_review_side_never_imports_diagnostics():
    for path in REVIEW_SIDE:
        assert not any("diagnostics" in name for name in _imports(path)), path.name


def test_diagnostics_never_import_reviews_or_datasets():
    forbidden = ("review", "dataset", "case_rules", "redaction", "human_input")
    for path in _sources("services", "diagnostics") + _sources("api", "diagnostics.py"):
        for name in _imports(path):
            assert not any(word in name for word in forbidden), (path.name, name)


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            out.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return out


def test_only_the_explicit_endpoints_write_reviews_and_cases():
    writers = {"insert_review": set(), "update_review": set(), "insert_case": set()}
    for path in sorted(APP.rglob("*.py")):
        calls = _calls(path)
        for fn, users in writers.items():
            if fn in calls:
                users.add(path.relative_to(APP).as_posix())
    assert writers == {
        "insert_review": {"api/reviews.py"},
        "update_review": {"api/reviews.py"},
        "insert_case": {"api/datasets.py"},
    }


def test_ingest_and_read_paths_do_not_reference_human_tables():
    for parts in (("services", "ingestion"), ("api", "otel.py"), ("repositories", "telemetry.py")):
        for path in _sources(*parts):
            src = path.read_text(encoding="utf-8")
            for word in ("failure_reviews", "FailureReview", "test_cases", "TestCase"):
                assert word not in src, (path.name, word)


def test_human_input_code_never_reads_content_attributes():
    content = (
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.definitions",
        "gen_ai.prompt",
    )
    for path in REVIEW_SIDE:
        src = path.read_text(encoding="utf-8")
        assert not any(c in src for c in content), path.name
