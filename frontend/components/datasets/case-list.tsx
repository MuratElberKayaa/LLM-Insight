import Link from "next/link";
import { RULE_LABELS, type JsonValue, type TestCase } from "../../lib/api/review-types";
import { LosslessInt } from "../../lib/json";
import { formatIsoNs } from "../../lib/nanos";
import { Ident } from "../traces/trace-values";

/** Stored JSON as indented text (a text node: never interpreted as markup). */
export function jsonText(value: JsonValue): string {
  return JSON.stringify(value, (_k, v: unknown) => (v instanceof LosslessInt ? v.digits : v), 2);
}

/** UI-R4: the anchor of a case card, for the table and the previous / next links. */
export const caseAnchor = (id: number) => `case-${id}`;

/**
 * UI-R4: trace → human review → this case, as stored. The links open the trace page (its review
 * section for the review); nothing is created or re-run from here.
 */
function Lineage({ c, project }: { c: TestCase; project: string }) {
  if (c.origin_trace_id === null) {
    return (
      <p className="mt-2 text-xs text-ink-3" data-testid="case-lineage" data-origin="standalone">
        Standalone case (no source trace): entered directly with the cases API, not converted from a review.
      </p>
    );
  }
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-ink-3" data-testid="case-lineage" data-origin="trace">
      <span>From trace</span>
      <Link href={`/traces/${c.origin_trace_id}?project=${project}`} className="underline underline-offset-2" data-testid="case-origin-trace">
        <Ident value={c.origin_trace_id} />
      </Link>
      <span aria-hidden="true">→</span>
      {c.origin_review_id === null ? (
        <span>its review was removed</span>
      ) : (
        <Link href={`/traces/${c.origin_trace_id}?project=${project}#reviews-heading`} className="underline underline-offset-2" data-testid="case-origin-review">
          review #{c.origin_review_id}
        </Link>
      )}
      <span aria-hidden="true">→</span>
      <span>approved as this test case</span>
    </p>
  );
}

function CaseCard({ c, project, prev, next }: { c: TestCase; project: string; prev: TestCase | null; next: TestCase | null }) {
  const { type, ...params } = c.rule;
  return (
    // tabIndex -1: a jump from the index or previous / next moves focus here (CaseFocus), not only the scroll.
    <li
      id={caseAnchor(c.id)}
      tabIndex={-1}
      aria-label={`Test case #${c.id}`}
      className="scroll-mt-20 rounded-xl border border-line bg-surface px-4 py-4 outline-none focus-visible:ring-2 focus-visible:ring-focus sm:px-5 [&:focus]:ring-2 [&:focus]:ring-focus"
      data-testid="test-case"
      data-case-id={c.id}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-ink">Test case #{c.id}</span>
        <span className="rounded-full border border-line bg-raised px-2 py-0.5 text-xs text-ink-2">{RULE_LABELS[type]}</span>
        <span className="rounded-full border border-good/50 bg-good/10 px-2 py-0.5 text-xs text-ink" data-testid="case-approved">
          <span aria-hidden="true">✓ </span>Approved by {c.approved_by}
        </span>
        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-2">Server redaction applied</span>
        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-3">Evaluated only by experiments</span>
      </div>
      <Lineage c={c} project={project} />
      <p className="mt-1 text-xs text-ink-3">Approved {formatIsoNs(c.approved_at, 6)}.</p>
      <dl className="mt-3 grid gap-3 text-sm lg:grid-cols-2">
        <div className="lg:col-span-2">
          <dt className="text-xs text-ink-3">Input (human-entered, as stored after redaction; not a captured user conversation and not rebuilt from telemetry)</dt>
          <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink" data-testid="case-input-text">
            {c.sanitized_input}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink-3">Expected output</dt>
          <dd className="mt-0.5">
            {c.expected_output === null ? (
              <span className="text-xs text-ink-3">None: this case is never evaluated (every experiment reports it as not evaluated).</span>
            ) : (
              <pre className="max-h-64 overflow-auto rounded-md bg-canvas p-2 font-mono text-xs text-ink-2">{jsonText(c.expected_output)}</pre>
            )}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink-3">Rule ({type})</dt>
          <dd className="mt-0.5">
            {Object.keys(params).length === 0 ? (
              <span className="text-xs text-ink-3">No parameters: the output must equal the expected output.</span>
            ) : (
              <pre className="max-h-64 overflow-auto rounded-md bg-canvas p-2 font-mono text-xs text-ink-2">{jsonText(params)}</pre>
            )}
          </dd>
        </div>
      </dl>
      {(prev || next) && (
        <nav aria-label={`Move from test case #${c.id}`} className="mt-3 flex flex-wrap justify-between gap-2 border-t border-line pt-2.5 text-xs">
          {prev ? (
            <a href={`#${caseAnchor(prev.id)}`} className="text-ink-2 underline-offset-2 hover:text-ink hover:underline" data-testid="case-prev">
              ← Case #{prev.id}
            </a>
          ) : (
            <span />
          )}
          {next && (
            <a href={`#${caseAnchor(next.id)}`} className="text-ink-2 underline-offset-2 hover:text-ink hover:underline" data-testid="case-next">
              Case #{next.id} →
            </a>
          )}
        </nav>
      )}
    </li>
  );
}

export function CaseList({ cases, project }: { cases: TestCase[]; project: string }) {
  return (
    <ul className="space-y-3" data-testid="case-list">
      {cases.map((c, i) => (
        <CaseCard key={c.id} c={c} project={project} prev={cases[i - 1] ?? null} next={cases[i + 1] ?? null} />
      ))}
    </ul>
  );
}
