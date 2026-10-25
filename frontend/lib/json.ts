/**
 * JSON parsing that never silently rounds an integer. `JSON.parse` turns every number into
 * a float, so an integer above 2^53 (a span attribute such as `gen_ai.request.seed`, or a
 * hostile token count) would be displayed as a different value. With the reviver's source
 * text (ES2025 "JSON.parse source text access", available in Node 22), such integers are
 * kept as `LosslessInt` with their exact digits. Decoders that expect a plain number reject
 * a `LosslessInt`, so a value is either exact or refused, never approximated.
 */

export class LosslessInt {
  readonly digits: string;

  constructor(digits: string) {
    this.digits = digits;
  }

  toString(): string {
    return this.digits;
  }
}

/**
 * A non-integer whose JavaScript number is a different value from its source text (more significant
 * digits than a double keeps, e.g. `0.1000000000000000000001`). Only user-typed JSON produces it
 * (`parseUserJson`), which refuses it: sending the rounded number would change the assertion.
 */
export class InexactNumber {
  readonly source: string;

  constructor(source: string) {
    this.source = source;
  }
}

const INTEGER_SOURCE_RE = /^-?\d+$/;
const NUMBER_SOURCE_RE = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/;

/** Canonical (sign, digits, exponent) of a JSON number literal, for exact comparison. */
function canonicalDecimal(text: string): string | null {
  const m = NUMBER_SOURCE_RE.exec(text);
  if (m === null) return null;
  const [, sign, int, frac = "", exp = "0"] = m;
  let digits = (int + frac).replace(/^0+/, "");
  if (digits === "") return "0";
  let exponent = BigInt(exp) - BigInt(frac.length);
  const trailing = digits.length - digits.replace(/0+$/, "").length;
  digits = digits.slice(0, digits.length - trailing);
  exponent += BigInt(trailing);
  return `${sign}${digits}e${exponent}`;
}

export function parseJsonLossless(text: string, options: { refuseInexact?: boolean } = {}): unknown {
  return JSON.parse(text, (_key, value: unknown, context?: { source?: string }) => {
    if (typeof value === "number" && context?.source !== undefined) {
      if (!Number.isSafeInteger(value) && INTEGER_SOURCE_RE.test(context.source)) return new LosslessInt(context.source);
      if (options.refuseInexact === true && !INTEGER_SOURCE_RE.test(context.source) && canonicalDecimal(context.source) !== canonicalDecimal(String(value))) {
        return new InexactNumber(context.source);
      }
    }
    return value;
  });
}
