"""Live endpoint boundary (Phase 6B, ADR-32, R87), offline.

The real-mode credential may only travel to `https://api.openai.com/v1/responses`. These
tests build the runner's real client (the real `httpx2.HTTPTransport` class) and replace
only `HTTPTransport.handle_request` with a recorder, so they see exactly what would go on
the wire without opening a socket; every non-loopback connection is blocked as well. The
fake transports of the other suites stay available: `synthetic` provenance skips these
checks, `real` provenance refuses them."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import httpx2
import openai
import pytest

from llm_insight_sdk import live
from llm_insight_sdk.config import ConfigError
from llm_insight_sdk.openai_adapter import (
    LIVE_BASE_URL,
    ObservedOpenAIClient,
    check_live_client,
    live_client_problems,
    live_library_problems,
)
from tests.openai_fixtures import FAKE_API_KEY, block_network, response_body
from tests.unit.test_live_guard import live_env
from tests.unit.test_live_runner import (
    NOW,
    PHASE6B_PROFILE,
    PHASE6B_WORST_CASE,
    SMALL_USAGE,
    NeverCalled,
    live_openai,
    run,
    runner_env,
)

CLIENT_ENV = (
    "OPENAI_BASE_URL",
    "OPENAI_CUSTOM_HEADERS",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_ADMIN_KEY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


@pytest.fixture(autouse=True)
def offline(monkeypatch) -> Iterator[None]:
    for var in CLIENT_ENV:  # the process environment the `openai` client reads itself
        monkeypatch.delenv(var, raising=False)
    attempts: list[str] = []
    with block_network(attempts):
        yield
    assert attempts == [], f"a test tried to reach the network: {attempts}"


Reply = tuple[int, dict[str, Any], dict[str, str]]


@pytest.fixture
def wire(monkeypatch) -> tuple[list[httpx2.Request], list[Reply]]:
    """Records every request the real `HTTPTransport` would send; answers from `replies`
    (default: a SYNTHETIC completed Responses body)."""
    sent: list[httpx2.Request] = []
    replies: list[Reply] = []

    def handle_request(self: httpx2.HTTPTransport, request: httpx2.Request) -> httpx2.Response:
        request.read()
        # A proxied request reaches a different HTTPTransport (an HTTPProxy pool) with the
        # same URL, so the pool that would open the connection is recorded too.
        request.extensions["llmi_pool"] = type(self._pool).__name__
        sent.append(request)
        if replies:
            status, body, headers = replies.pop(0)
        else:
            body = response_body(usage=SMALL_USAGE, response_id="resp_SYNTHETIC_wire")
            status, headers = 200, {}
        return httpx2.Response(status, json=body, headers=headers)

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", handle_request)
    return sent, replies


def safe_http() -> httpx2.Client:
    return openai.DefaultHttpxClient(trust_env=False, follow_redirects=False)


def client(**overrides: Any) -> openai.OpenAI:
    kw: dict[str, Any] = {
        "api_key": FAKE_API_KEY,
        "base_url": "https://api.openai.com/v1",
        "max_retries": 0,
        "http_client": safe_http(),
    }
    kw.update(overrides)
    return openai.OpenAI(**{k: v for k, v in kw.items() if v is not None})


def runner_client() -> openai.OpenAI:
    policy = live.evaluate_policy(live_env(**PHASE6B_PROFILE), now=NOW).policy
    assert policy is not None
    return live_openai._default_client({"OPENAI_API_KEY": FAKE_API_KEY})(policy)


def profile_env(stub) -> dict[str, str]:
    return runner_env(stub.endpoint, **PHASE6B_PROFILE)


# ---------------------------------------------------------------- the allowed configuration
def test_the_runner_client_and_its_effective_copy_are_allowed():
    assert live_library_problems() == []  # openai 3.28.0 and httpx2 2.13.1, as audited
    c = runner_client()
    assert live_client_problems(c) == []
    assert live_client_problems(c.with_options(max_retries=0)) == []  # what the adapter uses
    assert str(c.base_url) == LIVE_BASE_URL
    assert c._client.follow_redirects is False and c._client._trust_env is False


# ---------------------------------------------------------------- refused configurations
class OpenAISubclass(openai.OpenAI):
    pass


def http(**kw: Any) -> httpx2.Client:
    kw.setdefault("trust_env", False)
    kw.setdefault("follow_redirects", False)
    return httpx2.Client(**kw)


REFUSED: dict[str, tuple[Callable[[], object], str]] = {
    "foreign_host": (lambda: client(base_url="https://evil.example/v1"), "base_url"),
    "lookalike_host": (
        lambda: client(base_url="https://api.openai.com.evil.example/v1"),
        "base_url",
    ),
    "plain_http": (lambda: client(base_url="http://api.openai.com/v1"), "base_url"),
    "other_port": (lambda: client(base_url="https://api.openai.com:8443/v1"), "base_url"),
    "userinfo": (lambda: client(base_url="https://u:p@api.openai.com/v1"), "base_url"),
    "other_path": (lambda: client(base_url="https://api.openai.com/v2"), "base_url"),
    "regional_endpoint": (
        lambda: client(base_url="https://eu.api.openai.com/v1"),
        "base_url",
    ),
    "data_residency": (lambda: client(base_url=None, data_residency="eu"), "base_url"),
    "custom_authorization": (
        lambda: client(default_headers={"Authorization": "Bearer SYNTHETIC-other"}),
        "custom headers",
    ),
    "custom_header": (lambda: client(default_headers={"X-Debug": "1"}), "custom headers"),
    "custom_query": (lambda: client(default_query={"route": "x"}), "query"),
    "organization": (lambda: client(organization="org_SYNTHETIC"), "organization"),
    "project": (lambda: client(project="proj_SYNTHETIC"), "project"),
    "admin_key": (lambda: client(admin_api_key="adm_SYNTHETIC"), "admin key"),
    "key_callback": (lambda: client(api_key=lambda: FAKE_API_KEY), "callback"),
    "sdk_retries": (lambda: client(max_retries=2), "max_retries"),
    "default_http_client": (lambda: client(http_client=None), "redirects"),
    "env_proxy_and_ca": (lambda: client(http_client=http(trust_env=True)), "trust_env"),
    "follows_redirects": (lambda: client(http_client=http(follow_redirects=True)), "redirects"),
    "explicit_proxy": (lambda: client(http_client=http(proxy="http://127.0.0.1:9")), "mount"),
    "transport_mount": (
        lambda: client(http_client=http(mounts={"https://": httpx2.HTTPTransport()})),
        "mount",
    ),
    "mock_transport": (
        lambda: client(http_client=http(transport=httpx2.MockTransport(lambda r: None))),
        "HTTPTransport",
    ),
    "custom_transport": (
        lambda: client(http_client=http(transport=type("T", (httpx2.HTTPTransport,), {})())),
        "HTTPTransport",
    ),
    "tls_unverified": (lambda: client(http_client=http(verify=False)), "TLS"),
    "event_hook": (
        lambda: client(http_client=http(event_hooks={"request": [lambda r: None]})),
        "hook",
    ),
    "http_auth": (lambda: client(http_client=http(auth=("u", "p"))), "auth"),
    "http_header": (
        lambda: client(http_client=http(headers={"Authorization": "Bearer SYNTHETIC-x"})),
        "credential or routing header",
    ),
    "azure": (
        lambda: openai.AzureOpenAI(
            api_key=FAKE_API_KEY,
            api_version="2024-10-21",
            azure_endpoint="https://synthetic.openai.azure.com",
            http_client=safe_http(),
        ),
        "openai.OpenAI instance",
    ),
    "subclass": (
        lambda: OpenAISubclass(api_key=FAKE_API_KEY, max_retries=0, http_client=safe_http()),
        "openai.OpenAI instance",
    ),
    "not_a_client": (lambda: safe_http(), "openai.OpenAI instance"),
}


@pytest.mark.parametrize(("make", "expected"), REFUSED.values(), ids=REFUSED.keys())
def test_unsafe_client_configurations_are_refused(make, expected):
    problems = live_client_problems(make())
    assert any(expected in p for p in problems), problems
    with pytest.raises(ConfigError) as refused:
        check_live_client(make())
    for secret in (FAKE_API_KEY, "SYNTHETIC-other", "evil.example", "u:p"):
        assert secret not in str(refused.value)  # names the rule, never a value


@pytest.mark.parametrize(
    ("var", "value", "expected"),
    [
        ("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer SYNTHETIC-other", "custom headers"),
        ("OPENAI_ORG_ID", "org_SYNTHETIC", "organization"),
        ("OPENAI_PROJECT_ID", "proj_SYNTHETIC", "project"),
        ("OPENAI_ADMIN_KEY", "adm_SYNTHETIC", "admin key"),
    ],
)
def test_client_environment_is_caught_even_in_the_copy_the_adapter_uses(
    monkeypatch, telemetry, var, value, expected
):
    good = runner_client()
    assert live_client_problems(good) == []
    monkeypatch.setenv(var, value)  # set after construction: `with_options` re-reads it
    with pytest.raises(ConfigError) as refused:
        ObservedOpenAIClient(telemetry, good, provenance="real", require_live_endpoint=True)
    assert expected in str(refused.value) and value not in str(refused.value)
    assert any(expected in p for p in live_client_problems(runner_client()))


def test_the_guard_also_refuses_an_admin_key_in_the_environment():
    decision = live.evaluate_policy(live_env(OPENAI_ADMIN_KEY="adm_SYNTHETIC"), now=NOW)
    assert "client_env_override" in {r.code for r in decision.refusals}


# ---------------------------------------------------------------- the final request boundary
def test_the_credential_goes_only_to_the_responses_endpoint_with_the_exact_request(stub, wire):
    sent, _ = wire
    rc, doc = run(["--execute"], profile_env(stub), provenance="real")
    assert rc == 0 and doc["provenance"] == "real" and doc["provider_request_sent"] is True
    (request,) = sent  # one physical request: no SDK retry, no redirect, no second call
    assert request.method == "POST"
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.extensions["llmi_pool"] == "ConnectionPool"  # direct, no proxy
    assert request.headers["authorization"] == f"Bearer {FAKE_API_KEY}"
    for routing in ("openai-organization", "openai-project"):
        assert routing not in request.headers
    assert request.headers["x-stainless-retry-count"] == "0"
    assert json.loads(request.content) == {
        "model": "gpt-5.4-mini",
        "input": live_openai.INPUT_TEXT,
        "max_output_tokens": 64,
        "service_tier": "default",
        "store": False,
    }
    timeout = request.extensions["timeout"]
    assert {timeout[k] for k in ("connect", "read", "write", "pool")} == {30.0}
    assert doc["plan"]["worst_case_request_usd"] == str(PHASE6B_WORST_CASE)
    assert doc["attempts"] == 1 and doc["estimated_cost_usd"] == "0.0000840000"
    (span,) = [s for s in stub.spans() if s["kind"] == "CLIENT"]
    assert span["attributes"]["llminsight.provenance"] == "real"
    assert span["attributes"]["server.address"] == "api.openai.com"


def test_the_environment_cannot_redirect_the_request(stub, wire, monkeypatch, tmp_path):
    sent, _ = wire
    # The guard reads `env`; the `openai`/`httpx2` libraries read the process environment.
    monkeypatch.setenv("OPENAI_BASE_URL", "https://evil.example/v1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")  # keeps the OTLP stub direct
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "attacker-ca.pem"))  # does not exist
    monkeypatch.setattr("httpx2._utils.getproxies", lambda: {"https": "http://127.0.0.1:9"})
    rc, doc = run(["--execute"], profile_env(stub), provenance="real")
    assert rc == 0 and doc["refusals"] == []
    (request,) = sent
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.extensions["llmi_pool"] == "ConnectionPool"  # direct, no proxy
    # For contrast: a default client would have taken the proxy from the environment.
    assert httpx2.Client(follow_redirects=False, verify=False)._mounts != {}


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_never_followed(stub, wire, status):
    sent, replies = wire
    replies.append((status, {}, {"location": "https://evil.example/collect"}))
    rc, doc = run(["--execute"], profile_env(stub), provenance="real")
    assert rc == 1 and doc["status"] == "error" and doc["attempts"] == 1
    assert [str(r.url) for r in sent] == ["https://api.openai.com/v1/responses"]
    assert doc["booked_against_budget_usd"] == str(PHASE6B_WORST_CASE)  # may be billed


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer SYNTHETIC-other"),
        ("OPENAI_ORG_ID", "org_SYNTHETIC"),
        ("OPENAI_PROJECT_ID", "proj_SYNTHETIC"),
        ("OPENAI_ADMIN_KEY", "adm_SYNTHETIC"),
    ],
)
def test_a_client_override_the_guard_did_not_see_is_refused_before_sending(
    stub, wire, monkeypatch, var, value
):
    """Defence in depth: the variable is only in the process environment, not in the
    mapping the guard evaluated, so only the client check can stop it."""
    sent, _ = wire
    monkeypatch.setenv(var, value)
    rc, doc = run(["--execute"], profile_env(stub), provenance="real")
    assert rc == 2 and sent == [] and doc["provider_request_sent"] is False
    assert {r["code"] for r in doc["refusals"]} == {"client_not_allowed"}
    assert value not in json.dumps(doc)
    assert stub.spans() == []  # telemetry never started


@pytest.mark.parametrize("make", ["foreign_host", "mock_transport", "default_http_client"])
def test_an_unsafe_injected_client_is_refused_in_real_mode(stub, wire, make):
    sent, _ = wire
    factory = REFUSED[make][0]
    rc, doc = run(
        ["--execute"], profile_env(stub), provenance="real", client_factory=lambda p: factory()
    )
    assert rc == 2 and sent == [] and {r["code"] for r in doc["refusals"]} == {"client_not_allowed"}
    assert doc["provider_request_sent"] is False and "booked_against_budget_usd" not in doc


def test_a_refused_client_releases_the_reservation(stub, wire, monkeypatch):
    sessions: list[live.LiveSession] = []

    class Recording(live.LiveSession):
        def __init__(self, policy: live.LivePolicy) -> None:
            super().__init__(policy)
            sessions.append(self)

    monkeypatch.setattr(live, "LiveSession", Recording)
    factory = REFUSED["foreign_host"][0]
    rc, _ = run(
        ["--execute"], profile_env(stub), provenance="real", client_factory=lambda p: factory()
    )
    assert rc == 2
    (session,) = sessions
    snap = session.snapshot()
    assert Decimal(str(snap["booked_usd"])) == Decimal(str(snap["outstanding_usd"])) == 0


def test_fake_transports_remain_available_for_synthetic_runs(stub):
    from tests.openai_fixtures import FakeOpenAI, response_ok

    fake = FakeOpenAI([response_ok(usage=SMALL_USAGE)])
    rc, doc = run(["--execute"], profile_env(stub), client_factory=lambda p: fake.client())
    assert rc == 0 and doc["provenance"] == "synthetic" and len(fake.requests) == 1


def test_the_dry_run_never_builds_a_client_and_reports_the_library_audit(stub):
    rc, doc = run([], profile_env(stub), provenance="real", client_factory=NeverCalled())
    assert rc == 0 and doc["refusals"] == [] and NeverCalled.calls == 0
    assert os.environ.get("OPENAI_API_KEY") is None  # no real key anywhere in this test run
