import Link from "next/link";
import type { Diagnostics, EvidenceSpan, Finding } from "../../lib/api/diagnostics";
import type { ApiResult } from "../../lib/api/result";
import type { JsonValue } from "../../lib/api/review-types";
import { LosslessInt } from "../../lib/json";
import { formatDurationNs } from "../../lib/nanos";
import { ErrorState } from "../states";
import { Ident } from "../traces/trace-values";

/** A fact value as compact JSON text (rendered as a text node, never as markup). */
function factText(value: JsonValue): string {
  return JSON.stringify(value, (_k, v: unknown) => (v instanceof LosslessInt ? v.digits : v));
}

function humanize(code: string): string {
  return code.replaceAll("_", " ");
}

function EvidenceRow({ e, spanHref }: { e: EvidenceSpan; spanHref: (spanId: string) => string }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-2">
      <Link href={spanHref(e.span_id)} className="underline underline-offset-2" data-testid="diagnostic-evidence-link">
        <Ident value={e.span_id} />
      </Link>
      <span>
        {e.kind} · {e.status} · {e.role}
      </span>
      {e.error_type !== null && <span>error.type {e.error_type}</span>}
      <span>{formatDurationNs(BigInt(e.duration_ns))}</span>
    </li>
  );
}

function FindingItem({ f, spanHref }: { f: Finding; spanHref: (spanId: string) => string }) {
  return (
    <li className="rounded-md border border-dashed border-line bg-surface px-3 py-2" data-testid="diagnostic-finding" data-category={f.category}>
      <details>
        <summary className="cursor-pointer text-sm text-ink">
          <span className="font-medium">{humanize(f.category)}</span> <span className="text-xs text-ink-3">({f.group}, about the {humanize(f.subject.type)})</span>{" "}
          <span className="ml-1 rounded border border-line px-1 text-[10px] font-semibold uppercase tracking-wide text-ink-3">Unverified</span>
        </summary>
        <div className="mt-2 space-y-2 text-sm text-ink-2">
          <p>
            <span className="text-xs font-medium text-ink-3">Rule fired because: </span>
            {f.trigger}
          </p>
          {f.observed_facts.length > 0 && (
            <dl className="grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-[max-content_1fr]">
              {f.observed_facts.map((fact) => (
                <div key={fact.name} className="contents">
                  <dt className="text-ink-3">{humanize(fact.name)}</dt>
                  <dd className="break-all font-mono text-ink-2">{factText(fact.value)}</dd>
                </div>
              ))}
            </dl>
          )}
          <div>
            <p className="text-xs font-medium text-ink-3">
              Evidence spans ({f.evidence_total}
              {f.evidence_truncated ? `, first ${f.evidence.length} shown` : ""})
            </p>
            <ul className="mt-1 space-y-0.5">
              {f.evidence.map((e) => (
                <EvidenceRow key={e.span_id} e={e} spanHref={spanHref} />
              ))}
            </ul>
          </div>
          <p data-testid="diagnostic-hypothesis">
            <span className="text-xs font-medium text-ink-3">Possible explanation (not verified): </span>
            {f.hypothesis.statement}
            {f.hypothesis.error_interpretation && <span className="block text-xs text-ink-3">{f.hypothesis.error_interpretation.statement}</span>}
          </p>
          {f.not_established.length > 0 && (
            <div>
              <p className="text-xs font-medium text-ink-3">Not established by this data</p>
              <ul className="list-disc pl-5 text-xs">
                {f.not_established.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
          )}
          {(!f.data_complete || f.limitations.length > 0) && (
            <div>
              <p className="text-xs font-medium text-ink-3">Limitations{!f.data_complete && " (data incomplete)"}</p>
              <ul className="list-disc pl-5 text-xs">
                {f.limitations.map((l) => (
                  <li key={l.code}>{l.text}</li>
                ))}
              </ul>
            </div>
          )}
          {f.investigation_steps.length > 0 && (
            <div>
              <p className="text-xs font-medium text-ink-3">Things a reviewer could check</p>
              <ul className="list-disc pl-5 text-xs">
                {f.investigation_steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </details>
    </li>
  );
}

/**
 * Automatic diagnostics (Phase 4A) as optional, unverified supporting evidence for the human
 * reviewer. Visually separate from the review (dashed, muted, labeled), read-only, and never
 * used to fill in, approve or decide a review. A failed request degrades only this panel.
 */
export function DiagnosticsPanel({ result, spanHref }: { result: ApiResult<Diagnostics> | null; spanHref: (spanId: string) => string }) {
  return (
    <section aria-labelledby="diagnostics-heading" className="rounded-lg border border-dashed border-axis bg-surface/60 px-4 py-4" data-testid="diagnostics-panel">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 id="diagnostics-heading" className="text-base font-semibold text-ink">
          Machine observations
        </h2>
        <span className="rounded border border-axis px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-ink-3" data-testid="diagnostics-label">
          Automatic · Unverified · Not a human review
        </span>
      </div>
      <p className="mb-3 text-sm text-ink-3">
        Deterministic rules over stored metadata only: no prompt or completion content and no language model. Each possible explanation is unverified. These observations never fill
        in, approve or decide the human review.
      </p>
      {result === null ? (
        <p className="text-sm text-ink-3">Not loaded: the page has no valid project.</p>
      ) : !result.ok ? (
        <ErrorState error={result.error} resource="the trace diagnostics" compact />
      ) : (
        <>
          {!result.data.coverage.complete && (
            <p className="mb-2 text-xs text-warning" data-testid="diagnostics-incomplete">
              Analysis incomplete: {result.data.coverage.spans_analyzed} of {result.data.coverage.spans_stored} stored spans analysed.
            </p>
          )}
          {result.data.findings.length === 0 ? (
            <p className="text-sm text-ink-2" data-testid="diagnostics-empty">
              No rule fired on the stored metadata. That does not show the trace is healthy; see what is not assessed below.
            </p>
          ) : (
            <ul className="space-y-2" data-testid="diagnostics-findings">
              {result.data.findings.map((f) => (
                <FindingItem key={f.id} f={f} spanHref={spanHref} />
              ))}
            </ul>
          )}
          {result.data.findings_truncated && <p className="mt-2 text-xs text-ink-3">First {result.data.findings.length} of {result.data.findings_total} findings shown.</p>}
          <details className="mt-3 text-xs text-ink-3">
            <summary className="cursor-pointer">Not assessed by these rules</summary>
            <ul className="mt-1 list-disc pl-5">
              {result.data.not_assessed.map((n) => (
                <li key={n.category}>
                  <span className="text-ink-2">{humanize(n.category)}:</span> {n.reason}
                </li>
              ))}
            </ul>
          </details>
          <p className="mt-2 text-[11px] text-ink-3">
            {result.data.contract_version} · {result.data.ruleset_version}
          </p>
        </>
      )}
    </section>
  );
}
