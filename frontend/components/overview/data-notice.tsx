import type { Overview } from "../../lib/api/types";
import { dataNotice } from "../../lib/provenance";
import { ProvenanceBadge } from "../provenance-badge";
import { formatInt } from "../../lib/format";

/** Provenance banner above the KPIs: synthetic data is always identified (T48). */
export function DataNoticeBanner({ data }: { data: Overview }) {
  const traces = data.traces.count.by_provenance;
  const attempts = data.inference.attempts.by_provenance;
  const notice = dataNotice(traces, attempts);
  if (notice.kind === "empty") return null;
  const caution = notice.kind !== "real_only";
  return (
    <div
      role="note"
      data-testid="data-notice"
      data-notice={notice.kind}
      className={`mb-5 rounded-lg border px-4 py-3 ${caution ? "border-synthetic/60 bg-synthetic/10" : "border-line bg-surface"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-semibold text-ink">
          {notice.kind === "synthetic_only" && (
            <span className="mr-2 rounded bg-synthetic px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white">Synthetic</span>
          )}
          {notice.title}
        </p>
        <div className="flex flex-wrap gap-1.5" aria-label="Traces by provenance">
          {(["real", "synthetic", "mixed", "unknown"] as const).map((p) => (
            <ProvenanceBadge key={p} provenance={p} count={formatInt(traces[p])} />
          ))}
        </div>
      </div>
      <p className="mt-1 text-sm text-ink-2">{notice.body}</p>
      <p className="mt-1 text-xs text-ink-3">
        Headline scope: <span className="text-ink-2">{data.provenance_label}</span>. Provenance is asserted by the instrumentation and not verified by the server.
      </p>
    </div>
  );
}
