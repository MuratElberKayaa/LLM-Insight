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

const INTEGER_SOURCE_RE = /^-?\d+$/;

export function parseJsonLossless(text: string): unknown {
  return JSON.parse(text, (_key, value: unknown, context?: { source?: string }) => {
    if (typeof value === "number" && !Number.isSafeInteger(value) && context?.source !== undefined && INTEGER_SOURCE_RE.test(context.source)) {
      return new LosslessInt(context.source);
    }
    return value;
  });
}
