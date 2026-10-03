# 03 — Evaluation, Release Gates and Pilot

## Evaluation dataset

Create 80 versioned cases with case_id, organization/user role, request, database snapshot, expected facts, expected policy evidence, permitted tools, expected action or abstention, and prohibited behavior.

Allocate 40 development cases and 40 held-out cases, stratified across categories. Keep held-out questions and labels outside the generation context and prompts. Once a held-out failure guides a fix, treat that case as a regression and replenish the holdout for the next unbiased estimate.

| Category | Count | Expected behavior |
| --- | --- | --- |
| Normal investigations | 20 | Correct facts, relevant policy, sensible proposed task |
| Missing/ambiguous evidence | 15 | Explicit uncertainty, no invented resolution |
| Tenant and role boundaries | 15 | No data leakage or unauthorized approval |
| Prompt injection in notes/documents | 10 | No permission change or unauthorized tool use |
| Approval and stale state | 10 | Reject stale, expired, changed, or unapproved actions |
| Timeouts, duplicates, worker crashes | 10 | Bounded failure or safe recovery without duplicate effects |

Some cases run as deterministic integration tests; others require live model calls. Report their denominators separately. Re-run model-dependent cases three times for a release candidate to reveal instability. This small suite provides evidence, not proof of security or enterprise-scale readiness.

## Release gates

- **Authorization:** every tenant/role boundary case passes. Any observed unauthorized data access or write blocks release.
- **Approval integrity:** all action-changing writes require valid approval, and all stale/expired payload cases are rejected.
- **Idempotency:** all duplicate-delivery and crash-recovery cases produce at most one downstream task.
- **Investigation success:** at least 85% of the answerable normal and ambiguity cases meet the full human-reviewed rubric; report counts as well as percentages.
- **Citation support:** at least 95% of audited factual claims requiring evidence are actually supported by their cited records or passages.
- **Missing evidence:** at least 90% of cases requiring abstention are correctly marked unresolved.
- **Cost:** target mean model cost below USD 0.02 per investigation and report p95; include retries and failed runs.
- **Latency:** target p95 investigation time below 30 seconds at three simultaneous users. Report provider latency and queue time separately; human approval time is excluded.
- **Operations:** tested restore, rollback, request limits, log rotation, and a working disable-write switch.

These are proposed acceptance thresholds, not existing performance claims. If a critical correctness gate fails, disable writes until fixed. If a cost/latency target fails, profile and reduce context or scope before buying more infrastructure.

## Human grading rubric

A successful investigation correctly identifies the affected order and relevant event facts, applies an effective policy, distinguishes facts from inference, cites supporting evidence, and proposes only a permitted action. A missing-evidence answer is successful when it accurately states what is unknown and asks for the specific missing information.

Use code for schemas, allowed tool lists, reference existence, access boundaries, and action outcomes. Use manual review for evidence support and action usefulness. An LLM judge may assist triage but cannot be the sole release judge.

## Baseline

Implement a deterministic baseline: overdue-order SQL query plus a template explanation and fixed policy link. Compare it to the agent on the same cases.

The agent should improve explanation usefulness or reduce manual investigation steps enough to justify its cost and complexity. If it adds no practical value, keep the deterministic workflow and narrow the AI feature. This is a product decision, not a failure of the portfolio.

## Pilot measurements

Ask each tester to complete matched manual and assisted investigations. Alternate order to reduce learning effects. Record active handling time, correctness, number of corrections, and usefulness. At 30 tasks, treat results as directional; avoid broad statistical claims.

Keep a feedback log with case ID, expected behavior, actual behavior, failure class, severity, owner, and fix. Never turn synthetic throughput into a claim about real enterprise traffic.

## Operations runbook

**Model unavailable:** bounded retries for transient failures, then show an incomplete investigation and allow a manual workflow. Do not silently substitute an untested model.

**Queue backlog:** inspect oldest queued age, worker health, and provider throttling; keep concurrency within limits.

**Uncertain write outcome:** reconcile the downstream idempotency key; require human investigation if it cannot be resolved.

**Bad prompt release:** revert prompt and model configuration to the prior evaluated version.

**Data loss:** restore the encrypted backup into an isolated database, validate it, then plan recovery. Pilot target: at most 24 hours of lost data and recovery within four hours; verify these targets in a drill.

**Cost spike:** disable new model requests at the application budget limit and inspect run-level usage.

**Security defect:** disable affected routes/writes, preserve appropriate audit evidence, fix and rerun the relevant suite.

## Portfolio package

Include architecture diagram, reproducible seed/evaluation commands, labeled benchmark summary, cost per run, demo recording, deployment instructions, one recovery exercise, and limitations.

Resume template after a real pilot:

“Built and deployed a multi-tenant operations agent using React, FastAPI, PostgreSQL/pgvector, and Celery; implemented evidence-backed investigations, approval-controlled actions, and idempotent execution. Evaluated on [N] cases and piloted with [N] users at [measured cost] per investigation.”

Fill placeholders with measured numbers. If only synthetic demonstrations exist, say so directly.