# Evaluation harness

80 labelled cases, versioned with the code: 40 development, 40 held out. Thresholds
and the rubric live in [docs/plan/03-evaluation-gates.md](../docs/plan/03-evaluation-gates.md);
how to run it is in [docs/runbooks/EVALUATION.md](../docs/runbooks/EVALUATION.md).

| Category | Count | Kind | Expected behaviour |
| --- | --- | --- | --- |
| Normal investigations | 20 | model | Correct facts, relevant policy, sensible proposed task |
| Missing or ambiguous evidence | 15 | model | Explicit uncertainty, no invented resolution |
| Tenant and role boundaries | 15 | deterministic | No data leakage, no unauthorized approval |
| Prompt injection in notes/requests | 10 | model | No permission change, no unauthorized tool use |
| Approval and stale state | 10 | deterministic | Reject stale, expired, changed or unapproved actions |
| Timeouts, duplicates, worker crashes | 10 | deterministic | Bounded failure or safe recovery, no duplicate effects |

## Layout

    cases/v1/cases.json         the frozen set (split checksums; human_review blocks open)
    cases/seed_scenarios.json   the 30 seeded exception cases the model cases come from
    harness/                    environment, cases, deterministic checks, grading, report
    reports/                    generated output (git-ignored)

## Where expectations come from

Never from a model. Model cases take their facts from the seeded database and their
expected behaviour from the seed generator's construction rule for each exception
class; deterministic cases take theirs from the system contract. Each case records
its `label_source`. A person confirms the derived expectations and fills the
`human_review` blocks — the investigation-success and citation-audit gates stay
"pending" until they do.

## Rules that keep the numbers meaningful

1. **Held-out cases stay out of implementation prompts.** Once a held-out failure
   guides a fix, that case becomes a regression test and the holdout is replenished
   in a new version.
2. **Deterministic and model-dependent cases report separate denominators.**
3. **Model-dependent cases run three times for a release candidate** (`--repeat 3`).
4. **Repeated runs are counted separately from unique cases.**
5. **Every report records the case-file version, split checksums, prompt version,
   model and commit.**

## Earlier Week 2–3 tools

`scripts/evaluate_week2.py` and `scripts/evaluate_week3.py` collect live
investigations and score retrieval questions for the human gates described in
[the validation runbook](../docs/runbooks/WEEKS-2-3-VALIDATION.md). Both gates still
need human labels and grading.
