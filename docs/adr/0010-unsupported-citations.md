# ADR 0010 — Unsupported citations are stripped, and counted before stripping

**Status:** Accepted — 2026-09-12

## Context

Every factual claim must carry an evidence id, and `evidence_grants` records exactly
what each run was shown. A model can still emit an id it was never given — a
hallucinated uuid, or a real id from a different part of the transcript.

Three responses were considered, and the choice changes what the release gate measures:

* **Fail the run.** Simple and strict, but one bad citation discards an otherwise
  correct investigation and the analyst gets nothing.
* **Display it, flagged.** Puts an ungrounded claim in front of the person about to
  approve an action — the precise failure this system exists to prevent.
* **Strip it.** Safe to display, but if the gate is computed on the cleaned output it
  reads ~100% support by construction and measures nothing.

The third is right and the objection to it is about *where the number comes from*, not
about stripping.

## Decision

Split display from measurement.

1. **Display**: findings whose evidence ids are not all granted are removed from the
   result the user sees. Policy citations are dropped the same way.
2. **Run status**: if anything was stripped, the run carries
   `incomplete_reason = insufficient_evidence`. An investigation missing a claim is
   incomplete, and the UI says so rather than quietly presenting a shorter answer.
3. **Measurement**: the *pre-strip* counts — `cited`, `granted`, `unsupported`, and the
   unsupported ids themselves — are written to a `validation` row in `run_steps` and to
   `agent_runs.result_json` under `validation`. The citation-support gate is computed
   from those raw numbers.

So the gate reads 3/4 = 75% while the analyst sees the 3 supported findings.

## Consequences

- The 95% citation-support gate stays meaningful: stripping cannot inflate it.
- An analyst never sees an ungrounded claim, and never sees a silently shortened answer
  either — the incomplete marker is the honest signal.
- Evaluation can distinguish "the model was wrong" from "the model was stopped", which
  are different problems with different fixes.
- Slightly more state to carry: validation counts live alongside the result rather than
  being derivable from it.

## Revisit when

Measured unsupported-citation rates are near zero across a release candidate, at which
point failing the run becomes affordable and is stricter. Or if stripping is found to
remove the *load-bearing* finding often enough that the remaining answer misleads — in
which case the rule becomes "strip, unless it empties the result, then fail".
