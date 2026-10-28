"""Gemini real-mode credential boundary (Phase 6C-A, ADR-34), offline.

The real runner client is built exactly as `demo_app.live_gemini` builds it. Where a request
is sent, the real `httpx.HTTPTransport` class is used with only `handle_request` replaced
by a recorder (no socket is opened), and every non-loopback connection is blocked anyway.
"""

from __future__ import annotations

import io
import json
import ssl
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import certifi
import httpx
import pytest
from google import genai
from google.genai import types as genai_types

from llm_insight_sdk import gemini_live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.gemini_adapter import (
    GeminiTextAdapter,
    ObservedGeminiClient,
    live_gemini_client_problems,
)
from llm_insight_sdk.providers import TextRequest
from tests.conftest import DUMMY_KEY
from tests.gemini_fixtures import FAKE_GEMINI_KEY, MODEL, OUTPUT_SENTINEL, gemini_body
from tests.openai_fixtures import block_network
from tests.unit.test_gemini_live_guard import NOW, policy
from tests.unit.test_gemini_runner import runner_env

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from demo_app import live_gemini  # noqa: E402

URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


@pytest.fixture(autouse=True)
def offline() -> Iterator[None]:
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


class Sent(list[httpx.Request]):
    """The requests that reached the transport, plus scripted replies (default: success)."""

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[httpx.Response] = []


@pytest.fixture
def recorder(monkeypatch) -> Sent:
    """Replace only `HTTPTransport.handle_request`: the transport class, its connection pool
    and its TLS context stay the real ones the live check inspects."""
    sent = Sent()

    def handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        request.read()
        sent.append(request)
        return sent.replies.pop(0) if sent.replies else httpx.Response(200, json=gemini_body())

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    return sent


def runner_client(env: dict[str, str] | None = None) -> genai.Client:
    factory = live_gemini._default_client(env or {"GEMINI_API_KEY": FAKE_GEMINI_KEY})
    return factory(policy())


def safe_http(**overrides: Any) -> httpx.Client:
    kwargs: dict[str, Any] = {
        "verify": ssl.create_default_context(cafile=certifi.where()),
        "trust_env": False,
        "follow_redirects": False,
    }
    kwargs.update(overrides)
    return httpx.Client(**kwargs)


def client_with(
    *, http: httpx.Client | None = None, client_kw: dict | None = None, **options: Any
) -> genai.Client:
    opts: dict[str, Any] = {
        "base_url": "https://generativelanguage.googleapis.com/",
        "api_version": "v1beta",
        "timeout": 20_000,
        "httpx_client": safe_http() if http is None else http,
    }
    opts.update(options)
    if opts.get("httpx_client") is False:
        opts.pop("httpx_client")
    kw: dict[str, Any] = {"vertexai": False, "api_key": FAKE_GEMINI_KEY}
    kw.update(client_kw or {})
    return genai.Client(http_options=genai_types.HttpOptions(**opts), **kw)


class SubClient(genai.Client):
    pass


class CustomTransport(httpx.HTTPTransport):
    pass


def _mutate(attr_path: str, value: Any) -> Callable[[], genai.Client]:
    def build() -> genai.Client:
        c = runner_client()
        target: Any = c
        *parents, last = attr_path.split(".")
        for name in parents:
            target = getattr(target, name)
        if isinstance(target, dict):
            target[last] = value
        else:
            setattr(target, last, value)
        return c

    return build


