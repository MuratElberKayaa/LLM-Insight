"""Import the completed local report via the authenticated local API; NEVER runs inference.

Inside the backend container: LLMI_ADMIN_TOKEN is already configured. Only rule booleans,
fixed identifiers and trace IDs leave this script. API derives timing/usage from PostgreSQL.
"""
import argparse
import json
import os
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--project", type=int, required=True)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not report["complete"] or len(report["cases"]) != 24:
        raise ValueError("complete 24-call evidence required")
    for c in report["cases"]:
        if not c["verified"] or c["project_id"] != args.project:
            raise ValueError("project or verification mismatch")
        if (c["validation"] == "passed") != all(c["rules"].values()):
            raise ValueError("inconsistent rule result")
    models = sorted({c["model"] for c in report["cases"]})
    if models != ["gemini-3.1-flash-lite", "openai/gpt-oss-20b"]:
        raise ValueError("unexpected model set")
    for model in models:
        body = {"run_id": report["run_id"], "model": model, "suite": report["suite"],
                "rule_version": "ticket-category-priority-v1", "cases": [
                    {k: c[k] for k in ("case_id", "variant", "trace_id", "rules")}
                    for c in report["cases"] if c["model"] == model]}
        req = urllib.request.Request(
            f"http://127.0.0.1:{args.port}/api/v1/imported-experiments?project={args.project}",
            data=json.dumps(body).encode(), method="POST", headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + os.environ["LLMI_ADMIN_TOKEN"]})
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
        print(json.dumps({"id": result["id"], "model": model,
                          "groups": result["groups"], "calls": len(result["cases"])}))


if __name__ == "__main__":
    main()
