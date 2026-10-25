/**
 * Minimal typed decoders for backend JSON. A response that does not match the documented
 * schema (ARCHITECTURE.md §5.2) is rejected as a whole, so the UI never renders a guessed
 * value. Errors carry the JSON path only, never the offending value.
 */

export class DecodeError extends Error {
  readonly path: string;

  constructor(path: string, expected: string) {
    super(`${path}: expected ${expected}`);
    this.name = "DecodeError";
    this.path = path;
  }
}

export type Decoder<T> = (value: unknown, path: string) => T;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A non-negative safe integer (counts). */
export const count: Decoder<number> = (v, path) => {
  if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) throw new DecodeError(path, "a non-negative integer");
  return v;
};

/** A positive safe integer (ids). */
export const positiveInt: Decoder<number> = (v, path) => {
  if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 1) throw new DecodeError(path, "a positive integer");
  return v;
};

/** A finite, non-negative number (milliseconds). */
export const nonNegativeNumber: Decoder<number> = (v, path) => {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0) throw new DecodeError(path, "a non-negative number");
  return v;
};

/** A rate in [0, 1]. */
export const rate: Decoder<number> = (v, path) => {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 1) throw new DecodeError(path, "a rate between 0 and 1");
  return v;
};

export const boolean: Decoder<boolean> = (v, path) => {
  if (typeof v !== "boolean") throw new DecodeError(path, "a boolean");
  return v;
};

/** A string of at most `max` characters. */
export function string(max = 1024): Decoder<string> {
  return (v, path) => {
    if (typeof v !== "string" || v.length > max) throw new DecodeError(path, `a string of at most ${max} characters`);
    return v;
  };
}

const DECIMAL_RE = /^\d{1,30}(\.\d{1,20})?$/;

/** A non-negative decimal string (money). Never converted to a float here. */
export const decimalString: Decoder<string> = (v, path) => {
  if (typeof v !== "string" || !DECIMAL_RE.test(v)) throw new DecodeError(path, "a non-negative decimal string");
  return v;
};

const ISO_UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$/;

/** An ISO-8601 UTC timestamp with a `Z` suffix. */
export const isoUtc: Decoder<string> = (v, path) => {
  if (typeof v !== "string" || !ISO_UTC_RE.test(v) || Number.isNaN(Date.parse(v))) {
    throw new DecodeError(path, "an ISO-8601 UTC timestamp");
  }
  return v;
};

export function literal<const T extends readonly (string | number | boolean)[]>(...values: T): Decoder<T[number]> {
  return (v, path) => {
    if (!values.includes(v as T[number])) throw new DecodeError(path, `one of ${values.map(String).join(", ")}`);
    return v as T[number];
  };
}

export function nullable<T>(decoder: Decoder<T>): Decoder<T | null> {
  return (v, path) => (v === null ? null : decoder(v, path));
}

export function array<T>(decoder: Decoder<T>, maxItems: number): Decoder<T[]> {
  return (v, path) => {
    if (!Array.isArray(v) || v.length > maxItems) throw new DecodeError(path, `an array of at most ${maxItems} items`);
    return v.map((item, i) => decoder(item, `${path}[${i}]`));
  };
}

type Shape<T> = { [K in keyof T]: Decoder<T[K]> };

/** An object with exactly the listed fields decoded; unknown extra fields are ignored. */
export function object<T>(shape: Shape<T>): Decoder<T> {
  return (v, path) => {
    if (!isRecord(v)) throw new DecodeError(path, "an object");
    const out = {} as T;
    for (const key of Object.keys(shape) as (keyof T & string)[]) {
      if (!Object.hasOwn(v, key)) throw new DecodeError(`${path}.${key}`, "a present field");
      out[key] = shape[key](v[key], `${path}.${key}`);
    }
    return out;
  };
}

/** An object whose keys are exactly `keys` (all present), each decoded with `decoder`. */
export function keyed<K extends string, T>(keys: readonly K[], decoder: Decoder<T>): Decoder<Record<K, T>> {
  return (v, path) => {
    if (!isRecord(v)) throw new DecodeError(path, "an object");
    const out = {} as Record<K, T>;
    for (const key of keys) {
      if (!Object.hasOwn(v, key)) throw new DecodeError(`${path}.${key}`, "a present field");
      out[key] = decoder(v[key], `${path}.${key}`);
    }
    return out;
  };
}

/** Decode a whole document; returns the error path instead of throwing. */
export function decode<T>(decoder: Decoder<T>, value: unknown): { ok: true; value: T } | { ok: false; path: string } {
  try {
    return { ok: true, value: decoder(value, "$") };
  } catch (err) {
    if (err instanceof DecodeError) return { ok: false, path: err.path };
    throw err;
  }
}
