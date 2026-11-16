# Live A/B inputs in Datasets

The user explicitly selected the six synthetic cases used by the real Groq/Gemini experiment
for a new dataset named `golden_synthetic_v2`. Existing `golden-synthetic-v1` (#58, twelve
scripted mock cases) was not copied or changed.

- [Dataset #98](http://127.0.0.1:3001/datasets/98?project=1), Project 1.
- Six original inputs and category/priority reference labels from
  `demo_app/prompt_ab_scenarios.py`; t01–t06 map to stored cases #317–322.
- Inserted using the existing authenticated dataset/case APIs, with explicit session approval
  represented by the self-declared label `user-approved-live-ticket-import`. Server-side
  redaction applied. No fabricated human name, trace-to-review lineage or provider response.
- The existing dataset rule contract stores `exact` / `json`: the expected two-field object
  is the reference. This is not a newly executed eight-rule live evaluation. Historical live
  rule booleans and 3/6 → 5/6 results stay in the immutable imported experiment reports.
- Original suite/dataset identifiers remain `ticket-ab-v1` / `live-ticket-six-v1`; the new
  requested name is a storage/display alias. Both live prompt variants used these same six
  cases. This is not a different input dataset for V2 alone.
- The billing/bug ambiguity of t02 is retained rather than changing reference labels after
  observing results. A future policy revision needs a separately identified study.
- No model calls, no new experiment run, no application code/schema change. The mock provider
  has no scripted answers for these live-ticket inputs; adding the dataset does not enable
  live execution through the New mock experiment form.

Verification: API read-back confirmed all six inputs, labels, exact-JSON rules and redaction
flags. Running dashboard list and detail pages returned 200; list links to #98 and the detail
contains all six original inputs and case IDs. Evidence in `live-ticket-dataset-import-20261113.json`,
`live-ticket-dataset-ui-check.json` and `live-ticket-dataset-*-browser.txt`.
