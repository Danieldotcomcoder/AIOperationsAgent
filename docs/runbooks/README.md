# Runbooks

| Runbook | Use it for |
| --- | --- |
| [LOCAL-MODELS.md](LOCAL-MODELS.md) | Choosing the fake or OpenRouter model and embeddings; keys |
| [APPROVALS.md](APPROVALS.md) | The approval → outbox → execution path; recovery drills |
| [RETENTION-CANCELLATION.md](RETENTION-CANCELLATION.md) | Cancellation, trace/audit retention |
| [WEEKS-2-3-VALIDATION.md](WEEKS-2-3-VALIDATION.md) | The human review gates for investigations and retrieval |
| [EVALUATION.md](EVALUATION.md) | The 80-case harness, baseline and reports |
| [DEPLOY.md](DEPLOY.md) | Production build, deploy, rollback, backup, restore |
| [INCIDENTS.md](INCIDENTS.md) | Model outage, backlog, uncertain write, bad prompt, data loss, cost, security |

Pilot targets to verify in a drill on the server: at most 24 hours of lost data,
recovery within four hours.
