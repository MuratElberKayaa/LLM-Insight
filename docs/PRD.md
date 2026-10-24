# LLM Insight — Product Requirements (MVP)

Decision source: `docs/MVP_MASTER_PLAN.md` (ADR-MVP-1.0). Target demo date: 2026-11-07. Progress is governed by acceptance gates, not by dates.

## 1. Problem

Teams shipping LLM features usually can't answer a few basic questions with evidence:
- Which call was slow or failed, and why might that be?
- What did it cost? Is that number actually known?
- Did a prompt change make quality better or worse?

Generic APM tools don't model tokens, cost provenance, or prompt versions. Hosted LLM observability tools often capture raw prompts by default.

## 2. Target users

- **Primary:** an AI/backend engineer instrumenting a Python LLM service, who wants traces, token/cost provenance and a regression loop.
- **Secondary:** a reviewer or tech lead who inspects failures and approves regression test cases.
- **Demo audience:** AI-engineering / infrastructure portfolio reviewers.

## 3. Value proposition and narrative: Observe → Diagnose → Evaluate → Improve

1. **Observe:** real OTel traces with latency, model, tokens, cost and status, plus the provenance of each number.
2. **Diagnose:** in Failure Review, a human records evidence and a *hypothesis*. The tool never claims a root cause.
3. **Evaluate:** a human-approved, sanitized test case is added to a dataset. Checks are deterministic, using JSON Schema and golden rules.
4. **Improve:** prompt versions A and B are compared on the same dataset, with N, pass rate, latency and cost shown, and real or synthetic results labeled.

## 4. Scope

### P0 (required for the demo)

| Area | Requirement |
|---|---|
| Ingest | OTLP/HTTP binary protobuf `POST /v1/traces`, project key, limits, idempotent spans (see `TELEMETRY_CONTRACT.md`) |
| SDK | Independent Python package. HTTP root span plus a CLIENT inference span. OpenAI adapter and deterministic mock. Fail-open export |
| Usage & cost | Provider-reported tokens with their subsets, a versioned price table, Decimal cost, and `unknown` cost as NULL plus status. Idempotent ledger |
| Dashboard | KPIs with N; trace list with filters (date, model, status, prompt_version); span tree; p50/p95/p99; error rate; cost and calls charts; provenance badges |
| Failure Review | Trace-linked record with `issue_type`, `evidence`, `hypothesis`, `review_status` and `reviewer_decision` |
| Trace-to-Testset | Saved only with human approval, using sanitized or synthetic input |
| Offline evaluation | JSON Schema and golden match. Two `prompt_version`s on the same dataset. Real and mock results separated |
| Quality bar | At least 24 critical automated tests (39 starred, CR-04), 48-scenario target matrix, README, demo, architecture diagram |
| Security | Metadata-only, allowlist, hashed keys, server-only admin token, local-only binding |

### P1 (only if P0 is complete)
- Streaming time-to-first-chunk, with first-meaningful-content as a separate measure, and streaming cancel.
- Threshold alerts for cost, error rate and latency.
- Safe CSV export.
- Retention job.
- User feedback.
- A second provider adapter or an OpenInference normalizer, without breaking the existing schema.

### P2 (post-MVP)
ClickHouse scale-out, a Collector with a disk queue, a separate evaluation worker, LLM-as-judge, RAG metrics, multi-tenant and RBAC.

## 5. Non-goals (MVP)

- Infrastructure: ClickHouse, Redis, S3, Kafka, Kubernetes, a mandatory Collector.
- Product scope: multi-tenant SaaS, billing, self-hosted LLM, full RAG, automatic agent orchestration, automatic LLM-as-judge.
- Automatic root-cause claims. Billing-grade cost accuracy. Guaranteed delivery (no persistent queue).
- Production or internet exposure, complex RBAC, a mobile app.
- Implementing all 64 metrics from the earlier report.

## 6. Success criteria for the demo

- One reproducible 3–5 minute run of `DEMO_SCRIPT.md` from `docker compose up --build`.
- 0 FAIL on the 39 critical tests. Every one of the 48 is reported as PASS, FAIL or SKIP with a reason.
- No fabricated numbers: every KPI is backed by DB rows, and synthetic data is labeled.
