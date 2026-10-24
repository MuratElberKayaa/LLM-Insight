"""R08 end to end (Phase 1 final): synthetic, non-working secrets and personal data injected into
every permitted string field of a real OTLP protobuf request (plain and gzip) never reach the
database, the read API or the logs, while valid GenAI metadata in the same batch is kept.

All telemetry is SYNTHETIC; secrets are assembled at runtime (see test_metadata_privacy.py).
"""

import logging

import pytest
from opentelemetry.proto.trace.v1.trace_pb2 import Span as SpanPb
from opentelemetry.proto.trace.v1.trace_pb2 import Status as SpanStatus

from tests.integration.conftest import ADMIN_AUTH
from tests.integration.ingest_helpers import counts, post, rows
from tests.otlp import FIXTURE_TRACE_ID, MS, T0, body, inference_attrs, kvs, multi, span

pytestmark = pytest.mark.integration

S = {  # one distinctive synthetic secret per carrier field
    "name": "sk-" + "proj-" + "NAME" * 6,
    "req_model": "sk-" + "ant-" + "REQM" * 6,
    "resp_model": "AKIA" + "RESPMODEL" + "0123456",
    "error_type": "ghp_" + "ERRT" * 6,
    "exc_type": "github_pat_" + "EXCT" * 6,
    "prompt_version": "lli_" + "PrVr1234" + "_" + "pv" * 10,
    "prompt_name": "AIza" + "PRNM" * 9,
    "route": "token=" + "ROUTE" * 3,
    "response_id": "sk-" + "RESPID" * 4,
    "call_id": "sk-" + "CALLID" * 4,
    "fingerprint": "xox" + "p-" + "FINGERPRINT-99",
    "server_address": "admin:" + "SRVPASS" + "@api.example.com",
    "service_name": "Bearer " + "SVCNAME" + "4567890abc",
    "service_version": "AKIA" + "SVCVERSION" + "123456",
    "environment": "ops.team" + "@" + "corp-example.com",
    "namespace": "+90 5" + "55 444 33 22",
    "scope_name": "eyJ" + "SCOPENAMEhdr0" + "." + "SCOPEBODY00a",
    "scope_version": "sk-" + "SCOPEVER" * 3,
    "finish": "sk-" + "FINISHREASON" * 2,
}


def needles():
    # A distinctive fragment of every injected value (the whole value for short ones).
    return [v[-12:] if len(v) > 12 else v for v in S.values()]


def adversarial_request():
    bad_attrs = {
        **inference_attrs(
            gen_ai__request__model=f"ft:gpt-4o:acme:{S['req_model']}",
            gen_ai__response__model=S["resp_model"],
            gen_ai__response__id=S["response_id"],
            llminsight__call_id=S["call_id"],
            gen_ai__prompt__version=S["prompt_version"],
        ),
        "gen_ai.prompt.name": S["prompt_name"],
        "error.type": S["error_type"],
        "openai.response.system_fingerprint": S["fingerprint"],
        "server.address": S["server_address"],
        "gen_ai.response.finish_reasons": ["stop", S["finish"]],
    }
    bad = span(
        span_id="1" * 16,
        name=f"chat {S['name']}",
        kind=SpanPb.SPAN_KIND_CLIENT,
        attrs=bad_attrs,
        status_code=SpanStatus.STATUS_CODE_ERROR,
    )
    ev = bad.events.add(name="exception", time_unix_nano=T0)
    ev.attributes.extend(kvs({"exception.type": S["exc_type"]}))
    root = span(
        span_id="2" * 16,
        name="POST /v1/summarize",
        kind=SpanPb.SPAN_KIND_SERVER,
        attrs={"http.route": f"/v1/{S['route']}", "http.request.method": "POST"},
    )
    good = span(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="3" * 16,
        name="chat gpt-4o-mini",
        kind=SpanPb.SPAN_KIND_CLIENT,
        start=T0 + MS,
        attrs={
            **inference_attrs(
                gen_ai__response__id="chatcmpl-VALID-0001",
                gen_ai__request__max_tokens=512,
                gen_ai__usage__cache_read__input_tokens=40,
                gen_ai__usage__reasoning__output_tokens=5,
            ),
            "gen_ai.response.finish_reasons": ["stop"],
            "server.address": "api.openai.com",
            "openai.response.system_fingerprint": "fp_44709d6fcb",
        },
    )
    bad_resource = {
        "service.name": S["service_name"],
        "service.version": S["service_version"],
        "deployment.environment.name": S["environment"],
        "service.namespace": S["namespace"],
        "telemetry.sdk.language": "python",
    }
    good_resource = {"service.name": "demo-summarizer", "service.version": "1.4.2"}
    return multi(
        (bad_resource, [((S["scope_name"], S["scope_version"]), [bad, root])]),
        (good_resource, [(("llm_insight_sdk", "0.1.0"), [good])]),
    )


