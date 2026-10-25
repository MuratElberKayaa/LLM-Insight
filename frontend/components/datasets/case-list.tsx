import Link from "next/link";
import { RULE_LABELS, type JsonValue, type TestCase } from "../../lib/api/review-types";
import { LosslessInt } from "../../lib/json";
import { formatIsoNs } from "../../lib/nanos";
import { Ident } from "../traces/trace-values";

/** Stored JSON as indented text (a text node: never interpreted as markup). */
export function jsonText(value: JsonValue): string {
  return JSON.stringify(value, (_k, v: unknown) => (v instanceof LosslessInt ? v.digits : v), 2);
}

function CaseCard({ c, project }: { c: TestCase; project: string }) {
  const { type, ...params } = c.rule;
  return (
    <li className="rounded-lg border border-line bg-surface px-4 py-4" data-testid="test-case" data-case-id={c.id}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-ink">Test case #{c.id}</span>
        <span className="rounded-full border border-line bg-raised px-2 py-0.5 text-xs text-ink-2">{RULE_LABELS[type]}</span>
        <span className="rounded-full border border-good/50 bg-good/10 px-2 py-0.5 text-xs text-ink" data-testid="case-approved">
          Approved by {c.approved_by}
        </span>
        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-2">Server redaction applied</span>
        <span className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-3">Not evaluated yet</span>
      </div>
      <p className="mt-2 text-xs text-ink-3">
        {c.origin_trace_id === null ? (
          "Standalone case (no source trace)."
        ) : (
          <>
            From trace{" "}
            <Link href={`/traces/${c.origin_trace_id}?project=${project}`} className="underline underline-offset-2" data-testid="case-origin-trace">
              <Ident value={c.origin_trace_id} />
            </Link>
            {c.origin_review_id === null ? " (its review was removed)" : `, review #${c.origin_review_id}`}.
          </>
        )}{" "}
        Approved {formatIsoNs(c.approved_at, 6)}.
      </p>
      <dl className="mt-3 grid gap-3 text-sm lg:grid-cols-2">
        <div className="lg:col-span-2">
          <dt className="text-xs text-ink-3">Input (human-entered, as stored after redaction)</dt>
          <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink" data-testid="case-input-text">
            {c.sanitized_input}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink-3">Expected output</dt>
          <dd className="mt-0.5">
            {c.expected_output === null ? (
              <span className="text-xs text-ink-3">None: rules that need it will report this case as not evaluated.</span>
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
    </li>
  );
}

export function CaseList({ cases, project }: { cases: TestCase[]; project: string }) {
  return (
    <ul className="space-y-3" data-testid="case-list">
      {cases.map((c) => (
        <CaseCard key={c.id} c={c} project={project} />
      ))}
    </ul>
  );
}
