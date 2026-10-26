"use client";

import { useState } from "react";
import { submitExperiment } from "../../lib/actions/experiments";
import type { ExperimentOptions } from "../../lib/api/experiment-types";
import type { Dataset } from "../../lib/api/review-types";
import { ActionMessage, FieldLabel, inputClass, SubmitButton, useWriteForm } from "../reviews/form-parts";

/**
 * Phase 4D experiment setup (ADR-26). One explicit write: nothing runs until the person has
 * chosen every value, confirmed a mock-only run and pressed the start button; the button is
 * disabled while a run is pending, and a second press is ignored. The choices offered are the
 * backend's (`GET /api/v1/experiment-options`): the prompt versions the scripted mock answers
 * and the models with a stored price. There is no mode control: the action always sends
 * `mode: "mock"`.
 */
export function ExperimentForm({ project, datasets, options, initialDataset }: { project: string; datasets: Dataset[]; options: ExperimentOptions; initialDataset: string | null }) {
  const { state, pending, formRef, onSubmit, invalid } = useWriteForm(submitExperiment);
  const preset = initialDataset !== null && datasets.some((d) => String(d.id) === initialDataset) ? initialDataset : "";
  const [datasetId, setDatasetId] = useState(preset);
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [model, setModel] = useState("");
  const [budget, setBudget] = useState("");
  const dataset = datasets.find((d) => String(d.id) === datasetId) ?? null;
  const max = options.limits.max_cases;
  const reset = () => {
    setDatasetId(preset);
    setA("");
    setB("");
    setModel("");
    setBudget("");
  };

  return (
    <form ref={formRef} onSubmit={onSubmit} onReset={reset} noValidate className="space-y-4" data-testid="experiment-form">
      <input type="hidden" name="project" value={project} />
      <div>
        <FieldLabel htmlFor="dataset_id" hint={`A dataset of this project with 1–${max} test cases. Every case runs under both versions.`}>
          Golden dataset
        </FieldLabel>
        <select id="dataset_id" name="dataset_id" required value={datasetId} onChange={(e) => setDatasetId(e.target.value)} aria-invalid={invalid("dataset_id")} aria-describedby="dataset_id-hint" className={inputClass} data-testid="experiment-dataset">
          <option value="" disabled>
            Choose a dataset…
          </option>
          {datasets.map((d) => (
            <option key={d.id} value={String(d.id)} disabled={d.case_count === 0 || d.case_count > max}>
              {d.name} ({d.case_count} case{d.case_count === 1 ? "" : "s"}
              {d.case_count === 0 ? ", nothing to run" : d.case_count > max ? `, more than ${max}` : ""})
            </option>
          ))}
        </select>
        {dataset !== null && (
          <p className="mt-1 text-xs text-ink-2" data-testid="experiment-case-count">
            {dataset.case_count} test case{dataset.case_count === 1 ? "" : "s"} → {2 * dataset.case_count} simulated calls (each case once under A and once under B, no retries).
          </p>
        )}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {(
          [
            ["prompt_version_a", "Prompt version A", a, setA],
            ["prompt_version_b", "Prompt version B", b, setB],
          ] as const
        ).map(([name, label, value, set]) => (
          <div key={name}>
            <FieldLabel htmlFor={name} hint={`A version the scripted mock answers (${options.prompt_versions.map((v) => `${v.prompt_version}: ${v.scripted_inputs} inputs`).join("; ")}).`}>
              {label}
            </FieldLabel>
            <select id={name} name={name} required value={value} onChange={(e) => set(e.target.value)} aria-invalid={invalid(name)} aria-describedby={`${name}-hint`} className={inputClass} data-testid={`experiment-${name.replaceAll("_", "-")}`}>
              <option value="" disabled>
                Choose a prompt version…
              </option>
              {options.prompt_versions.map((v) => (
                <option key={v.prompt_version} value={v.prompt_version}>
                  {v.prompt_version}
                </option>
              ))}
            </select>
          </div>
        ))}
      </div>
      {a !== "" && a === b && (
        <p role="alert" className="text-sm text-critical-ink" data-testid="experiment-same-version">
          Versions A and B must differ.
        </p>
      )}
      <div>
        <FieldLabel htmlFor="model" hint="Recorded with each result and used only to price the synthetic token counts (provider openai, stored catalogue). It does not change the scripted answers.">
          Model
        </FieldLabel>
        <select id="model" name="model" required value={model} onChange={(e) => setModel(e.target.value)} aria-invalid={invalid("model")} aria-describedby="model-hint" className={inputClass} data-testid="experiment-model">
          <option value="" disabled>
            Choose a model…
          </option>
          {options.models.map((m) => (
            <option key={m.model} value={m.model}>
              {m.model} (price {m.price_version})
            </option>
          ))}
        </select>
      </div>
      <div>
        <FieldLabel
          htmlFor="budget_usd"
          hint={`Optional for mock runs. When given (USD, > 0, at most ${options.limits.budget_usd_decimal_places} decimal places), the backend checks the estimated cost of every simulated call before anything runs and refuses the run if any estimate is unknown or the total exceeds it.`}
        >
          Budget (USD)
        </FieldLabel>
        <input id="budget_usd" name="budget_usd" inputMode="decimal" autoComplete="off" value={budget} onChange={(e) => setBudget(e.target.value)} aria-invalid={invalid("budget_usd")} aria-describedby="budget_usd-hint" className={inputClass} placeholder="none" data-testid="experiment-budget" />
      </div>

      <section aria-labelledby="experiment-review-heading" className="rounded-md border border-line bg-canvas px-3 py-3" data-testid="experiment-review">
        <h3 id="experiment-review-heading" className="text-sm font-semibold text-ink">
          Review before running
        </h3>
        <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-ink-3">Execution</dt>
          <dd className="text-ink" data-testid="experiment-review-mode">
            <strong>Mock only</strong>: scripted SYNTHETIC answers; no provider is called, nothing is spent.
          </dd>
          <dt className="text-ink-3">Dataset</dt>
          <dd className="text-ink">{dataset === null ? "not chosen" : `${dataset.name} (${dataset.case_count} cases)`}</dd>
          <dt className="text-ink-3">A vs B</dt>
          <dd className="text-ink">{a === "" || b === "" ? "not chosen" : `${a} vs ${b}`}</dd>
          <dt className="text-ink-3">Model</dt>
          <dd className="text-ink">{model === "" ? "not chosen" : model}</dd>
          <dt className="text-ink-3">Budget</dt>
          <dd className="text-ink" data-testid="experiment-review-budget">
            {budget.trim() === "" ? "none (mock run)" : `$${budget.trim()} USD, checked before execution`}
          </dd>
          <dt className="text-ink-3">Bounds</dt>
          <dd className="text-ink-2">
            At most {max} cases; stopped after {options.limits.run_time_limit_seconds} s; at most {options.limits.max_concurrent_runs} runs at once. A refused or stopped run stores nothing.
          </dd>
        </dl>
      </section>

      <div className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2">
        <input id="confirm_mock" name="confirm_mock" type="checkbox" value="yes" required aria-invalid={invalid("confirm_mock")} className="mt-1" data-testid="experiment-confirm" />
        <label htmlFor="confirm_mock" className="text-sm text-ink">
          I want to run this experiment with the deterministic mock provider. I understand its results are SYNTHETIC, are stored in this project, and say nothing about a real model.
        </label>
      </div>
      <ActionMessage state={state} testId="experiment-form-result" />
      <SubmitButton pending={pending} testId="experiment-submit" pendingLabel="Running…">
        Run mock experiment
      </SubmitButton>
      {pending && (
        <p role="status" className="text-xs text-ink-3" data-testid="experiment-running">
          Running in mock mode… the button stays disabled until the backend answers.
        </p>
      )}
    </form>
  );
}
