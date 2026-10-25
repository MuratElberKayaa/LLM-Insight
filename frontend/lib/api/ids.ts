/** OTel identifier decoders shared by the trace and review schemas (ARCHITECTURE.md §4.1). */

import { DecodeError, type Decoder } from "../validate";

export const TRACE_ID_RE = /^[0-9a-f]{32}$/;
export const SPAN_ID_RE = /^[0-9a-f]{16}$/;

export function pattern(re: RegExp, expected: string): Decoder<string> {
  return (v, path) => {
    if (typeof v !== "string" || !re.test(v)) throw new DecodeError(path, expected);
    return v;
  };
}

export const traceId = pattern(TRACE_ID_RE, "a 32-character lowercase hex trace id");
export const spanId = pattern(SPAN_ID_RE, "a 16-character lowercase hex span id");
