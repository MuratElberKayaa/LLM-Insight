"""Phase 6C-C: the additive, read-only `provider` filter of `GET /api/v1/traces`,
`GET /api/v1/overview` and `GET /api/v1/models/compare`, through the real ingest and PostgreSQL.
Every span is SYNTHETIC telemetry; the one `real` claim is a fixture (no provider is called).

| trace | start | inference rows (provider / model) | provenance | notes |
|---|---|---|---|---|
| t1 | T0 | openai / gpt-a | synthetic | |
| t2 | T0+1s | gcp.gemini / gemini-3.1-flash-lite | real (asserted, with response id) | |
| t3 | T0+2s | openai / gpt-b and gcp.gemini / gemini-x | synthetic | two providers |
| t4 | T0+3s | (no provider name → `unknown`) / gpt-a | synthetic | |
| t5 | T0+4s | openai / gpt-a | synthetic | ERROR |
| b1 | T0+5s, project B | openai / gpt-a | synthetic | other project |
"""

import pytest

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.otlp import T0, body, inference_attrs, request, span

pytestmark = pytest.mark.integration

SEC = 1_000_000_000
WINDOW = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-31T00:00:00Z"}  # contains T0
ERROR = 2


def tid(i: int) -> str:
    return f"{i:032x}"


def attrs(n: int, provider: str | None, model: str, **kw) -> dict:
    return inference_attrs(
        gen_ai__provider__name=provider,
        gen_ai__request__model=model,
        gen_ai__response__model=model,
        gen_ai__response__id=f"resp-SYNTHETIC-{n}",
        llminsight__call_id=f"5f0c6e1a-3b7d-4c2e-9a41-{n:012d}",
        **kw,
    )


@pytest.fixture
def seeded(api_client, make_key):
    a, b = make_key("p6c-a"), make_key("p6c-b")
    spans = [
        span(trace_id=tid(1), span_id="1" * 16, attrs=attrs(1, "openai", "gpt-a")),
        span(
            trace_id=tid(2),
            span_id="2" * 16,
            start=T0 + SEC,
            attrs=attrs(2, "gcp.gemini", "gemini-3.1-flash-lite", llminsight__provenance="real"),
        ),
        span(
            trace_id=tid(3), span_id="3" * 16, start=T0 + 2 * SEC, attrs=attrs(3, "openai", "gpt-b")
        ),
        span(
            trace_id=tid(3),
            span_id="4" * 16,
            start=T0 + 2 * SEC + 1000,
            attrs=attrs(4, "gcp.gemini", "gemini-x"),
        ),
        span(trace_id=tid(4), span_id="5" * 16, start=T0 + 3 * SEC, attrs=attrs(5, None, "gpt-a")),
        span(
            trace_id=tid(5),
            span_id="6" * 16,
            start=T0 + 4 * SEC,
            status_code=ERROR,
            attrs=attrs(6, "openai", "gpt-a"),
        ),
    ]
    assert post(api_client, body(request(*spans)), a.key).status_code == 200
    other = span(
        trace_id=tid(9), span_id="9" * 16, start=T0 + 5 * SEC, attrs=attrs(9, "openai", "gpt-a")
    )
    assert post(api_client, body(request(other)), b.key).status_code == 200
    return a.project_id, b.project_id


