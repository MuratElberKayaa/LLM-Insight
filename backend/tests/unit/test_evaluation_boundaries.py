"""Static boundaries of the Phase 4C evaluation code (A2, A3, B3): no code execution, no
uncontrolled Python `re`, RE2 and `jsonschema` confined to their safety modules, no network
library, no `check_schema` (its format checker would run `re.compile` on user regexes), and
every validator built with an explicit no-retrieval registry."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
EVALUATION = APP / "services" / "evaluation"
EXPERIMENTS = APP / "services" / "experiments"
GUARDED = [p for d in (EVALUATION, EXPERIMENTS) if d.exists() for p in sorted(d.rglob("*.py"))]
ALL_APP = sorted(APP.rglob("*.py"))


def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported_modules(module: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_guarded_modules_exist():
    assert {p.name for p in GUARDED} >= {
        "engine.py",
        "rules.py",
        "safe_regex.py",
        "safe_schema.py",
        "json_values.py",
    }


def test_no_dynamic_code_execution_anywhere_in_the_backend():
    banned = {"eval", "exec", "compile", "__import__"}
    for path in ALL_APP:
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned, f"{path.name}: {node.func.id}()"
        assert "importlib" not in imported_modules(tree(path)), path.name


def test_python_re_is_only_used_with_constant_patterns_in_evaluation_code():
    for path in GUARDED:
        for node in ast.walk(tree(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
            ):
                assert node.func.attr == "compile", f"{path.name}: re.{node.func.attr}"
                first = node.args[0]
                assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                    f"{path.name}: re.compile with a non-constant pattern"
                )


def test_re2_and_jsonschema_are_confined_to_their_safety_modules():
    for path in ALL_APP:
        mods = imported_modules(tree(path))
        if "re2" in mods:
            assert path == EVALUATION / "safe_regex.py", path
        if mods & {"jsonschema", "jsonschema_specifications", "referencing"}:
            assert path == EVALUATION / "safe_schema.py", path


def test_no_network_library_in_evaluation_or_experiment_code():
    network = {"socket", "urllib", "http", "httpx", "httpx2", "requests", "openai", "aiohttp"}
    for path in GUARDED:
        assert not (imported_modules(tree(path)) & network), path.name


def test_check_schema_and_ref_resolver_are_never_used():
    for path in ALL_APP:
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(tree(path)):
            if isinstance(node, ast.Attribute):
                assert node.attr not in ("check_schema", "RefResolver"), f"{path.name}: {node.attr}"
        assert "RefResolver" not in source, path.name


def test_every_validator_is_built_with_an_explicit_registry_and_no_format_checker():
    module = tree(EVALUATION / "safe_schema.py")
    constructions = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("Draft202012Validator", "_ExactValidator")
    ]
    assert len(constructions) == 2
    for call in constructions:
        keywords = {k.arg: k.value for k in call.keywords}
        assert isinstance(keywords.get("registry"), ast.Name)
        assert keywords["registry"].id == "_NO_RETRIEVAL"
        assert isinstance(keywords.get("format_checker"), ast.Constant)
        assert keywords["format_checker"].value is None


def test_re2_errors_are_never_logged():
    source = (EVALUATION / "safe_regex.py").read_text(encoding="utf-8")
    assert "options.log_errors = False" in source


def test_experiment_and_evaluation_services_are_pure():
    """No database, telemetry repository or web framework in the engine, runner, mock provider
    or report: the API layer loads rows and stores what they return."""
    impure = {"sqlalchemy", "psycopg", "fastapi", "starlette"}
    for path in GUARDED:
        module = tree(path)
        assert not (imported_modules(module) & impure), path.name
        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.repositories"), path.name
                assert not node.module.startswith("app.api"), path.name


def test_only_the_experiments_api_writes_experiment_rows():
    writers = {"insert_experiment", "insert_results", "finish_experiment"}
    callers = set()
    for path in ALL_APP:
        for node in ast.walk(tree(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in writers
            ):
                callers.add(path.relative_to(APP).as_posix())
    assert callers == {"api/experiments.py"}


def test_no_openai_client_anywhere_in_the_backend():
    for path in ALL_APP:
        assert "openai" not in imported_modules(tree(path)), path.name
