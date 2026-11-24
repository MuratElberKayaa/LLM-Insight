/**
 * UI-R3: a chronological reading of one pseudonymous session's traces, from the trace list the
 * backend already returns for `?session=` (Phase 10C). Every position is the trace's own stored
 * start and end; nothing is estimated and no session-level metric is invented. Times that do not
 * parse leave the trace out of the axis but keep it in the list.
 */
import type { TraceListItem } from "./api/trace-types";
import { axisPercent, formatDurationNs, parseNs } from "./nanos";

export interface TimelineRow {
  item: TraceListItem;
  /** Start offset from the first trace's start ("+1.20 s"). */
  offsetLabel: string;
  durationLabel: string;
  left: number;
  width: number;
}

export interface SessionTimeline {
  rows: TimelineRow[];
  /** First trace start to last trace end, as stored; null without a parsable time. */
  spanLabel: string | null;
}

/** Oldest first (the list API is newest first); ties by trace id, so the order is stable. */
export function buildSessionTimeline(items: TraceListItem[]): SessionTimeline {
  const timed = items
    .map((item) => ({ item, start: parseNs(item.start_time_unix_nano), end: parseNs(item.end_time_unix_nano) }))
    .sort((a, b) => {
      if (a.start !== null && b.start !== null && a.start !== b.start) return a.start < b.start ? -1 : 1;
      return a.item.trace_id < b.item.trace_id ? -1 : a.item.trace_id > b.item.trace_id ? 1 : 0;
    });
  const starts = timed.map((t) => t.start).filter((v): v is bigint => v !== null);
  const ends = timed.map((t) => t.end).filter((v): v is bigint => v !== null);
  if (starts.length === 0 || ends.length === 0) {
    return { rows: timed.map(({ item }) => ({ item, offsetLabel: "—", durationLabel: formatDurationNs(BigInt(item.duration_ns)), left: 0, width: 0 })), spanLabel: null };
  }
  const axisStart = starts.reduce((m, v) => (v < m ? v : m));
  const axisEnd = ends.reduce((m, v) => (v > m ? v : m));
  const length = axisEnd > axisStart ? axisEnd - axisStart : 0n;
  return {
    rows: timed.map(({ item, start, end }) => {
      const geo = start !== null && end !== null ? axisPercent(start, end, axisStart, length) : { left: 0, width: 0 };
      return {
        item,
        offsetLabel: start === null ? "—" : `+${formatDurationNs(start - axisStart)}`,
        durationLabel: formatDurationNs(BigInt(item.duration_ns)),
        left: geo.left,
        width: geo.width,
      };
    }),
    spanLabel: formatDurationNs(length),
  };
}