def get(client, path, project, **params):
    r = client.get(path, params={"project": project, **WINDOW, **params}, headers=ADMIN_AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def trace_ids(client, project, **params) -> list[str]:
    return [i["trace_id"] for i in get(client, "/api/v1/traces", project, **params)["items"]]


# ---------------------------------------------------------------- trace list
def test_trace_list_provider_filter_is_exact_and_project_scoped(api_client, seeded):
    a, b = seeded
    assert trace_ids(api_client, a, provider="openai") == [tid(5), tid(3), tid(1)]
    assert trace_ids(api_client, a, provider="gcp.gemini") == [tid(3), tid(2)]
    # A span without `gen_ai.provider.name` is stored as provider `unknown` (§3.1).
    assert trace_ids(api_client, a, provider="unknown") == [tid(4)]
    assert trace_ids(api_client, a, provider="OpenAI") == []  # never case-folded
    assert trace_ids(api_client, a, provider="open") == []  # exact, not a prefix
    assert trace_ids(api_client, a, provider="anthropic") == []
    assert trace_ids(api_client, b, provider="openai") == [tid(9)]  # project B only
    assert tid(9) not in trace_ids(api_client, a, provider="openai")


def test_provider_and_model_must_match_the_same_usage_row(api_client, seeded):
    a, _ = seeded
    # t3 has openai/gpt-b and gcp.gemini/gemini-x: no single row is openai + gemini-x.
    assert trace_ids(api_client, a, provider="openai", model="gemini-x") == []
    assert trace_ids(api_client, a, provider="gcp.gemini", model="gemini-x") == [tid(3)]
    assert trace_ids(api_client, a, provider="openai", model="gpt-a") == [tid(5), tid(1)]
    assert trace_ids(api_client, a, model="gpt-a") == [tid(5), tid(4), tid(1)]  # unchanged


def test_provider_combines_with_provenance_status_and_paging(api_client, seeded):
    a, _ = seeded
    assert trace_ids(api_client, a, provider="gcp.gemini", provenance="real") == [tid(2)]
    assert trace_ids(api_client, a, provider="gcp.gemini", provenance="synthetic") == [tid(3)]
    assert trace_ids(api_client, a, provider="openai", status="error") == [tid(5)]
    first = get(api_client, "/api/v1/traces", a, provider="openai", limit="2")
    assert [i["trace_id"] for i in first["items"]] == [tid(5), tid(3)]
    rest = get(
        api_client, "/api/v1/traces", a, provider="openai", limit="2", cursor=first["next_cursor"]
    )
    assert [i["trace_id"] for i in rest["items"]] == [tid(1)] and rest["next_cursor"] is None


# ---------------------------------------------------------------- aggregates
def test_overview_provider_filter_at_each_metric_level(api_client, seeded):
    a, _ = seeded
    o = get(api_client, "/api/v1/overview", a, provider="openai")
    assert o["filters"]["provider"] == "openai"
    assert o["inference"]["attempts"]["total"] == 3  # usage level: t1, t3 (openai row), t5
    assert o["traces"]["count"]["total"] == 3  # trace level: t1, t3, t5
    g = get(api_client, "/api/v1/overview", a, provider="gcp.gemini", provenance="real")
    assert g["inference"]["attempts"]["total"] == 1
    assert g["inference"]["attempts"]["by_provenance"]["real"] == 1
    assert g["traces"]["count"]["total"] == 1
    assert g["provenance_verified"] is False
    # Gemini cost is never computed (ADR-35): unknown, never 0.
    assert g["inference"]["cost"]["total"]["known_cost_usd"] is None
    assert g["inference"]["cost"]["total"]["cost_complete"] is False
    unfiltered = get(api_client, "/api/v1/overview", a)
    assert unfiltered["filters"]["provider"] is None
    assert unfiltered["inference"]["attempts"]["total"] == 6


def test_models_compare_provider_filter_keeps_only_that_providers_groups(api_client, seeded):
    a, _ = seeded
    c = get(api_client, "/api/v1/models/compare", a, provider="gcp.gemini")
    assert c["filters"]["provider"] == "gcp.gemini"
    assert sorted((g["provider"], g["requested_model"]) for g in c["groups"]) == [
        ("gcp.gemini", "gemini-3.1-flash-lite"),
        ("gcp.gemini", "gemini-x"),
    ]
    everything = get(api_client, "/api/v1/models/compare", a)
    assert {g["provider"] for g in everything["groups"]} == {"openai", "gcp.gemini", "unknown"}
    assert (
        get(api_client, "/api/v1/models/compare", a, provider="openai", model="gemini-x")["groups"]
        == []
    )


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("path", ["/api/v1/traces", "/api/v1/overview", "/api/v1/models/compare"])
@pytest.mark.parametrize(
    "value",
    [
        "",
        "p" * 65,
        "open ai",
        "-openai",
        "open:ai",
        "<script>alert(1)</script>",
        "openai\n",
        "ope%nai",
    ],
    ids=["empty", "too-long", "space", "leading-dash", "colon", "markup", "newline", "percent"],
)
def test_invalid_provider_is_422_naming_the_field_without_echo(api_client, make_key, path, value):
    k = make_key("p6c-v")
    r = api_client.get(
        path, params={"project": k.project_id, "provider": value}, headers=ADMIN_AUTH
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_request" and err["fields"] == ["provider"]
    if len(value) > 3:
        assert value not in r.text


def test_provider_filter_needs_admin_auth_first(api_client, make_key):
    k = make_key("p6c-auth")
    r = api_client.get("/api/v1/traces", params={"project": k.project_id, "provider": "<bad>"})
    assert r.status_code == 401  # auth before validation: nothing about parameters is revealed
