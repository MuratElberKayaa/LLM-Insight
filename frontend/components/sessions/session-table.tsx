import type { SessionList, SessionRow } from "../../lib/api/sessions";
import { formatUsd } from "../../lib/decimal";
import { formatInt, formatUtcMinute } from "../../lib/format";
import { DataTable, NullValue, type Column } from "../data-table";
import { Flag } from "../metric-card";

/** Phase 10C (ADR-56): one row per pseudonymous session; a link opens its traces. */
export function SessionTable({ data, project }: { data: SessionList; project: string | null }) {
  const href = (s: SessionRow) => {
    const sp = new URLSearchParams();
    if (project !== null) sp.set("project", project);
    sp.set("from", data.window.from);
    sp.set("to", data.window.to);
    sp.set("session", s.session);
    return `/traces?${sp.toString()}`;
  };
  const columns: Column<SessionRow>[] = [
    {
      key: "session",
      header: "Session (pseudonym)",
      rowHeader: true,
      render: (s) => (
        <a className="font-mono text-xs underline underline-offset-2" href={href(s)} data-testid="session-link" title="Open the traces of this session">
          {s.session}
        </a>
      ),
    },
    { key: "traces", header: "Traces", align: "right", render: (s) => <span>{formatInt(s.traces)}{s.error_traces > 0 ? <span className="block text-xs text-critical-ink">{formatInt(s.error_traces)} failed</span> : null}</span> },
    { key: "attempts", header: "Inference attempts", align: "right", render: (s) => formatInt(s.attempts) },
    {
      key: "tokens",
      header: "Tokens in / out",
      render: (s) => `${s.input_tokens === null ? "unknown" : formatInt(s.input_tokens)} / ${s.output_tokens === null ? "unknown" : formatInt(s.output_tokens)}`,
    },
    {
      key: "cost",
      header: "Known cost (USD)",
      render: (s) => {
        const known = s.known_cost_usd === null ? null : formatUsd(s.known_cost_usd);
        return (
          <span data-testid="session-cost">
            {known === null ? <NullValue label="no priced attempt">unknown</NullValue> : known.text}
            {known !== null && !s.cost_complete ? (
              <span className="ml-1">
                <Flag tone="warning" title="Some attempts have no computed cost; this is a lower bound">Incomplete</Flag>
              </span>
            ) : null}
            {s.unknown_cost_calls > 0 ? <span className="block text-xs text-ink-3">{formatInt(s.unknown_cost_calls)} attempt(s) with unknown cost</span> : null}
          </span>
        );
      },
    },
    { key: "seen", header: "First → last trace (UTC)", render: (s) => `${formatUtcMinute(s.first_seen)} → ${formatUtcMinute(s.last_seen)}` },
  ];
  return (
    <>
      <DataTable caption="Sessions by number of traces" captionHidden columns={columns} rows={data.items} rowKey={(s) => s.session} testId="session-table" />
      {data.truncated ? <p className="mt-2 text-xs text-ink-3" data-testid="sessions-truncated">Only the {formatInt(data.limit)} sessions with the most traces are listed.</p> : null}
    </>
  );
}
