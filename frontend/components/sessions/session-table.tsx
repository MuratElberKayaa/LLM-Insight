import Link from "next/link";
import type { SessionList, SessionRow } from "../../lib/api/sessions";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatUtcMinute } from "../../lib/format";
import { DataTable, NullValue, type Column } from "../data-table";
import { Flag } from "../metric-card";

function Cost({ s }: { s: SessionRow }) {
  const known = s.known_cost_usd === null ? null : formatUsd(s.known_cost_usd);
  return (
    <span data-testid="session-cost">
      {known === null ? <NullValue label="no priced attempt">unknown</NullValue> : known.text}
      {known !== null && !s.cost_complete ? (
        <span className="ml-1">
          <Flag tone="warning" title="Some attempts have no computed cost; this is a lower bound">
            Incomplete
          </Flag>
        </span>
      ) : null}
      {s.unknown_cost_calls > 0 ? <span className="block text-xs text-ink-3">{formatInt(s.unknown_cost_calls)} attempt(s) with unknown cost</span> : null}
    </span>
  );
}

function Traces({ s }: { s: SessionRow }) {
  return (
    <span>
      {formatInt(s.traces)}
      {s.error_traces > 0 ? (
        <span className="block text-xs text-critical-ink">
          <span aria-hidden="true">✕ </span>
          {formatInt(s.error_traces)} failed
        </span>
      ) : null}
    </span>
  );
}

/**
 * Phase 10C (ADR-56) / UI-R3: one row per pseudonymous session. "Inspect" opens the session on this
 * page (timeline, `?session=`); the pseudonym link opens its traces in the trace list (unchanged).
 * Below `md` the same values are cards. The pseudonym is shown as stored: a keyed hash, not an
 * anonymous id.
 */
export function SessionTable({ data, project, selected = null, inspectHref }: { data: SessionList; project: string | null; selected?: string | null; inspectHref?: (s: SessionRow) => string }) {
  const href = (s: SessionRow) => {
    const sp = new URLSearchParams();
    if (project !== null) sp.set("project", project);
    sp.set("from", data.window.from);
    sp.set("to", data.window.to);
    sp.set("session", s.session);
    return `/traces?${sp.toString()}`;
  };
  const inspect = (s: SessionRow) =>
    inspectHref ? (
      <Link
        href={inspectHref(s)}
        prefetch={false}
        aria-current={selected === s.session ? "true" : undefined}
        className="whitespace-nowrap text-xs font-medium text-accent underline-offset-2 hover:underline aria-[current=true]:text-ink"
        data-testid="session-inspect"
      >
        {selected === s.session ? "Selected" : "Inspect →"}
      </Link>
    ) : null;
  const columns: Column<SessionRow>[] = [
    {
      key: "session",
      header: "Session (pseudonym)",
      rowHeader: true,
      render: (s) => (
        // UI-R3: the full pseudonym, wrapped in a bounded column so the table fits without scrolling.
        <a className="inline-block max-w-[11rem] whitespace-normal break-all font-mono text-xs underline underline-offset-2" href={href(s)} data-testid="session-link" title="Open the traces of this session">
          {s.session}
        </a>
      ),
    },
    { key: "traces", header: "Traces", align: "right", render: (s) => <Traces s={s} /> },
    { key: "attempts", header: "Inference attempts", align: "right", render: (s) => formatInt(s.attempts) },
    {
      key: "tokens",
      header: "Tokens in / out",
      align: "right",
      render: (s) => `${s.input_tokens === null ? "unknown" : formatInt(s.input_tokens)} / ${s.output_tokens === null ? "unknown" : formatInt(s.output_tokens)}`,
    },
    { key: "cost", header: "Known cost (USD)", align: "right", render: (s) => <Cost s={s} /> },
    {
      key: "seen",
      header: "First → last trace (UTC)",
      // Two lines, so the table fits beside a full pseudonym without scrolling.
      render: (s) => (
        <span className="tabular block text-xs">
          <span className="block">{formatUtcMinute(s.first_seen)}</span>
          <span className="block text-ink-3">→ {formatUtcMinute(s.last_seen)}</span>
        </span>
      ),
    },
    ...(inspectHref ? [{ key: "inspect", header: "Timeline", render: (s: SessionRow) => inspect(s) }] : []),
  ];
  return (
    <>
      <div className="hidden md:block">
        <DataTable caption="Sessions by number of traces" captionHidden columns={columns} rows={data.items} rowKey={(s) => s.session} testId="session-table" />
      </div>
      <ul className="space-y-2 md:hidden" aria-label="Sessions" data-testid="session-cards">
        {data.items.map((s) => (
          <li key={s.session} className={`rounded-lg border px-4 py-3 ${selected === s.session ? "border-accent/60 bg-accent/5" : "border-line bg-surface"}`}>
            <a className="block break-all font-mono text-xs text-ink underline underline-offset-2" href={href(s)} title="Open the traces of this session">
              {s.session}
            </a>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
              <div>
                <dt className="text-xs text-ink-3">Traces</dt>
                <dd className="tabular text-ink-2">
                  <Traces s={s} />
                </dd>
              </div>
              <div>
                <dt className="text-xs text-ink-3">Attempts</dt>
                <dd className="tabular text-ink-2">{formatInt(s.attempts)}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-ink-3">Known cost (USD)</dt>
                <dd className="text-ink-2">
                  <Cost s={s} />
                </dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-ink-3">First → last trace (UTC)</dt>
                <dd className="tabular text-xs text-ink-2">
                  {formatUtcMinute(s.first_seen)} → {formatUtcMinute(s.last_seen)}
                </dd>
              </div>
            </dl>
            {inspect(s) && <div className="mt-2">{inspect(s)}</div>}
          </li>
        ))}
      </ul>
      {data.truncated ? (
        <p className="mt-2 text-xs text-ink-3" data-testid="sessions-truncated">
          Only the {formatInt(data.limit)} sessions with the most traces are listed.
        </p>
      ) : null}
    </>
  );
}
