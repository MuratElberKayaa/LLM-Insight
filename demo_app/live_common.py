"""Provider-neutral pieces of the live runners (`live_openai`, `live_gemini`): the backend
readiness probe, the dashboard links and the one-line JSON output. No provider import."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, TextIO

import llm_insight_sdk as lli
from llm_insight_sdk.config import PROJECT_KEY_HEADER

DEFAULT_DASHBOARD_URL = "http://127.0.0.1:3001"
READINESS_TIMEOUT_SECONDS = 3.0


def check_backend(config: lli.LLMInsightConfig) -> dict[str, Any]:
    """POST an empty OTLP request: the backend answers 200 and writes nothing when it is up
    and accepts the project key (TELEMETRY_CONTRACT.md §1.1 step 12)."""
    request = urllib.request.Request(  # noqa: S310 - the endpoint is validated by LLMInsightConfig
        config.endpoint,
        data=b"",
        method="POST",
        headers={
            "Content-Type": "application/x-protobuf",
            PROJECT_KEY_HEADER: config.project_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=READINESS_TIMEOUT_SECONDS) as response:  # noqa: S310
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = exc.code
    except (urllib.error.URLError, OSError):
        return {"ready": False, "reason": "backend_unreachable"}
    if status == 200:
        return {"ready": True, "reason": "ready"}
    if status == 401:
        return {"ready": False, "reason": "project_key_rejected"}
    return {"ready": False, "reason": f"backend_http_{status}"}


def dashboard_links(base: str, trace_id: str, env: Mapping[str, str]) -> dict[str, str]:
    project = (env.get("LLMI_DASHBOARD_PROJECT_ID") or "").strip()
    query = f"?project={project}" if project.isdigit() else ""
    return {
        "dashboard_url": f"{base.rstrip('/')}/traces/{trace_id}{query}",
        "api_path": f"/api/v1/traces/{trace_id}",
    }


def emit(out: TextIO, doc: dict[str, Any]) -> None:
    out.write(json.dumps(doc, sort_keys=True) + "\n")
