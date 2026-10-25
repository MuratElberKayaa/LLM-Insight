/**
 * Nanosecond time arithmetic for traces (T26). The backend sends `*_unix_nano` and
 * `duration_ns` as decimal strings because epoch nanoseconds (~1.8e18) exceed 2^53; a JS
 * number would round them to ~256 ns. Everything here uses BigInt, and floats appear only
 * for on-screen geometry (bar positions in percent).
 */

const NS_RE = /^\d{1,19}$/;
export const INT64_MAX = 2n ** 63n - 1n;
const NS_PER_MS = 1_000_000n;
const NS_PER_S = 1_000_000_000n;

/** A non-negative int64 nanosecond string, or null when it is not one. */
export function parseNs(value: string): bigint | null {
  if (!NS_RE.test(value)) return null;
  const n = BigInt(value);
  return n <= INT64_MAX ? n : null;
}

function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** `value / 10^scale` as a decimal string with exactly `places` fraction digits (truncated toward zero). */
function fixed(value: bigint, scale: bigint, places: number): string {
  const int = value / scale;
  if (places === 0) return groupThousands(int.toString());
  const frac = ((value % scale) * 10n ** BigInt(places)) / scale;
  return `${groupThousands(int.toString())}.${frac.toString().padStart(places, "0")}`;
}

/**
 * A duration from exact nanoseconds: "0 ns", "850 ns", "11.2623 ms", "1.284 s", "2 min 5.1 s".
 * Sub-second values keep enough digits to show 100 ns steps exactly; digits are truncated,
 * never rounded up into a larger value.
 */
export function formatDurationNs(ns: bigint): string {
  if (ns < 0n) return `−${formatDurationNs(-ns)}`;
  if (ns < 1000n) return `${ns.toString()} ns`;
  if (ns < NS_PER_S) {
    const s = fixed(ns, NS_PER_MS, 6).replace(/0+$/, "").replace(/\.$/, "");
    return `${s} ms`;
  }
  if (ns < 60n * NS_PER_S) return `${fixed(ns, NS_PER_S, 3)} s`;
  const minutes = ns / (60n * NS_PER_S);
  const rest = ns % (60n * NS_PER_S);
  return `${groupThousands(minutes.toString())} min ${fixed(rest, NS_PER_S, 1)} s`;
}

/** The exact millisecond value with all 6 fraction digits, for titles and tables: "11.262300 ms". */
export function formatExactMs(ns: bigint): string {
  return `${fixed(ns, NS_PER_MS, 6)} ms`;
}

const ISO_NS_RE = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z$/;

/**
 * "2026-10-25 07:29:47.428346900 UTC" from the backend's ISO string, by string handling only
 * (a JS Date keeps milliseconds and would drop the remaining digits). Returns the input
 * unchanged if it is not an ISO UTC timestamp (decoders reject those before rendering).
 */
export function formatIsoNs(iso: string, fractionDigits = 9): string {
  const m = ISO_NS_RE.exec(iso);
  if (m === null) return iso;
  const frac = (m[3] ?? "").padEnd(9, "0").slice(0, fractionDigits);
  return `${m[1]} ${m[2]}${frac ? `.${frac}` : ""} UTC`;
}

/**
 * Position of [start, end] on an axis [axisStart, axisStart + axisLength] as percentages
 * with 0.0001 % resolution, clamped to the axis. A zero-length axis puts everything at 0.
 */
export function axisPercent(start: bigint, end: bigint, axisStart: bigint, axisLength: bigint): { left: number; width: number } {
  if (axisLength <= 0n) return { left: 0, width: 0 };
  const clamp = (v: bigint) => (v < 0n ? 0n : v > axisLength ? axisLength : v);
  const a = clamp(start - axisStart);
  const b = clamp(end - axisStart);
  const pct = (v: bigint) => Number((v * 1_000_000n) / axisLength) / 10_000;
  return { left: pct(a), width: pct(b > a ? b - a : 0n) };
}
