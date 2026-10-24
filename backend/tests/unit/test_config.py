import importlib.util

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.helpers import DUMMY_PEPPER, DUMMY_TOKEN, REPO_ROOT, make_settings


def _load_init_env():
    spec = importlib.util.spec_from_file_location("init_env", REPO_ROOT / "scripts/dev/init_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _example_pairs() -> dict[str, str]:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    return dict(
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )


def test_defaults_match_env_example():
    """Typed defaults must equal the documented .env.example values."""
    example = _example_pairs()
    s = make_settings()
    assert s.bind_host == example["LLMI_BIND_HOST"]
    assert s.port == int(example["LLMI_PORT"])
    assert s.otlp_max_body_bytes == int(example["LLMI_OTLP_MAX_BODY_BYTES"])
    assert s.otlp_max_decompressed_bytes == int(example["LLMI_OTLP_MAX_DECOMPRESSED_BYTES"])
    assert s.otlp_max_spans_per_request == int(example["LLMI_OTLP_MAX_SPANS_PER_REQUEST"])
    assert s.rate_limit_ingest_rps == float(example["LLMI_RATE_LIMIT_INGEST_RPS"])
    assert s.rate_limit_ingest_burst == int(example["LLMI_RATE_LIMIT_INGEST_BURST"])
    assert s.rate_limit_api_rps == float(example["LLMI_RATE_LIMIT_API_RPS"])
    assert s.rate_limit_api_burst == int(example["LLMI_RATE_LIMIT_API_BURST"])
    assert s.rate_limit_authfail_per_min == int(example["LLMI_RATE_LIMIT_AUTHFAIL_PER_MIN"])
    assert s.retention_days == int(example["LLMI_RETENTION_DAYS"])
    assert s.allow_real_llm is (example["LLMI_ALLOW_REAL_LLM"] == "true")
    assert s.allow_real_llm is False


def test_contract_limits():
    s = make_settings()
    assert s.otlp_max_body_bytes == 4 * 1024 * 1024  # ADR-17
    assert s.otlp_max_decompressed_bytes == 16 * 1024 * 1024
    assert s.otlp_max_spans_per_request == 2048


@pytest.mark.parametrize(
    "overrides",
    [
        {"key_pepper": "CHANGE_ME"},
        {"admin_token": "CHANGE_ME"},
        {"key_pepper": "short"},
        {"admin_token": "x" * 31},
        {"database_url": "postgresql+psycopg://u:CHANGE_ME@127.0.0.1:55432/db"},
        {"database_url": "postgresql://u:pw@127.0.0.1:55432/db"},
        {"bind_host": "0.0.0.0"},  # noqa: S104 - asserting it is rejected
        {"bind_host": "192.168.1.10"},
        {"port": 0},
        {"otlp_max_body_bytes": 0},
        {"otlp_max_body_bytes": 1024, "otlp_max_decompressed_bytes": 512},
    ],
)
def test_invalid_settings_rejected(overrides):
    with pytest.raises(ValidationError):
        make_settings(**overrides)


def test_secrets_not_exposed_in_repr_or_dump():
    url = "postgresql+psycopg://u:verysecretpw@127.0.0.1:55432/db"
    s = make_settings(database_url=url)
    rendered = repr(s) + str(s) + s.model_dump_json()
    for secret in ("verysecretpw", DUMMY_PEPPER, DUMMY_TOKEN):
        assert secret not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key_pepper", "tooshort-secret"),
        ("admin_token", "tooshort-token"),
        ("database_url", "mysql://u:leakedpw@127.0.0.1/db"),
    ],
)
def test_validation_error_does_not_echo_secret(field, value):
    with pytest.raises(ValidationError) as exc:
        make_settings(**{field: value})
    assert value not in str(exc.value)
    assert "leakedpw" not in str(exc.value)


def test_env_file_from_example_loads(tmp_path):
    """.env rendered from .env.example by init_env loads; non-LLMI keys are ignored."""
    init_env = _load_init_env()
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    values = {
        "POSTGRES_PASSWORD": "a" * 48,
        "LLMI_KEY_PEPPER": "b" * 64,
        "LLMI_ADMIN_TOKEN": "c" * 64,
        "LLMI_API_TOKEN": "c" * 64,
    }
    content, filled = init_env.render(example, values)
    assert set(filled) == {*values, "LLMI_DATABASE_URL"}
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    s = Settings(_env_file=env_file)
    assert s.database_url.get_secret_value().endswith(f":{'a' * 48}@127.0.0.1:55432/llminsight")
    assert s.key_pepper.get_secret_value() == "b" * 64
    assert "X-Project-Key=CHANGE_ME" in content  # ingest key is created in Phase 1B


def test_init_env_never_overwrites(tmp_path, monkeypatch, capsys):
    init_env = _load_init_env()
    target = tmp_path / ".env"
    target.write_text("EXISTING=1\n", encoding="utf-8")
    monkeypatch.setattr(init_env, "TARGET", str(target))
    assert init_env.main() == 0
    assert target.read_text(encoding="utf-8") == "EXISTING=1\n"
    assert "left unchanged" in capsys.readouterr().out


def test_init_env_creates_without_printing_secrets(tmp_path, monkeypatch, capsys):
    init_env = _load_init_env()
    target = tmp_path / ".env"
    monkeypatch.setattr(init_env, "TARGET", str(target))
    assert init_env.main() == 0
    pairs = dict(
        ln.split("=", 1) for ln in target.read_text(encoding="utf-8").splitlines() if "=" in ln
    )
    out = capsys.readouterr().out
    for key in ("POSTGRES_PASSWORD", "LLMI_KEY_PEPPER", "LLMI_ADMIN_TOKEN"):
        assert len(pairs[key]) >= 48 and pairs[key] != "CHANGE_ME"
        assert pairs[key] not in out
    assert pairs["LLMI_API_TOKEN"] == pairs["LLMI_ADMIN_TOKEN"]
    assert f":{pairs['POSTGRES_PASSWORD']}@" in pairs["LLMI_DATABASE_URL"]
    assert pairs["OPENAI_API_KEY"] == ""
