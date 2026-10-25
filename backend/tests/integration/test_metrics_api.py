"""Aggregate metrics M01–M15 through the real path: OTLP/HTTP protobuf → /v1/traces →
PostgreSQL → `/api/v1/overview` and `/api/v1/models/compare` (Phase 2C; T24, T29, T32,
T48-S1). Every span is SYNTHETIC telemetry; prices are SYNTHETIC test prices of a fresh
`synthetic.*` provider, never a real provider's.

The dataset below is small enough to compute every expected value by hand (comments).
Window W = [2026-10-25T00:00Z, 2026-10-26T00:00Z).

| trace | start | spans (duration) | provenance | notes |
|---|---|---|---|---|
| T7 | W.from exactly | e1 root inference (80 ms) | synthetic | unpriced model, no call_id |
| T1 | +1 h | root 100 ms; a1 10 ms; a2 20 ms ERROR 429; a3 30 ms | synthetic | a2+a3: 1 call |
| T2 | +2 h | root 200 ms; b1 40 ms real; b2 60 ms synthetic | mixed | prompt v2 |
| T3 | +3 h | root 300 ms | unknown | no inference |
| T4 | +4 h | root 400 ms; d1 50 ms unpriced; d2 70 ms estimated usage | synthetic | |
| T5 | +5 h | orphan 500 ms (parent not stored) | unknown | no root span |
| T8 | W.to exactly | inference | synthetic | outside (exclusive end) |
| T9 | W.from − 1 h | inference | synthetic | outside |
| T10 | +6 h, project B | inference | real | other project |
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span
from sqlalchemy import insert

from app.models import ModelPrice
from tests.helpers import letters
from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import post
from tests.otlp import MS, body, request, span

pytestmark = pytest.mark.integration

F = datetime(2026, 10, 25, tzinfo=UTC)
TO = F + timedelta(days=1)
WINDOW = {"from": "2026-10-25T00:00:00Z", "to": "2026-10-26T00:00:00Z"}
ERROR = 2  # OTLP Status.STATUS_CODE_ERROR
UNPRICED = "synthetic-unpriced"
PRICED = "synthetic-model"


def ns(dt: datetime) -> int:
    return int((dt - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 1_000_000_000


def tid(n: int) -> str:
    return f"{n:032x}"


def sid(n: int) -> str:
    return f"{n:016x}"


def call(n: int) -> str:
    return f"5f0c6e1a-3b7d-4c2e-9a41-{n:012d}"


def inference(provider: str, **kw) -> dict:
    """Attributes of one SYNTHETIC inference span (metadata only)."""
    a = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": kw.get("req", PRICED),
        "llminsight.provenance": kw.get("prov", "synthetic"),
    }
    optional = {
        "gen_ai.response.model": kw.get("resp", kw.get("req", PRICED)),
        "gen_ai.response.id": kw.get("resp_id"),
        "gen_ai.usage.input_tokens": kw.get("tin"),
        "gen_ai.usage.output_tokens": kw.get("tout"),
        "gen_ai.usage.cache_read.input_tokens": kw.get("cached"),
        "gen_ai.usage.reasoning.output_tokens": kw.get("reasoning"),
        "gen_ai.prompt.version": kw.get("prompt"),
        "llminsight.call_id": kw.get("call"),
        "llminsight.attempt": kw.get("attempt"),
        "llminsight.usage_source": kw.get("source"),
        "error.type": kw.get("error_type"),
    }
    if kw.get("no_resp_model"):
        optional["gen_ai.response.model"] = None
    a.update({k: v for k, v in optional.items() if v is not None})
    return a


def sp(trace: int, span_id: int, at: datetime, ms: int, *, parent=None, attrs=None, error=False):
    is_inference = attrs is not None and "gen_ai.operation.name" in attrs
    return span(
        trace_id=tid(trace),
        span_id=sid(span_id),
        parent=sid(parent) if parent else None,
        name="chat synthetic-model" if is_inference else "demo.request",
        kind=Span.SPAN_KIND_CLIENT if is_inference else Span.SPAN_KIND_SERVER,
        start=ns(at),
        end=ns(at) + ms * MS,
        attrs=attrs or {},
        status_code=ERROR if error else 0,
    )


@pytest.fixture
def provider(ingest_engine) -> tuple[str, str]:
    """A fresh SYNTHETIC provider with one SYNTHETIC price row (append-only table)."""
    name = f"synthetic.m{letters()}"
    version = f"synthetic-{letters(8)}"
    with ingest_engine.begin() as c:
        c.execute(
            insert(ModelPrice).values(
                version=version,
                provider=name,
                model=PRICED,
                effective_from=F - timedelta(days=1),
                input_per_million=Decimal("1"),
                cached_input_per_million=Decimal("0.5"),
                output_per_million=Decimal("2"),
                source_url="https://example.invalid/synthetic",
                verified_at=F - timedelta(days=1),
            )
        )
    return name, version


def dataset(p: str) -> list:
    h = timedelta(hours=1)
    msec = timedelta(milliseconds=1)
    return [
        # T7: the inference span is the root; exactly at W.from (inclusive); no call_id
        sp(7, 0x71, F, 80, attrs=inference(p, req=UNPRICED, resp_id="resp-e1", tin=50, tout=5)),
        # T1: root + a1 (priced) + a2 (429, no usage) + a3 (retry of a2, priced)
        sp(1, 0x10, F + h, 100),
        sp(1, 0x11, F + h + msec, 10, parent=0x10,
           attrs=inference(p, resp_id="resp-a1", tin=1000, tout=200, cached=400, reasoning=50,
                           call=call(1), attempt=1, prompt="v1")),
        sp(1, 0x12, F + h + 20 * msec, 20, parent=0x10, error=True,
           attrs=inference(p, no_resp_model=True, call=call(2), attempt=1, prompt="v1",
                           error_type="429")),
        sp(1, 0x13, F + h + 50 * msec, 30, parent=0x10,
           attrs=inference(p, resp_id="resp-a3", tin=500, tout=100, call=call(2), attempt=2,
                           prompt="v1")),
        # T2: one real + one synthetic attempt -> trace `mixed`
        sp(2, 0x20, F + 2 * h, 200),
        sp(2, 0x21, F + 2 * h + msec, 40, parent=0x20,
           attrs=inference(p, prov="real", resp_id="resp-b1", tin=100, tout=10, call=call(3),
                           attempt=1, prompt="v2")),
        sp(2, 0x22, F + 2 * h + 2 * msec, 60, parent=0x20,
           attrs=inference(p, resp_id="resp-b2", tin=200, tout=20, call=call(4), attempt=1,
                           prompt="v2")),
        # T3: a root without inference -> `unknown`
        sp(3, 0x30, F + 3 * h, 300),
        # T4: an unpriced model and estimated usage
        sp(4, 0x40, F + 4 * h, 400),
        sp(4, 0x41, F + 4 * h + msec, 50, parent=0x40,
           attrs=inference(p, req=UNPRICED, resp_id="resp-d1", tin=300, tout=30, call=call(5),
                           attempt=1)),
        sp(4, 0x42, F + 4 * h + 2 * msec, 70, parent=0x40,
           attrs=inference(p, resp_id="resp-d2", tin=10, tout=1, source="estimated",
                           call=call(6), attempt=1)),
        # T5: only an orphan (its parent was never stored) -> no root
        sp(5, 0x51, F + 5 * h, 500, parent=0xFFFF),
        # outside the window
        sp(8, 0x81, TO, 10, attrs=inference(p, resp_id="resp-f1", tin=1, tout=1, call=call(8),
                                            attempt=1)),
        sp(9, 0x91, F - h, 10, attrs=inference(p, resp_id="resp-g1", tin=1, tout=1, call=call(9),
                                               attempt=1)),
    ]  # fmt: skip


@pytest.fixture
def loaded(api_client, make_key, provider):
    """Project A with the dataset, project B with one real attempt inside the window."""
    a, b = make_key("metrics-a"), make_key("metrics-b")
    assert post(api_client, body(request(*dataset(provider[0]))), a.key).status_code == 200
    other = sp(10, 0xA1, F + 6 * timedelta(hours=1), 999, attrs=inference(
        provider[0], prov="real", resp_id="resp-other", tin=7777, tout=777, call=call(10),
        attempt=1))  # fmt: skip
    assert post(api_client, body(request(other)), b.key).status_code == 200
    return a, b, provider


def get(client, path: str, **params):
    return client.get(path, params=params, headers=ADMIN_AUTH)


def overview(client, project: int, **params) -> dict:
    r = get(client, "/api/v1/overview", project=project, **WINDOW, **params)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


# ---------------------------------------------------------------- T32: counts
def test_t32_trace_and_inference_counts_are_separate_and_exact(api_client, loaded):
    a, _, _ = loaded
    o = overview(api_client, a.project_id)
    traces, inf = o["traces"], o["inference"]
    # M01: T7, T1, T2, T3, T4, T5 (T8 at `to` and T9 before `from` are outside)
    assert traces["count"] == {
        "total": 6,
        "by_provenance": {"real": 0, "synthetic": 3, "mixed": 1, "unknown": 2},
    }
    # M02: e1, a1, a2, a3, b1, b2, d1, d2: retries are separate physical attempts
    assert inf["attempts"] == {
        "total": 8,
        "by_provenance": {"real": 1, "synthetic": 7, "unknown": 0},
    }
    # M03: 6 distinct call_ids (a2 and a3 share one); e1 has none
    assert inf["logical_calls"]["total"] == {
        "value": 6,
        "attempts_without_call_id": 1,
        "n": 8,
    }
    # M04: one failed attempt out of 8
    assert inf["errors"]["total"] == {"n": 8, "failed": 1, "succeeded": 7, "rate": 0.125}
    assert inf["errors"]["by_error_type"] == [{"error_type": "429", "count": 1}]
    assert inf["errors"]["by_provenance"]["real"] == {
        "n": 1, "failed": 0, "succeeded": 1, "rate": 0.0
    }  # fmt: skip
    # M05: T1 is an error trace (a recovered retry still marks it, R27)
    assert traces["error_rate"]["total"] == {"n": 6, "error_traces": 1, "rate": 0.166667}


def test_mixed_and_unknown_are_never_counted_as_real(api_client, loaded):
    """T48-S1 at the API: the mixed trace T2 is not a real trace, but its real usage row is
    a real inference attempt (METRICS_CATALOG.md provenance rules 1–3)."""
    a, _, _ = loaded
    real = overview(api_client, a.project_id, provenance="real")
    assert real["provenance_label"] == "real only"
    assert real["traces"]["count"]["total"] == 0
    assert real["inference"]["attempts"]["total"] == 1
    assert real["inference"]["cost"]["total"]["known_cost_usd"] == "0.0001200000"
    mixed = overview(api_client, a.project_id, provenance="mixed")
    assert mixed["traces"]["count"]["total"] == 1
    assert mixed["inference"]["attempts"]["total"] == 0  # no physical attempt is mixed
    everything = overview(api_client, a.project_id)
    assert everything["provenance_label"] == "all provenance"
    assert everything["provenance_verified"] is False


# ---------------------------------------------------------------- tokens and cost (M08–M14)
def test_tokens_are_summed_without_unknowns_or_subsets(api_client, loaded):
    a, _, _ = loaded
    tokens = overview(api_client, a.project_id)["inference"]["tokens"]["total"]
    # input 50+1000+500+100+200+300+10 = 2160 (a2 unknown); output 5+200+100+10+20+30+1 = 366
    assert tokens["input_tokens"] == 2160
    assert tokens["output_tokens"] == 366
    assert tokens["calls_with_unknown_input_tokens"] == 1
    assert tokens["calls_with_unknown_output_tokens"] == 1
    assert tokens["subsets"] == {
        "cached_input_tokens": {"sum": 400, "calls_reporting": 1},
        "cache_write_input_tokens": {"sum": None, "calls_reporting": 0},
        "reasoning_output_tokens": {"sum": 50, "calls_reporting": 1},
    }


def test_known_cost_is_an_exact_decimal_shown_with_unknown_cost(api_client, loaded):
    a, _, (provider, version) = loaded
    inf = overview(api_client, a.project_id)["inference"]
    cost = inf["cost"]["total"]
    # a1 (600·1 + 400·0.5 + 200·2)/1e6 = 0.0012; a3 (500 + 200)/1e6 = 0.0007;
    # b1 (100 + 20)/1e6 = 0.00012; b2 (200 + 40)/1e6 = 0.00024 -> 0.00226
    assert cost == {
        "n": 8,
        "currency": "USD",
        "known_cost_usd": "0.0022600000",
        "computed_cost_calls": 4,
        "unknown_cost_calls": 4,
        "unknown_cost_by_reason": {"price_missing": 2, "usage_missing": 2, "usage_inconsistent": 0},
        "cost_complete": False,
        "known_cost_per_call_usd": "0.0005650000",  # M12: 0.00226 / 4
    }
    assert isinstance(cost["known_cost_usd"], str)  # never a float
    assert inf["cost"]["by_provenance"]["real"]["cost_complete"] is True
    assert inf["cost"]["by_provenance"]["unknown"]["known_cost_usd"] is None  # not "0"
    assert inf["price_versions"] == {
        "items": [{"provider": provider, "model": PRICED, "price_version": version, "calls": 4}],
        "truncated": False,
    }
    assert inf["usage_source"]["total"] == {"provider_reported": 6, "estimated": 1, "unknown": 1}


def test_the_database_keeps_unknown_cost_as_null(api_client, loaded, db):
    a, _, _ = loaded
    with db.connect() as c:
        rows = c.exec_driver_sql(
            "SELECT cost_status, cost_usd FROM usage_records WHERE project_id = %s",
            (a.project_id,),
        ).all()
    assert {(s, v is None) for s, v in rows} == {("computed", False), ("unknown", True)}
    assert all(v is None for s, v in rows if s == "unknown")


# ---------------------------------------------------------------- latency (M06, M07, T24)
def linear_percentile(values: list[int], p: float) -> Fraction:
    """NumPy's `linear` method (Hyndman–Fan type 7), which PostgreSQL `percentile_cont`
    also implements: h = (n − 1)·p, x[⌊h⌋] + (h − ⌊h⌋)·(x[⌊h⌋+1] − x[⌊h⌋]). Exact."""
    xs = sorted(values)
    h = (len(xs) - 1) * Fraction(str(p))
    lo = int(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


def ms(v: Fraction) -> float:
    return round(float(v / MS), 6)


def test_t24_inference_and_trace_latency_percentiles(api_client, loaded):
    a, _, _ = loaded
    o = overview(api_client, a.project_id)
    inference_ns = [d * MS for d in (80, 10, 20, 30, 40, 60, 50, 70)]
    lat = o["inference"]["latency"]["total"]
    assert lat["n"] == 8
    # hand-computed: p50 = 40 + 0.5·10 = 45; p95: h = 6.65 -> 70 + 0.65·10 = 76.5;
    # p99: h = 6.93 -> 79.3
    assert (lat["p50_ms"], lat["p95_ms"], lat["p99_ms"]) == (45.0, 76.5, 79.3)
    assert [lat[k] for k in ("p50_ms", "p95_ms", "p99_ms")] == [
        ms(linear_percentile(inference_ns, p)) for p in (0.5, 0.95, 0.99)
    ]
    assert lat["low_sample"] == {"p95": True, "p99": True}
    assert o["inference"]["latency"]["by_provenance"]["real"]["p50_ms"] == 40.0
    # M07: root spans of T7 (80), T1 (100), T2 (200), T3 (300), T4 (400); T5 has no root
    roots = o["traces"]["latency"]
    assert roots["traces_without_root"] == 1
    assert roots["total"]["n"] == 5
    root_ns = [d * MS for d in (80, 100, 200, 300, 400)]
    assert [roots["total"][k] for k in ("p50_ms", "p95_ms", "p99_ms")] == [
        ms(linear_percentile(root_ns, p)) for p in (0.5, 0.95, 0.99)
    ] == [200.0, 380.0, 396.0]  # fmt: skip


def test_t24_percentiles_match_the_linear_method_on_a_larger_sample(api_client, make_key, provider):
    """37 attempts with irregular durations (1..997 ms): SQL equals the exact linear method,
    and the low-sample flags follow n (p95 needs 20, p99 needs 100)."""
    k = make_key("metrics-percentiles")
    durations = [(i * 7919) % 997 + 1 for i in range(37)]
    spans = [
        sp(100 + i, 0x1000 + i, F + timedelta(minutes=i), d,
           attrs=inference(provider[0], resp_id=f"resp-p{i}", tin=1, tout=1,
                           call=call(100 + i), attempt=1))
        for i, d in enumerate(durations)
    ]  # fmt: skip
    assert post(api_client, body(request(*spans)), k.key).status_code == 200
    lat = overview(api_client, k.project_id)["inference"]["latency"]["total"]
    assert lat["n"] == 37
    expected = [ms(linear_percentile([d * MS for d in durations], p)) for p in (0.5, 0.95, 0.99)]
    assert [lat["p50_ms"], lat["p95_ms"], lat["p99_ms"]] == expected
    assert lat["low_sample"] == {"p95": False, "p99": True}


# ---------------------------------------------------------------- T29: empty aggregation
def _assert_empty(o: dict) -> None:
    traces, inf = o["traces"], o["inference"]
    assert traces["count"]["total"] == 0 and inf["attempts"]["total"] == 0
    assert traces["error_rate"]["total"] == {"n": 0, "error_traces": 0, "rate": None}
    assert traces["latency"]["total"]["p95_ms"] is None
    assert inf["errors"]["total"]["rate"] is None
    assert inf["logical_calls"]["total"]["value"] is None
    lat = inf["latency"]["total"]
    assert lat["n"] == 0 and lat["p50_ms"] is None and lat["p99_ms"] is None
    tokens = inf["tokens"]["total"]
    assert tokens["input_tokens"] is None and tokens["output_tokens"] is None
    cost = inf["cost"]["total"]
    assert cost["known_cost_usd"] is None and cost["known_cost_per_call_usd"] is None
    assert cost["cost_complete"] is None
    assert inf["price_versions"]["items"] == []
    for bucket in inf["latency"]["by_provenance"].values():
        assert bucket["n"] == 0 and bucket["p50_ms"] is None


def test_t29_empty_project_returns_n_zero_and_nulls(api_client, make_key):
    k = make_key("metrics-empty")
    o = overview(api_client, k.project_id)
    _assert_empty(o)
    assert all(b["inference_attempts"] == 0 for b in o["trend"]["items"])
    r = get(api_client, "/api/v1/models/compare", project=k.project_id, **WINDOW)
    assert r.status_code == 200 and r.json()["groups"] == []


def test_t29_window_without_data_is_empty_too(api_client, loaded):
    a, _, _ = loaded
    r = get(
        api_client,
        "/api/v1/overview",
        project=a.project_id,
        **{"from": "2026-10-10T00:00:00Z", "to": "2026-10-11T00:00:00Z"},
    )
    assert r.status_code == 200
    _assert_empty(r.json())


# ---------------------------------------------------------------- isolation, windows, filters
def test_projects_never_see_each_other(api_client, loaded):
    a, b, _ = loaded
    other = overview(api_client, b.project_id)
    assert other["inference"]["attempts"]["total"] == 1
    assert other["inference"]["tokens"]["total"]["input_tokens"] == 7777
    mine = overview(api_client, a.project_id)
    assert mine["inference"]["tokens"]["total"]["input_tokens"] == 2160  # 7777 not included


def test_unknown_project_is_the_same_404_as_the_trace_api(api_client, loaded):
    r1 = get(api_client, "/api/v1/overview", project=999_999)
    r2 = get(api_client, "/api/v1/models/compare", project=999_999)
    r3 = get(api_client, f"/api/v1/traces/{tid(1)}", project=999_999)
    assert r1.status_code == r2.status_code == r3.status_code == 404
    assert r1.content == r2.content == r3.content


def test_window_boundaries_are_inclusive_start_exclusive_end(api_client, loaded):
    a, _, _ = loaded
    # [W.from + 1 ns ... ) drops T7 (at W.from); extending `to` by one second adds T8
    later = overview_with(api_client, a.project_id, "2026-10-25T00:00:00.000001Z", WINDOW["to"])
    assert later["traces"]["count"]["total"] == 5
    wider = overview_with(api_client, a.project_id, WINDOW["from"], "2026-10-26T00:00:01Z")
    assert wider["inference"]["attempts"]["total"] == 9
    assert wider["window"] == {
        "from": "2026-10-25T00:00:00.000000Z",
        "to": "2026-10-26T00:00:01.000000Z",
    }


def overview_with(client, project, start, end) -> dict:
    r = get(client, "/api/v1/overview", project=project, **{"from": start, "to": end})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    ("params", "attempts", "traces"),
    [
        ({"model": UNPRICED}, 2, 2),  # e1, d1 / T7, T4
        ({"status": "error"}, 1, 1),  # a2 / T1
        ({"status": "ok"}, 7, 5),
        ({"prompt_version": "v1"}, 3, 1),  # a1, a2, a3 / T1
        ({"prompt_version": "v2"}, 2, 1),  # b1, b2 / T2
        ({"provenance": "synthetic"}, 7, 3),
        ({"provenance": "unknown"}, 0, 2),
    ],
)
def test_filters_apply_to_each_metric_level(api_client, loaded, params, attempts, traces):
    a, _, _ = loaded
    o = overview(api_client, a.project_id, **params)
    assert o["inference"]["attempts"]["total"] == attempts
    assert o["traces"]["count"]["total"] == traces
    assert {k: v for k, v in o["filters"].items() if v is not None} == params


def test_reexport_changes_no_metric(api_client, loaded):
    a, _, (p, _) = loaded
    before = overview(api_client, a.project_id)
    assert post(api_client, body(request(*dataset(p))), a.key).status_code == 200
    assert overview(api_client, a.project_id) == before


# ---------------------------------------------------------------- trend
def test_trend_buckets_add_up_to_the_totals(api_client, loaded):
    a, _, _ = loaded
    o = overview(api_client, a.project_id)
    trend = o["trend"]
    assert trend["bucket"] == "hour" and len(trend["items"]) == 24
    assert trend["items"][0]["start"] == "2026-10-25T00:00:00.000000Z"
    assert sum(i["inference_attempts"] for i in trend["items"]) == 8
    assert sum(i["traces"] for i in trend["items"]) == 6
    assert sum(i["failed_attempts"] for i in trend["items"]) == 1
    hour1 = trend["items"][1]
    assert hour1["known_cost_usd"] == "0.0019000000" and hour1["unknown_cost_calls"] == 1
    assert trend["items"][3]["known_cost_usd"] is None  # T3: no inference, unknown not 0
    known = sum(Decimal(i["known_cost_usd"]) for i in trend["items"] if i["known_cost_usd"])
    assert known == Decimal(o["inference"]["cost"]["total"]["known_cost_usd"])
    week = {"from": "2026-10-20T00:00:00Z", "to": "2026-10-27T00:00:00Z"}
    weekly = get(api_client, "/api/v1/overview", project=a.project_id, **week).json()
    assert weekly["trend"]["bucket"] == "day" and len(weekly["trend"]["items"]) == 7


# ---------------------------------------------------------------- M15: models/compare
def test_m15_groups_by_provider_models_and_prompt_version(api_client, loaded):
    a, _, (p, _) = loaded
    r = get(api_client, "/api/v1/models/compare", project=a.project_id, limit=4, **WINDOW)
    assert r.status_code == 200
    out = r.json()
    keys = [(g["requested_model"], g["response_model"], g["prompt_version"]) for g in out["groups"]]
    assert keys == [
        (PRICED, PRICED, "v1"),  # a1, a3
        (PRICED, PRICED, "v2"),  # b1, b2
        (UNPRICED, UNPRICED, None),  # e1, d1
        (PRICED, None, "v1"),  # a2 (failed: no response model)
    ]
    assert out["groups_truncated"] is True  # d2's group is beyond the limit
    first = out["groups"][0]
    assert first["provider"] == p and first["attempts"] == 2
    assert first["errors"] == {"failed": 0, "succeeded": 2, "rate": 0.0}
    assert first["latency"]["p50_ms"] == 20.0  # (10 + 30) / 2
    assert first["cost"]["known_cost_usd"] == "0.0019000000"
    assert first["cost"]["cost_complete"] is True
    assert out["groups"][1]["by_provenance"] == {"real": 1, "synthetic": 1, "unknown": 0}
    unpriced = out["groups"][2]["cost"]
    assert unpriced["known_cost_usd"] is None and unpriced["unknown_cost_calls"] == 2
    assert unpriced["unknown_cost_by_reason"]["price_missing"] == 2
    full = get(api_client, "/api/v1/models/compare", project=a.project_id, **WINDOW).json()
    assert len(full["groups"]) == 5 and full["groups_truncated"] is False
    assert sum(g["attempts"] for g in full["groups"]) == 8


# ---------------------------------------------------------------- access and validation
@pytest.mark.parametrize("path", ["/api/v1/overview", "/api/v1/models/compare"])
def test_auth_runs_before_validation_and_queries(api_client, path):
    r = api_client.get(path, params={"project": "not-a-number", "from": "garbage"})
    assert r.status_code == 401
    assert r.json() == {
        "error": {"code": "unauthorized", "message": "missing or invalid credentials"}
    }
    wrong = api_client.get(path, params={"project": 1}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"from": "2026-10-25T00:00:00"}, "from"),  # naive
        ({"from": "2026-10-25T00:00:00+03:00"}, "from"),  # not UTC
        ({"from": "2026-09-01T00:00:00Z", "to": "2026-10-25T00:00:00Z"}, "from"),  # > 31 days
        ({"from": "2026-10-25T00:00:00Z", "to": "2026-10-25T00:00:00Z"}, "from"),  # empty
        ({"status": "failed"}, "status"),
        ({"provenance": "verified"}, "provenance"),
        ({"model": "m" * 257}, "model"),
        ({"prompt_version": "v" * 65}, "prompt_version"),
    ],
)
def test_invalid_parameters_are_422_without_echoing_values(api_client, loaded, params, field):
    a, _, _ = loaded
    r = get(api_client, "/api/v1/overview", project=a.project_id, **params)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_request" and err["fields"] == [field]
    for value in params.values():
        assert value not in r.text


@pytest.mark.parametrize("limit", [0, 201, "x"])
def test_compare_limit_is_bounded(api_client, loaded, limit):
    a, _, _ = loaded
    r = get(api_client, "/api/v1/models/compare", project=a.project_id, limit=limit)
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["limit"]


def test_responses_carry_no_content_or_credentials(api_client, loaded):
    a, _, _ = loaded
    text = get(api_client, "/api/v1/overview", project=a.project_id, **WINDOW).text
    text += get(api_client, "/api/v1/models/compare", project=a.project_id, **WINDOW).text
    for forbidden in (a.key, a.key.split("_", 2)[2], "resp-a1", tid(1), sid(0x11), call(1)):
        assert forbidden not in text  # no keys, no response or trace IDs, no call IDs


@pytest.mark.parametrize("path", ["/api/v1/overview", "/api/v1/models/compare"])
def test_the_api_bucket_limits_the_aggregate_endpoints(ingest_settings, make_key, path):
    """T43 for the new endpoints: the shared `/api/v1` bucket answers 429 before any query."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    k = make_key("metrics-ratelimit")
    tight = ingest_settings.model_copy(
        update={"rate_limit_api_rps": 0.001, "rate_limit_api_burst": 2}
    )
    with TestClient(create_app(tight)) as c:
        codes = [get(c, path, project=k.project_id).status_code for _ in range(3)]
        r = get(c, path, project=k.project_id)
    assert codes == [200, 200, 429]
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert r.json()["error"]["code"] == "rate_limited"
