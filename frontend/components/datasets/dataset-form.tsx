"use client";

import { submitDataset } from "../../lib/actions/datasets";
import { LIMITS } from "../../lib/api/review-types";
import { ActionMessage, FieldLabel, inputClass, SubmitButton, useWriteForm } from "../reviews/form-parts";

/** Create a dataset (a named set of approved test cases) in the current project. */
export function DatasetForm({ project }: { project: string }) {
  const { state, pending, formRef, onSubmit, invalid } = useWriteForm(submitDataset);
  return (
    <form ref={formRef} onSubmit={onSubmit} noValidate className="flex flex-wrap items-end gap-3" data-testid="dataset-form">
      <input type="hidden" name="project" value={project} />
      <div className="min-w-64 flex-1">
        <FieldLabel htmlFor="dataset-name" hint={`1–${LIMITS.datasetName} characters, unique in this project.`}>
          New dataset name
        </FieldLabel>
        <input
          id="dataset-name"
          name="name"
          required
          maxLength={LIMITS.datasetName}
          autoComplete="off"
          aria-invalid={invalid("name")}
          aria-describedby="dataset-name-hint"
          className={inputClass}
          data-testid="dataset-name"
        />
      </div>
      <SubmitButton pending={pending} testId="dataset-submit">
        Create dataset
      </SubmitButton>
      <div className="basis-full">
        <ActionMessage state={state} testId="dataset-form-result" />
      </div>
    </form>
  );
}
