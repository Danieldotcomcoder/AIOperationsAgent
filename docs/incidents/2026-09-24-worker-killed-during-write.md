# Incident report: execution worker killed around the ERP write (failure-injection drill)

**Type:** deliberate failure-injection exercise in an isolated local environment — not
a production incident. **Date:** 2026-09-24. **Severity if real:** high (a duplicated
or lost downstream task is a correctness failure of the only write path).
**Evidence:** `.data/recovery_c89c7dde0aaa47daae6f62b6c131fe7b/report.json` and the
process logs beside it (local, git-ignored). **Drill:** `scripts/probe_worker_recovery.py`.

## What was tested

An approved follow-up task is executed by a Celery worker that calls the simulated ERP
with a deterministic idempotency key. The drill killed the real worker process at the
two worst moments and restarted it:

| Phase | Kill point | Downstream tasks after recovery | Same task id? |
| --- | --- | --- | --- |
| normal | none | 1 | — |
| before | after the claim was committed, before the ERP POST | 1 | — |
| after | after the ERP committed, before the worker recorded success | 1 | yes |

Each phase then received three extra execute deliveries; the task count stayed at one.

## Timeline (local time, 2026-09-24)

- 10:41:47 — normal phase: beat publishes the approval's outbox event; one task.
- 10:41:52 — "before" worker claims the action (`executing` committed) and is
  terminated by the OS before calling the ERP. State: `executing`, no ERP task.
- 10:41:56 — restarted worker receives scheduled `action.reconcile`, looks the key up
  (absent), re-checks approval and source version, sends once: one task.
- 10:42:01 — "after" worker's ERP call commits; the process is killed before the local
  acknowledgement. State: `executing`, one ERP task the database does not know about.
- 10:42:05 — restarted worker reconciles: the key lookup finds the existing task and
  records that same id. No second POST.

## Why it held

1. The claim (`executing` plus an operation-ledger row) commits before any network
   call, so a dead worker leaves evidence instead of silence.
2. The idempotency key derives from stored state (organization, action, payload hash),
   so every retry in any process sends the same key.
3. Every delivery looks the key up before writing; the ERP's unique constraint
   deduplicates concurrent sends; an ambiguous answer becomes
   `reconciliation_required`, never a blind retry.

## What the drill did not show

- **Timing:** the scheduler polled every 2 s and the fixture was aged 61 s to cross
  the unchanged 60 s age gate. With production cadence (sweep every 120 s), worst-case
  recovery is about three minutes — acceptable for a follow-up task, but unmeasured.
- **Linux prefork** child-loss handling, Redis visibility-timeout redelivery and a
  whole-host crash were not exercised (Windows `solo` pool only).
- The same contracts now run on every evaluation: cases D06–D10 in `evals/cases/v1`
  (duplicate delivery, crash after/before the write, unreachable ERP) pass.

## Follow-ups

- Repeat the drill on the production host (Linux, prefork, real beat cadence) and
  record the time from kill to `succeeded` — Week 8 runbook, "verify in a drill".
- Alert when any action sits in `reconciliation_required` (the Metrics page already
  surfaces it in red) so a person sees an unresolved write within minutes.
