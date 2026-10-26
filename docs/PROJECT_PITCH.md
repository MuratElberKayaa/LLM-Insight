# LLM Insight — Project Pitch

Portfolio summary of the LLM Insight MVP. Every technical claim below is implemented and tested in this repository; the evidence is in `FINAL_DELIVERY_REPORT.md` and the root `README.md`. There are no customers, no production deployment and no paid provider usage: this is a local engineering MVP, and all demo and experiment data is SYNTHETIC.

## One-paragraph pitch

LLM Insight is a local, verifiable observability and evaluation MVP for LLM applications, built around one loop: **Observe → Diagnose → Evaluate → Improve**. An independent Python SDK emits OpenTelemetry GenAI client spans that carry metadata only, never prompts or completions. A FastAPI backend ingests them over real OTLP/HTTP binary protobuf and stores them idempotently in PostgreSQL. It prices provider-reported token usage with a versioned `Decimal` catalogue, and an unknown cost stays unknown instead of becoming `$0`. A Next.js dashboard lets an engineer explore traces and read automatic diagnostics that are labelled unverified. They can record a human failure review whose cause is stated as a hypothesis, and turn a reviewed trace into a human-approved regression test case. Two prompt versions can then be compared on a golden dataset with deterministic rules, in a mock-only, clearly labelled synthetic A/B experiment.

## Problem

Teams that ship LLM features often cannot answer basic questions with evidence:

- Which call was slow or failed, and what might explain it?
- What did it cost, and is that number actually known?
- Did a prompt change make quality better or worse, measured on the same cases?

Generic APM tools do not model tokens, cost provenance or prompt versions. Many hosted LLM observability tools capture raw prompts by default. Ad-hoc dashboards also tend to mix real and synthetic numbers, and to show missing costs as zero.

## Solution

| Step | What LLM Insight does |
|---|---|
| Observe | OTLP/HTTP protobuf ingest (`POST /v1/traces`) with authentication, size, span-count and rate limits. Idempotent span and usage ledger. Overview KPIs show the sample size `n`, p50/p95 latency, error rate, and known cost next to the count of calls whose cost is unknown. Trace explorer with a span tree and waterfall. |
| Diagnose | Deterministic, metadata-only machine observations per trace, labelled "Automatic · Unverified · Not a human review". A human failure review records issue type, evidence and a **hypothesis**, never a verified root cause. |
| Evaluate | Trace-to-testset: a reviewed trace becomes a regression test case only with explicit human approval. The person types a synthetic input, and server-side redaction runs before insert. A deterministic rule engine supports a restricted JSON Schema subset, field, exact and RE2-only regex rules. |
| Improve | A mock-only A/B run of two prompt versions on identical case ids. It reports the pass rate with its denominator, synthetic latency, and estimated cost or "unknown". A better version is named only on complete paired evidence. |

## Core differentiators

- **Real OTLP, not a look-alike.** The ingest endpoint decodes `opentelemetry-proto` `ExportTraceServiceRequest` messages and answers with protobuf `ExportTraceServiceResponse` or `google.rpc.Status`. The official Python OTLP exporter works against it unchanged.
- **Unknown is never zero.** Missing usage keeps NULL tokens and `cost_status` `unknown` with a reason. Costs are exact `Decimal` values from a versioned, append-only price catalogue, and a price change never rewrites history.
- **Idempotent by construction.** Re-exporting the same spans changes no row counts and no cost. Retries are separate physical attempts, and double instrumentation of one provider response produces one usage row.
- **Privacy by default.** Telemetry is metadata only: an attribute allowlist, an always-deny list, and value checks for credentials and personal-data shapes. Ingest keys are stored as HMACs. The admin token never reaches the browser.
- **Honest provenance.** Every number is labelled `real`, `synthetic`, `mixed` or `unknown`. Mixed and unknown data are never counted as real, and the dashboard shows SYNTHETIC banners wherever synthetic data appears.
- **Human in the loop where it matters.** Diagnostics never fill in a review. A test case cannot be stored without an explicit approval. Experiment results trace back to the review and trace they came from.

## Implemented technical features

- OTLP/HTTP binary protobuf receiver:
  - gzip with a bounded inflater;
  - 4 MiB wire, 16 MiB decompressed and 2048-span limits (`413`);
  - atomic batches;
  - `200` only after COMMIT;
  - project ingest keys with constant-time HMAC verification and revocation.
- PostgreSQL schema through five Alembic migrations, with database triggers for review integrity, append-only experiment results and synthetic-only mock results.
- Independent Python SDK with:
  - a CLIENT inference span per physical attempt;
  - `gen_ai.*` attributes and provenance;
  - a bounded batch exporter that fails open when the backend is down;
  - a deterministic mock provider;
  - an OpenAI Chat Completions adapter, tested offline only with fake transports and a network block.
- Aggregate metrics: request and inference counts, error rates, p50/p95/p99 with `n` (SQL `percentile_cont`, cross-checked against NumPy), known cost and unknown-cost counts, model comparison.
- Next.js dashboard: overview, model comparison, trace explorer, trace detail with span tree and waterfall, diagnostics panel, review and decision forms, datasets, approved test cases, and experiment setup, results and per-case pages.
- Dashboard security:
  - server-side backend access only;
  - five narrow Server Actions behind a loopback `Host` check and same-origin/CSRF checks;
  - a nonce-based CSP;
  - a bundle scan that proves the admin token never reaches the client.
