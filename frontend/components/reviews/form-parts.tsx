"use client";

import { startTransition, useActionState, useEffect, useRef, type FormEvent, type ReactNode } from "react";
import { IDLE, type ActionState } from "../../lib/action-state";
import { REDACTION_CATEGORIES } from "../../lib/api/review-types";

type Action = (prev: ActionState, formData: FormData) => Promise<ActionState>;

/**
 * One explicit write per submit (Phase 4B):
 * - the submit is ignored while a previous one is pending (no double submission), and the
 *   button is disabled meanwhile;
 * - the form is dispatched without React's automatic reset, so a refused submission keeps
 *   what the person typed; it is cleared only after a stored success;
 * - nothing is submitted on render or on page load.
 */
export function useWriteForm(action: Action) {
  const [state, dispatch, pending] = useActionState(action, IDLE);
  const formRef = useRef<HTMLFormElement>(null);
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending) return;
    const data = new FormData(event.currentTarget);
    startTransition(() => dispatch(data));
  };
  useEffect(() => {
    if (state.status === "success") formRef.current?.reset();
  }, [state]);
  const invalid = (field: string) => state.status === "error" && state.fields.includes(field);
  return { state, pending, formRef, onSubmit, invalid };
}

export function SubmitButton({ pending, children, testId }: { pending: boolean; children: ReactNode; testId?: string }) {
  return (
    <button
      type="submit"
      disabled={pending}
      aria-disabled={pending}
      data-testid={testId}
      className="inline-flex items-center justify-center rounded-md border border-series-1/70 bg-series-1/20 px-4 py-2 text-sm font-semibold text-ink hover:bg-series-1/30 disabled:cursor-wait disabled:opacity-60"
    >
      {pending ? "Saving…" : children}
    </button>
  );
}

function maskedSummary(masked: Record<string, Partial<Record<string, number>>>): string | null {
  const parts: string[] = [];
  for (const [field, counts] of Object.entries(masked)) {
    for (const category of REDACTION_CATEGORIES) {
      const n = counts[category];
      if (n) parts.push(`${n} ${category} value${n === 1 ? "" : "s"} in ${field.replaceAll("_", " ")}`);
    }
  }
  return parts.length === 0 ? null : parts.join(", ");
}

/** The outcome of the last submission; announced to assistive technology. */
export function ActionMessage({ state, testId }: { state: ActionState; testId?: string }) {
  if (state.status === "idle") return null;
  if (state.status === "error") {
    return (
      <div role="alert" data-testid={testId} data-outcome={state.outcomeUnknown ? "unknown" : "error"} className="rounded-md border border-critical/50 bg-critical/10 px-3 py-2 text-sm text-ink">
        {state.message}
      </div>
    );
  }
  const masked = state.masked === null ? null : maskedSummary(state.masked);
  return (
    <div role="status" data-testid={testId} data-outcome="success" className="rounded-md border border-good/50 bg-good/10 px-3 py-2 text-sm text-ink">
      <p>{state.message}</p>
      {state.masked !== null && (
        <p className="mt-1 text-xs text-ink-2" data-testid={testId ? `${testId}-redaction` : undefined}>
          {masked === null ? "Server redaction ran; nothing needed masking." : `Server redaction masked ${masked} before storage.`}
        </p>
      )}
      {state.link && (
        <a href={state.link.href} className="mt-1 inline-block text-xs font-medium text-ink underline underline-offset-2">
          {state.link.label}
        </a>
      )}
    </div>
  );
}

export function FieldLabel({ htmlFor, children, hint }: { htmlFor: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-1">
      <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
        {children}
      </label>
      {hint && (
        <p id={`${htmlFor}-hint`} className="text-xs text-ink-3">
          {hint}
        </p>
      )}
    </div>
  );
}

export const inputClass =
  "w-full rounded-md border border-line bg-canvas px-3 py-2 text-sm text-ink placeholder:text-ink-3 aria-[invalid=true]:border-critical";