def dump_database(db) -> str:
    out = []
    for t in ("projects", "project_api_keys", "traces", "spans", "usage_records"):
        out += [r["j"] for r in rows(db, f"SELECT row_to_json(x)::text AS j FROM {t} AS x")]  # noqa: S608
    return "\n".join(out)


@pytest.mark.parametrize("gzipped", [False, True], ids=["identity", "gzip"])
def test_r08_injected_secrets_never_reach_db_api_or_logs(api_client, make_key, db, caplog, gzipped):
    caplog.set_level(logging.DEBUG)  # every logger, every level
    k = make_key()
    headers = {"Content_Encoding": "gzip"} if gzipped else {}
    r = post(api_client, body(adversarial_request(), gzipped=gzipped), k.key, **headers)
    assert r.status_code == 200
    assert counts(db) == (2, 3, 2)

    window = {"from": "2026-10-24T00:00:00Z", "to": "2026-10-25T00:00:00Z"}
    listing = api_client.get(
        "/api/v1/traces", params={"project": k.project_id, **window}, headers=ADMIN_AUTH
    )
    details = [
        api_client.get(f"/api/v1/traces/{t}", params={"project": k.project_id}, headers=ADMIN_AUTH)
        for t in (FIXTURE_TRACE_ID, "4bf92f3577b34da6a3ce929d0e0e4736")
    ]
    assert listing.status_code == 200 and all(d.status_code == 200 for d in details)

    dump = dump_database(db)
    for needle in needles():
        assert needle not in dump, needle
        assert needle not in listing.text, needle
        assert all(needle not in d.text for d in details), needle
        assert needle not in caplog.text, needle
        assert needle not in r.text, needle

    # The adversarial span is kept with documented fallbacks, its secrets withheld and counted.
    bad = rows(db, "SELECT * FROM spans WHERE span_id = :s", s="1" * 16)[0]
    assert bad["name"] == "redacted_span"
    assert (bad["scope_name"], bad["scope_version"]) == (None, None)
    assert bad["error_type"] is None and bad["status_message"] is None
    assert bad["resource_attributes"] == {"telemetry.sdk.language": "python"}
    assert set(bad["attributes"]) == {
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "llminsight.attempt",
        "llminsight.provenance",
        "llminsight.usage_source",
    }
    # 10 span attributes + 4 resource attributes + name + scope name + scope version
    assert bad["server_dropped_attributes_count"] == 17
    (trace_a,) = rows(db, "SELECT * FROM traces WHERE trace_id = :t", t=FIXTURE_TRACE_ID)
    assert trace_a["service_name"] == "unknown_service"  # the unsafe service.name was dropped
    assert trace_a["prompt_version"] is None
    usage_bad = rows(db, "SELECT * FROM usage_records WHERE span_id = :s", s="1" * 16)[0]
    assert usage_bad["call_key_source"] == "span_fallback"  # no unsafe identity was used
    assert usage_bad["requested_model"] is None and usage_bad["response_model"] is None
    assert usage_bad["provider_response_id"] is None and usage_bad["call_id"] is None

    # Valid GenAI metadata in the same batch is untouched.
    good = rows(db, "SELECT * FROM spans WHERE span_id = :s", s="3" * 16)[0]
    assert good["name"] == "chat gpt-4o-mini" and good["server_dropped_attributes_count"] == 0
    assert (good["scope_name"], good["scope_version"]) == ("llm_insight_sdk", "0.1.0")
    assert good["attributes"]["gen_ai.request.max_tokens"] == 512
    assert good["attributes"]["gen_ai.response.finish_reasons"] == ["stop"]
    assert good["attributes"]["openai.response.system_fingerprint"] == "fp_44709d6fcb"
    usage_good = rows(db, "SELECT * FROM usage_records WHERE span_id = :s", s="3" * 16)[0]
    assert usage_good["canonical_call_key"] == "resp:openai:chatcmpl-VALID-0001"
    assert (usage_good["requested_model"], usage_good["response_model"]) == (
        "gpt-4o-mini",
        "gpt-4o-mini-2024-07-18",
    )
    assert (usage_good["input_tokens"], usage_good["output_tokens"]) == (100, 20)
    assert (usage_good["cached_input_tokens"], usage_good["reasoning_output_tokens"]) == (40, 5)
    assert (usage_good["cost_usd"], usage_good["cost_status"]) == (None, "unknown")
    detail_good = details[1].json()
    assert detail_good["trace"]["service_name"] == "demo-summarizer"
    assert detail_good["usage"][0]["response_model"] == "gpt-4o-mini-2024-07-18"


def test_r08_reexport_of_the_same_payload_stays_idempotent(api_client, make_key, db):
    """Redaction is deterministic, so a re-export is an identical span, not a conflict."""
    k = make_key()
    payload = body(adversarial_request())
    assert post(api_client, payload, k.key).status_code == 200
    assert post(api_client, payload, k.key).status_code == 200
    assert counts(db) == (2, 3, 2)
    stats = api_client.app.state.ingestor.stats
    assert (stats.spans_reexported, stats.span_content_conflicts) == (3, 0)
