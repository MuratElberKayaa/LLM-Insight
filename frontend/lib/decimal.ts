/**
 * Exact decimal handling for money strings from the backend (ADR-11). Values are parsed into
 * BigInt units at their own scale, so formatting and rounding never pass through a float.
 */

export interface Decimal {
  negative: boolean;
  units: bigint; // absolute value × 10^scale
  scale: number;
}

const DECIMAL_RE = /^(-)?(\d+)(?:\.(\d+))?$/;

export function parseDecimal(s: string): Decimal | null {
  const m = DECIMAL_RE.exec(s);
  if (m === null) return null;
  const frac = m[3] ?? "";
  return { negative: m[1] === "-", units: BigInt((m[2] ?? "0") + frac), scale: frac.length };
}

export function isZero(d: Decimal): boolean {
  return d.units === 0n;
}

/** Round half-even to `places` fraction digits (no-op when already that short). */
export function roundHalfEven(d: Decimal, places: number): Decimal {
  if (d.scale <= places) return d;
  const divisor = 10n ** BigInt(d.scale - places);
  let q = d.units / divisor;
  const r = d.units % divisor;
  const twice = r * 2n;
  if (twice > divisor || (twice === divisor && q % 2n === 1n)) q += 1n;
  return { negative: d.negative && q !== 0n, units: q, scale: places };
}

function groupThousands(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Plain digits with thousands separators; trailing fraction zeros trimmed to `minFraction`. */
export function formatDecimal(d: Decimal, minFraction = 0): string {
  const digits = d.units.toString().padStart(d.scale + 1, "0");
  const int = digits.slice(0, digits.length - d.scale);
  let frac = d.scale > 0 ? digits.slice(digits.length - d.scale) : "";
  frac = frac.replace(/0+$/, "");
  if (frac.length < minFraction) frac = frac.padEnd(minFraction, "0");
  const sign = d.negative && d.units !== 0n ? "-" : "";
  return `${sign}${groupThousands(int)}${frac ? `.${frac}` : ""}`;
}

export interface UsdDisplay {
  /** What the UI shows, e.g. "$0.0001875" or "≈ $12.345679". */
  text: string;
  /** The exact backend value, e.g. "$12.3456789012" (for titles and tables). */
  exact: string;
  approximate: boolean;
}

/**
 * Format a USD decimal string. Shows the exact value (trailing zeros trimmed, at least 2
 * fraction digits) when it fits in `minFraction` fraction digits, or, for amounts below
 * $1, in `significant` digits after the leading zeros (so $0.0001875 is never rounded
 * away). Longer values are rounded half-even to that many places and marked approximate,
 * with the exact value kept. Returns null for a string that is not a decimal (the decoder
 * rejects those before rendering).
 */
export function formatUsd(value: string, significant = 5, minFraction = 6): UsdDisplay | null {
  const d = parseDecimal(value);
  if (d === null) return null;
  const exact = `$${formatDecimal(d, 2)}`;
  const [int = "0", frac = ""] = formatDecimal(d).replace("-", "").split(".");
  const leadingZeros = int.replace(/,/g, "") === "0" ? (/^0*/.exec(frac)?.[0].length ?? 0) : 0;
  const places = Math.max(minFraction, int.replace(/,/g, "") === "0" ? leadingZeros + significant : 0);
  if (frac.length <= places) return { text: exact, exact, approximate: false };
  return { text: `≈ $${formatDecimal(roundHalfEven(d, places), 2)}`, exact, approximate: true };
}

/** Float for chart geometry ONLY (bar heights). Labels and tooltips use the exact string. */
export function decimalToChartNumber(value: string | null): number | null {
  if (value === null) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
