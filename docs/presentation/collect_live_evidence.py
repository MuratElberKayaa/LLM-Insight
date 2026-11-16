"""Read-only local API reconciliation for the completed, approved prompt A/B run."""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from demo_app.live_prompt_ab import load_environment

P = ROOT / "docs/presentation"
RUN = ROOT / "docs/reports/prompt-ab/ee04815a042c4357a75c12107392f72a"
report = json.loads((RUN / "report.json").read_text(encoding="utf-8"))
assert report["complete"] and report["provider_requests_reserved"] == 24
events = [json.loads(s) for s in (RUN / "journal.jsonl").read_text().splitlines()]
iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
window = {"project": 1, "provenance": "real",
          "from": iso(min(e["at"] for e in events if e["event"] == "reserved") - 1),
          "to": iso(max(e["at"] for e in events) + 1)}
day = {"project": 1, "provenance": "real",
       "from": "2026-11-12T00:00:00Z", "to": "2026-11-13T00:00:00Z"}
env = load_environment(ROOT / ".env")
with httpx.Client(trust_env=False, timeout=15,
                  headers={"Authorization": "Bearer " + env["LLMI_BATCH_READ_TOKEN"]}) as client:
    def get(route, params):
        r = client.get("http://127.0.0.1:8010/api/v1/" + route, params=params)
        r.raise_for_status()
        return r.json()
    ab_models = get("models/compare", {**window, "limit": 200})
    ab_traces = get("traces", {**window, "limit": 100})
    overview = get("overview", day)
    models = get("models/compare", {**day, "limit": 200})

# Independent aggregate read-back must match all individual verified traces.
assert not ab_models["groups_truncated"] and len(ab_models["groups"]) == 4
assert {r["trace_id"] for r in ab_traces["items"]} == {r["trace_id"] for r in report["cases"]}
for group in report["groups"]:
    api = next(g for g in ab_models["groups"] if
               g["requested_model"] == group["model"] and
               g["prompt_version"] == "live-ticket-" + group["variant"])
    assert api["attempts"] == group["verified"] == 6
    assert api["errors"]["failed"] == 0
    assert api["cost"]["unknown_cost_calls"] == 6
    assert math.isclose(api["latency"]["p50_ms"], group["client_p50_ms"], abs_tol=.01)
    assert api["tokens"]["input_tokens"] == group["reported_input_tokens"]
    assert api["tokens"]["output_tokens"] == group["reported_output_tokens"]
assert not models["groups_truncated"]
by_model = {}
for g in models["groups"]:
    summary = by_model.setdefault(g["requested_model"], {"attempts": 0, "errors": 0})
    summary["attempts"] += g["attempts"]
    summary["errors"] += g["errors"]["failed"]
obs = {"captured_at": datetime.now(timezone.utc).isoformat(), "window": day,
       "attempts": overview["inference"]["attempts"]["total"],
       "errors": overview["inference"]["errors"]["total"]["failed"],
       "traces": overview["traces"]["count"]["total"], "models": by_model,
       "unknown_cost_calls": overview["inference"]["cost"]["total"]["unknown_cost_calls"],
       "ab_window": window, "ab_aggregate_verified": True}
for name, data in [("live-observation.json", obs), ("live-overview-api.json", overview),
                   ("live-models-api.json", models), ("live-ab-models-api.json", ab_models),
                   ("live-ab-traces-api.json", ab_traces)]:
    (P / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
urls = {"overview": "http://127.0.0.1:3001/?" + urlencode(day),
        "models": "http://127.0.0.1:3001/models?" + urlencode(window),
        "traces": "http://127.0.0.1:3001/traces?" + urlencode(window)}
(P / "live-capture-urls.json").write_text(json.dumps(urls, indent=2), encoding="utf-8")
print(json.dumps(obs, ensure_ascii=False, indent=2))