REFUSED: dict[str, Callable[[], object]] = {
    "vertex ai client": lambda: genai.Client(vertexai=True, api_key=FAKE_GEMINI_KEY),
    "foreign base url": lambda: client_with(base_url="https://evil.example/"),
    "lookalike base url": lambda: client_with(
        base_url="https://generativelanguage.googleapis.com.evil.example/"
    ),
    "plain http": lambda: client_with(base_url="http://generativelanguage.googleapis.com/"),
    "other api version": lambda: client_with(api_version="v1"),
    "custom authorization header": lambda: client_with(headers={"Authorization": "Bearer x"}),
    "user project header": lambda: client_with(headers={"x-goog-user-project": "p"}),
    "custom header": lambda: client_with(headers={"X-Custom": "1"}),
    "extra body": lambda: client_with(extra_body={"tools": [{"google_search": {}}]}),
    "library retries": lambda: client_with(retry_options=genai_types.HttpRetryOptions(attempts=3)),
    "default library http client": lambda: client_with(httpx_client=False),
    "trust_env": lambda: client_with(http=safe_http(trust_env=True)),
    "redirects followed": lambda: client_with(http=safe_http(follow_redirects=True)),
    "explicit proxy": lambda: client_with(http=safe_http(proxy="http://127.0.0.1:9")),
    "transport mount": lambda: client_with(
        http=safe_http(mounts={"https://": httpx.HTTPTransport()})
    ),
    "mock transport": lambda: client_with(
        http=safe_http(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    ),
    "custom transport subclass": lambda: client_with(http=safe_http(transport=CustomTransport())),
    "tls verification off": lambda: client_with(http=safe_http(verify=False)),
    "event hook": lambda: client_with(http=safe_http(event_hooks={"request": [lambda r: None]})),
    "httpx auth": lambda: client_with(http=safe_http(auth=("u", "p"))),
    "credential header on the http client": lambda: client_with(
        http=safe_http(headers={"x-goog-api-key": "SYNTHETIC-other"})
    ),
    "client subclass": lambda: SubClient(vertexai=False, api_key=FAKE_GEMINI_KEY),
    "not a client": lambda: object(),
    "ephemeral token": lambda: client_with(client_kw={"api_key": "auth_tokens/SYNTHETIC"}),
    "replay client mode": _mutate("_debug_config.client_mode", "replay"),
    "api key header swapped": _mutate(
        "_api_client._http_options.headers.x-goog-api-key", "SYNTHETIC-other"
    ),
    "base url changed later": _mutate("_api_client._http_options.base_url", "https://x.example/"),
    "project": _mutate("_api_client.project", "SYNTHETIC-project"),
    "credentials": _mutate("_api_client._credentials", object()),
    "vertex flag": _mutate("_api_client.vertexai", True),
}


def test_the_runner_client_is_allowed():
    assert live_gemini_client_problems(runner_client()) == []


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_alternate_endpoints_and_unsafe_overrides_are_refused(name):
    problems = live_gemini_client_problems(REFUSED[name]())
    assert problems, name
    assert FAKE_GEMINI_KEY not in " ".join(problems)


def test_a_google_cloud_project_in_the_environment_is_caught_by_the_client_check(monkeypatch):
    """google-genai reads GOOGLE_CLOUD_PROJECT into the client even in Developer API mode;
    the guard refuses the variable, and the client check catches it independently."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "SYNTHETIC-project")
    assert any("project" in p for p in live_gemini_client_problems(runner_client()))


# ---------------------------------------------------------------- the exact final request
def _execute_real(stub, env: dict[str, str]) -> tuple[int, dict]:
    out = io.StringIO()
    code = live_gemini.main(["--execute"], env, now=NOW, provenance="real", out=out)
    text = out.getvalue()
    for secret in (FAKE_GEMINI_KEY, DUMMY_KEY, OUTPUT_SENTINEL, live_gemini.INPUT_TEXT):
        assert secret not in text
    return code, json.loads(text)


def test_the_credential_goes_only_to_the_generate_content_endpoint(stub, recorder):
    code, doc = _execute_real(stub, runner_env(stub.endpoint))
    assert code == 0 and doc["status"] == "completed" and doc["provenance"] == "real"
    (req,) = recorder
    assert (req.method, str(req.url)) == ("POST", URL)
    assert req.headers["x-goog-api-key"] == FAKE_GEMINI_KEY
    for name in ("authorization", "x-goog-user-project", "proxy-authorization"):
        assert name not in req.headers
    assert json.loads(req.content) == {
        "contents": [{"parts": [{"text": live_gemini.INPUT_TEXT}], "role": "user"}],
        "generationConfig": {"maxOutputTokens": 256},
    }
    assert req.extensions["timeout"] == {"connect": 20.0, "read": 20.0, "write": 20.0, "pool": 20.0}
    (span,) = [s for s in stub.spans() if s["kind"] == "CLIENT"]
    assert span["attributes"]["llminsight.provenance"] == "real"
    assert span["attributes"]["server.address"] == "generativelanguage.googleapis.com"


def test_the_environment_cannot_move_the_runner_client(monkeypatch, recorder):
    """Belt and braces below the guard's refusals: even with these variables in the process
    environment, the runner client still sends the same request with the same key."""
    for var, value in {
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "GOOGLE_GEMINI_BASE_URL": "https://evil.example/",
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_GENAI_USE_ENTERPRISE": "true",
        "GOOGLE_GENAI_CLIENT_MODE": "replay",
        "GOOGLE_API_KEY": "SYNTHETIC-other-key",
    }.items():
        monkeypatch.setenv(var, value)
    client = runner_client()
    assert live_gemini_client_problems(client) == []
    client.models.generate_content(model=MODEL, contents="x", config={"max_output_tokens": 16})
    (req,) = recorder
    assert str(req.url) == URL and req.headers["x-goog-api-key"] == FAKE_GEMINI_KEY


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_never_followed(stub, recorder, status):
    recorder.replies.append(
        httpx.Response(status, headers={"location": "https://evil.example/steal"})
    )
    code, doc = _execute_real(stub, runner_env(stub.endpoint))
    assert code == 1 and len(recorder) == 1 and str(recorder[0].url) == URL
    assert doc["error_category"] == "http_error"


def test_an_unreadable_ca_setting_refuses_before_sending(stub, recorder, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(REPO / "does-not-exist.pem"))
    code, doc = _execute_real(stub, runner_env(stub.endpoint))
    assert code == 2 and recorder == [] and doc["provider_request_sent"] is False
    assert doc["refusals"][0]["code"] == "client_construction_failed"


def test_the_client_is_checked_again_immediately_before_the_send(telemetry, recorder):
    client = runner_client()
    adapter = GeminiTextAdapter(
        ObservedGeminiClient(telemetry, client, provenance="real", require_live_endpoint=True)
    )
    client._api_client._http_options.base_url = "https://evil.example/"  # changed after the check
    with pytest.raises(ConfigError):
        adapter.generate_text(
            TextRequest(model=MODEL, input_text="SYNTHETIC", max_output_tokens=16)
        )
    assert recorder == []


def test_a_library_version_change_fails_closed(monkeypatch):
    from llm_insight_sdk import gemini_adapter

    monkeypatch.setitem(gemini_adapter.AUDITED_LIBRARY_VERSIONS, "google-genai", "0.0.0")
    assert gemini_adapter.gemini_library_problems() == [
        "google-genai must be the audited version 0.0.0"
    ]
    assert live_gemini_client_problems(runner_client())
    assert gemini_live.API_KEY_VAR == "GEMINI_API_KEY"
