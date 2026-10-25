"""Deterministic mock provider for experiments (Phase 4C, B4). It never calls any service.

It stands in for the project's one provider adapter (OpenAI, ADR-05) and answers from a fixed
script of SYNTHETIC outputs keyed by (prompt version, SHA-256 of the case input)
(`golden.SCRIPT`). There is no randomness, clock or network: the same request always gets the
same answer, so a rerun of an experiment reproduces its results exactly.

- An input the script does not know gets `mock_no_scripted_output`: no output, no usage. The
  mock never invents an answer for an arbitrary case (and never echoes the expected output,
  which would pass by construction).
- A scripted `provider_timeout` / `provider_error` simulates a failed call: no output, usage
  unknown.
- Exactly one physical attempt per request: the mock does not retry, so one experiment result
  is one attempt and nothing is merged.
- The model name is recorded identity only (and selects the price); it does not change the
  scripted answer.
"""

from dataclasses import dataclass

from app.services.experiments import golden
from app.services.experiments.golden import Scripted

SIMULATED_PROVIDER = "openai"  # for price lookup: the mock simulates the OpenAI adapter
NO_SCRIPTED_OUTPUT = "mock_no_scripted_output"
CALL_ERRORS = ("provider_timeout", "provider_error", NO_SCRIPTED_OUTPUT)


@dataclass(frozen=True)
class ScriptedMockProvider:
    script: dict[tuple[str, str], Scripted]

    def call(self, *, model: str, prompt_version: str, sanitized_input: str) -> Scripted:
        """One simulated attempt. `model` does not change the answer."""
        del model
        answer = self.script.get((prompt_version, golden.input_key(sanitized_input)))
        if answer is None:
            return Scripted(None, None, None, None, error=NO_SCRIPTED_OUTPUT)
        return answer

    def estimate(self, *, model: str, prompt_version: str, sanitized_input: str) -> Scripted:
        """What a call would report, without making it (used by the budget check). For a
        scripted mock the estimate is the answer itself."""
        return self.call(
            model=model, prompt_version=prompt_version, sanitized_input=sanitized_input
        )


GOLDEN_PROVIDER = ScriptedMockProvider(golden.SCRIPT)