- Rate limiting with `429` and `Retry-After` on ingest, authentication failures and every `/api/v1` route (in-process token buckets).

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI (modular monolith, one worker), SQLAlchemy, Alembic, `opentelemetry-proto` |
| Database | PostgreSQL 17 (Docker Compose) |
| SDK | Python, OpenTelemetry SDK 1.45.1 and OTLP/HTTP protobuf exporter |
| Dashboard | Next.js 16, React 19, TypeScript (strict), Tailwind CSS 4, Recharts |
| Evaluation | Deterministic rules: restricted JSON Schema 2020-12 subset, field/exact rules, RE2 regexes |
| Quality | pytest (unit + real PostgreSQL integration), Node test runner, Playwright (Edge), Ruff, strict mypy, ESLint, GitHub Actions on Ubuntu 24.04 |
| Packaging | Docker Compose (PostgreSQL by default; opt-in `app` profile for backend and dashboard containers) |

## Engineering challenges solved

- **Protocol fidelity.** The backend implements a documented binary-protobuf subset of OTLP 1.11.1 with the specified status codes and error-detail encoding, and stays interoperable with the standard exporter.
- **Correct cost accounting.** Cached-input and reasoning-output tokens are subsets, priced once and never added on top. Usage is keyed per physical attempt, with response-ID-first deduplication keys. The cost tests compare stored values against hand-computed `Decimal` results.
- **Concurrency-safe idempotency.** Concurrent identical exports and concurrent double instrumentation each store exactly one row, enforced by database constraints rather than by application checks alone.
- **Safe evaluation of untrusted rules.** RE2-only regexes run in linear time with program and memory caps. The JSON Schema subset can never fetch a remote document and has a keyword budget. Experiment runs are bounded per process (4 concurrent runs, 15 s per run).
- **Server Action hardening.** Live replay and raw-socket probes found two cases where a same-origin Server Action POST could run on a path the request guard skipped (`/favicon.ico` and `/_next/static` look-alikes). Both were fixed and are covered by regression tests. A final probe of 1339 requests found no other dispatch path.
- **Evidence-based acceptance.** A 48-scenario test matrix (42 critical, 10 presentation blockers) passes. Each result is backed by named tests and a phase report, and the clean-clone demo was reproduced from the documented commands.

## Privacy and provenance boundaries

- Prompts, completions and message content are never stored from telemetry. The redaction and value checks are heuristics, not a privacy guarantee: test-case input must be synthetic.
- Provenance is asserted by the client. The server downgrades inconsistent `real` claims, but it cannot prove that a span came from a real provider.
- Reviewer and approver names are self-declared labels. There is no login and no verified identity.
- The MVP is authorized for local use only (loopback binds). Public or multi-user deployment would first need real authentication, TLS, per-project authorization, a distributed rate limiter, audit logging and a privacy review.

## Offline synthetic evaluation limitations

- Experiments run in mock mode only. The backend refuses real-provider mode and has no real executor.
- Outputs, token counts and durations come from a scripted mock and say nothing about how a real model would behave.
- Costs are estimates of synthetic token counts at catalogue prices, not provider invoices. Models without a verified price show "unknown".
- The OpenAI adapter is tested offline only. **No real OpenAI call has been made by this project.**
- The golden dataset has 12 synthetic cases. Pass rates come from small samples, and no statistical significance is computed.

## CV-friendly project description

**LLM Insight — LLM observability and evaluation MVP (Python, FastAPI, PostgreSQL, OpenTelemetry, Next.js).** Designed and built a local observability platform for LLM applications:

- an OTLP/HTTP protobuf trace receiver;
- an independent OpenTelemetry Python SDK with an offline-tested OpenAI adapter;
- an idempotent usage ledger with versioned `Decimal` pricing in which unknown cost is never shown as zero;
- a Next.js dashboard with a trace explorer, human failure review, human-approved trace-to-testset and deterministic mock A/B prompt evaluation.

Metadata-only telemetry; 48-scenario acceptance matrix; 2,442 automated tests (backend 1,726, SDK 350, frontend unit 311, browser end-to-end 55), including real-PostgreSQL integration and Playwright end-to-end tests; GitHub Actions CI.

## LinkedIn / GitHub description

> **LLM Insight** — local, privacy-first observability and evaluation for LLM apps. Real OTLP/HTTP protobuf ingest, a metadata-only OpenTelemetry SDK, idempotent token/cost ledger (unknown cost is never $0), trace explorer, human-reviewed failure hypotheses, approved regression test cases and deterministic, clearly labelled synthetic A/B prompt experiments. FastAPI · PostgreSQL · OpenTelemetry · Next.js. Local MVP; no real-provider runs.

GitHub "About" (short): *Local, metadata-only LLM observability MVP: OTLP protobuf ingest, Decimal cost ledger, trace explorer, human review → approved test cases → deterministic synthetic A/B evaluation.*
