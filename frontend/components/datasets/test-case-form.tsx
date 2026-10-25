"use client";

import { useState } from "react";
import { submitTestCase } from "../../lib/actions/datasets";
import { LIMITS, RULE_LABELS, RULE_TYPES, type Dataset, type RuleType } from "../../lib/api/review-types";
import { ActionMessage, FieldLabel, inputClass, SubmitButton, useWriteForm } from "../reviews/form-parts";

/**
 * Trace-to-Testset (Phase 4B). The trace stored metadata only, so this form starts empty and
 * nothing is copied from the trace: the person types a synthetic or redacted input and the
 * expected result, chooses how the output will be checked, and approves explicitly. Only the
 * references (trace, review) come from the page.
 */
export function TestCaseForm({ project, traceId, reviewId, datasets }: { project: string; traceId: string; reviewId: number; datasets: Dataset[] }) {
  const { state, pending, formRef, onSubmit, invalid } = useWriteForm(submitTestCase);
  const [ruleType, setRuleType] = useState<RuleType | "">("");
  return (
    <form ref={formRef} onSubmit={onSubmit} noValidate className="space-y-4" data-testid="test-case-form" onReset={() => setRuleType("")}>
      <input type="hidden" name="project" value={project} />
      <input type="hidden" name="trace_id" value={traceId} />
      <input type="hidden" name="review_id" value={String(reviewId)} />
      <div>
        <FieldLabel htmlFor="dataset_id">Dataset</FieldLabel>
        <select id="dataset_id" name="dataset_id" required defaultValue="" aria-invalid={invalid("dataset_id")} className={inputClass} data-testid="case-dataset">
          <option value="" disabled>
            Choose a dataset…
          </option>
          {datasets.map((d) => (
            <option key={d.id} value={String(d.id)}>
              {d.name} ({d.case_count} case{d.case_count === 1 ? "" : "s"})
            </option>
          ))}
        </select>
      </div>
      <div>
        <FieldLabel htmlFor="sanitized_input" hint={`Type a synthetic or redacted input (at most ${LIMITS.sanitizedInput} characters). The original prompt was never stored and is not recovered.`}>
          Test input
        </FieldLabel>
        <textarea
          id="sanitized_input"
          name="sanitized_input"
          required
          rows={5}
          maxLength={LIMITS.sanitizedInput}
          aria-invalid={invalid("sanitized_input")}
          aria-describedby="sanitized_input-hint"
          className={inputClass}
          data-testid="case-input"
        />
      </div>
      <div>
        <FieldLabel htmlFor="rule_type" hint="Checks are deterministic. Nothing is evaluated now: evaluation is Phase 4C.">
          How the output will be checked
        </FieldLabel>
        <select
          id="rule_type"
          name="rule_type"
          required
          value={ruleType}
          onChange={(e) => setRuleType(e.target.value as RuleType | "")}
          aria-invalid={invalid("rule_type")}
          aria-describedby="rule_type-hint"
          className={inputClass}
          data-testid="case-rule-type"
        >
          <option value="" disabled>
            Choose a rule…
          </option>
          {RULE_TYPES.map((t) => (
            <option key={t} value={t}>
              {RULE_LABELS[t]} ({t})
            </option>
          ))}
        </select>
      </div>
      {ruleType === "json_schema" && (
        <div>
          <FieldLabel htmlFor="rule_schema" hint="A JSON Schema object. Only local references ($ref starting with #) are accepted.">
            JSON Schema
          </FieldLabel>
          <textarea
            id="rule_schema"
            name="rule_schema"
            required
            rows={6}
            spellCheck={false}
            aria-invalid={invalid("rule_schema") || invalid("rule")}
            aria-describedby="rule_schema-hint"
            className={`${inputClass} font-mono`}
            data-testid="case-rule-schema"
          />
        </div>
      )}
      {ruleType === "field_match" && (
        <div>
          <FieldLabel htmlFor="rule_fields" hint="Field paths compared with the expected output, separated by commas (for example: summary, meta.lang).">
            Fields to match
          </FieldLabel>
          <input id="rule_fields" name="rule_fields" required aria-invalid={invalid("rule_fields") || invalid("rule")} aria-describedby="rule_fields-hint" className={inputClass} data-testid="case-rule-fields" />
        </div>
      )}
      {ruleType === "regex" && (
        <div>
          <FieldLabel htmlFor="rule_pattern" hint={`A regular expression the output must match (at most ${LIMITS.pattern} characters).`}>
            Pattern
          </FieldLabel>
          <input
            id="rule_pattern"
            name="rule_pattern"
            required
            maxLength={LIMITS.pattern}
            spellCheck={false}
            aria-invalid={invalid("rule_pattern") || invalid("rule")}
            aria-describedby="rule_pattern-hint"
            className={`${inputClass} font-mono`}
            data-testid="case-rule-pattern"
          />
        </div>
      )}
      <div>
        <FieldLabel
          htmlFor="expected_output"
          hint="Optional JSON. Exact and field-match rules need it; a case without one is stored and later reported as not evaluated. Values that look like personal data or secrets are refused."
        >
          Expected output (JSON)
        </FieldLabel>
        <textarea
          id="expected_output"
          name="expected_output"
          rows={4}
          spellCheck={false}
          aria-invalid={invalid("expected_output")}
          aria-describedby="expected_output-hint"
          className={`${inputClass} font-mono`}
          data-testid="case-expected"
        />
      </div>
      <div>
        <FieldLabel htmlFor="approved_by" hint="Self-declared: the MVP has no user accounts. Use a name or handle.">
          Approved by
        </FieldLabel>
        <input id="approved_by" name="approved_by" required maxLength={LIMITS.actor} autoComplete="off" aria-invalid={invalid("approved_by")} aria-describedby="approved_by-hint" className={inputClass} data-testid="case-approved-by" />
      </div>
      <div className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2">
        <input id="approve" name="approve" type="checkbox" value="yes" required aria-invalid={invalid("approve")} className="mt-1" data-testid="case-approve" />
        <label htmlFor="approve" className="text-sm text-ink">
          I approve storing this test case. I typed the input and expected result myself; they are synthetic or redacted and contain no production content. I understand the server
          masks e-mail, phone, number and key-like values in the input before storage.
        </label>
      </div>
      <ActionMessage state={state} testId="case-form-result" />
      <SubmitButton pending={pending} testId="case-submit">
        Approve and store test case
      </SubmitButton>
    </form>
  );
}
