# Weeks 2–3 validation

## Current evidence (2026-09-25)

OpenRouter Nemotron Ultra produced structured results for five distinct seeded orders
in the attempted ten-case batch. The sixth case hit provider unavailability when the
account exhausted its daily free-request allowance. These are completed generations,
not five human-approved answers. No proposal was approved or executed.

Evidence: `evals/reports/week2_a50ae81e5a23462eaf438b433090856f.json`.
It includes database fact snapshots, outputs, citations, usage, prompt/model versions,
and blank human-review fields. Earlier probes are separate reports and must not inflate
the unique-case denominator. The initial three-turn failure is preserved in
`week2_198c1d51066c4eec85fa9f14cf1ed4b6.json`; the corrected probe is in
`week2_c91ba011c6b744c283500c18b381a424.json`.

The batch used fake embeddings and found no compatible policy evidence. It establishes
chat/tool connectivity and structured output, not semantic policy quality. The root
configuration now selects Liquid through OpenRouter; successful live embeddings still
need verification after the provider quota resets.

## Resume the investigation batch

Start local PostgreSQL/Redis, then from `apps/api` run:

```powershell
uv run python ../../scripts/evaluate_week2.py --start 5 --count 5
```

The command uses normal quotas and durable run claims, stopping at the first unusable
result. It runs the worker entrypoint synchronously, so no worker needs to be started.
Enqueued duplicate messages are harmless when a worker later claims them. Results and
unapproved proposals remain in the local development database for inspection. Do not
approve evaluation proposals. Reports are git-ignored, contain synthetic facts only,
and do not include keys. Settings are read at each command startup.

For a uniform evaluation after indexing policies, rerun all ten using `--count 10`
and preserve both reports; do not silently replace failures with successful retries.
Reports use the actual evaluation date with current database facts, not the older
seed-time overdue-day counts. Model/prompt/embedding changes require separate results.

A human reviewer fills `human_review` for each distinct case: identity, whether the
facts are correct, any fabricated order reference, and notes. Check each claim against
the database facts and cited evidence, distinguishing observation from inference.
Week 2 passes only with at least 8/10 correct and zero fabricated order references.
Automated schema checks cannot certify factual support or useful proposed actions.

## Retrieval labels and execution

`evals/cases/week3_retrieval_questions.json` contains twenty draft question candidates
for the starter policy. They are explicitly unreviewed; no model-authored answer is
ground truth. Have a human review the policy corpus, refine the questions, and enter
the exact expected supporting passage quote and `expected_document_title` for each
before ranking is scored. Set the top-level `reviewed_by` to the human reviewer.
Use a representative corpus with distractors: returning all passages of one small
document in the top five would be a trivial test, not convincing relevance evidence.

After quota reset, verify one Liquid embedding, index reviewed policies in the Liquid
space, run the twenty labeled queries, and record the top-five passage IDs/texts,
expected evidence, misses, model identity, dates, and token budget. Target 18/20 hits.
From `apps/api`, run:

```powershell
uv run python ../../scripts/evaluate_week3.py --labels ../../evals/cases/week3_retrieval_questions.json
```

The runner refuses missing labels, fake embeddings, and an empty compatible index
before provider requests. It matches the human's expected quote and document title
against the returned top five, preserves each attempt, and stops on provider failure.
Re-run tenant isolation, effective-version, and deletion regressions alongside that
relevance test. Test missing policies and conflicting versions separately rather than
folding them into an answerable-question denominator.

Until those steps and human review are complete, both quality gates remain open.
