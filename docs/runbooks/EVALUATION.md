# Evaluation (Week 7)

```sh
pnpm eval verify                      # the frozen case file is intact
pnpm eval run                         # dev split, scripted model: no spend
pnpm eval run --split held_out        # the held-out estimate
pnpm eval run --split all --live      # configured provider (spends quota)
pnpm eval run --live --repeat 3       # release-candidate stability
pnpm eval run --only N01 T04 --keep   # chosen cases; keep the database to inspect
```

Reports land in `evals/reports/eval_<split>_<id>.json` (every run, every answer, every
check, the baseline's answers) and `.md` (gates and tables). Exit code 1 means a
deterministic gate failed.

## How a run works

Each run creates its own database (`opspilot_eval_<id>_test`), migrates and seeds it
with the deterministic dataset, uses Redis databases 13–15, starts its own API on a
free port and drops everything afterwards. The development database is never touched.
Worker code runs in-process through the same entry points Celery calls; the queue hop
itself is covered by `scripts/probe_worker_recovery.py`.

- **35 deterministic cases** (tenancy/roles 15, approval/staleness 10, duplicates and
  crashes 10) always use the scripted model and pass or fail on the system contract.
- **45 model-dependent cases** (normal 20, missing/ambiguous 15, prompt injection 10)
  use the evaluated configuration. Code grades schema, citations against grants,
  which orders were referenced, abstention markers, bounds, tenancy of every granted
  id and hijacked proposals. Semantic quality is a person's job: the human rubric
  stays empty in the case file until someone grades it.
- **The baseline** answers the same model cases with SQL, a template and a fixed
  policy link, graded by the same checks.

## Rules (from the evaluation plan)

- Deterministic and model-dependent denominators are reported separately; repeats
  count as runs, never as cases.
- Scripted-model results verify the harness, not quality; the report says so and
  marks cost/latency/missing-evidence gates "not meaningful".
- Held-out cases stay out of prompts and implementation work. A held-out failure that
  guides a fix becomes a regression case; replenish the holdout in a new version
  (`evals/cases/v2`) instead of editing v1 — `verify` refuses an edited split.
- Free-tier quotas cap live runs: 45 cases × up to 3 calls exceeds 50 requests/day.
  Use `--only` to spread a live evaluation over days; record which reports combine.

## Results so far

- 2026-10-03, scripted model, all 80: deterministic **35/35** pass; model cases ran
  end to end; the scripted model cannot abstain (expected).
- 2026-10-03, live smoke (Nemotron Ultra free + Liquid embeddings), 3 cases: all code
  checks passed; 40–79 s per run; human grading pending.
