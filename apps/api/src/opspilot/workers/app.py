"""Celery application.

Queue separation matters here: a slow investigation must never delay an approved
write, and the write queue runs at concurrency 1 so the write path is serialized per
host.

Settings that are load-bearing rather than cosmetic:

``acks_late``
    A task is acknowledged after it finishes, so a killed worker returns the message
    to the queue. That is what makes crash recovery possible — and it is also why
    every task must be idempotent, because the message can be delivered twice.
``reject_on_worker_lost``
    A worker killed by the OOM killer re-queues rather than silently vanishing.
``prefetch_multiplier=1``
    No hoarding. A worker holding ten messages it has not started is ten messages that
    are invisible to a healthy worker.
"""

from __future__ import annotations

from celery import Celery
from celery.signals import setup_logging

from opspilot.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "opspilot", broker=settings.celery_broker_url, backend=settings.celery_result_backend
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # -- delivery semantics (ADR 0003) ------------------------------------
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # -- bounds ------------------------------------------------------------
    # Hard limit sits above the run deadline: the orchestrator should stop itself and
    # write a useful incomplete result. The hard kill is the backstop, not the plan.
    task_time_limit=int(settings.run_deadline_seconds) + 30,
    task_soft_time_limit=int(settings.run_deadline_seconds) + 10,
    task_default_queue="investigate",
    task_routes={
        "run.investigate": {"queue": "investigate"},
        "outbox.dispatch": {"queue": "outbox"},
        "run.requeue_stranded": {"queue": "outbox"},
        "run.fail_abandoned": {"queue": "outbox"},
        "action.execute": {"queue": "execute"},
        "action.reconcile": {"queue": "execute"},
        "action.reconcile_pending": {"queue": "outbox"},
        "approval.expire_stale": {"queue": "outbox"},
        "retention.*": {"queue": "outbox"},
    },
    beat_schedule={
        # Backstop for the dispatcher: anything committed to the outbox but not
        # published — because the process died between commit and publish — is picked
        # up here. This is the loop that makes "at-least-once" true.
        "outbox-sweep": {"task": "outbox.dispatch", "schedule": 10.0},
        # Safety net for run creation: the row is committed before the message is
        # published, so a broker blink leaves a queued run with no message. Claiming
        # is idempotent, which is what makes blind re-publishing safe.
        "requeue-stranded-runs": {"task": "run.requeue_stranded", "schedule": 60.0},
        # Expire proposals past 24 h so the approval inbox cannot accumulate
        # decisions that would be invalid anyway.
        "expire-proposals": {"task": "approval.expire_stale", "schedule": 300.0},
        # Anything stuck in reconciliation_required gets another lookup attempt.
        "reconcile-uncertain": {"task": "action.reconcile_pending", "schedule": 120.0},
        "fail-abandoned-runs": {"task": "run.fail_abandoned", "schedule": 60.0},
        "prune-traces": {"task": "retention.prune_traces", "schedule": 3600.0},
        "prune-audit": {"task": "retention.prune_audit", "schedule": 3600.0},
    },
)

# Import for side effects: task registration.
celery_app.autodiscover_tasks(["opspilot.workers"], force=True)


@setup_logging.connect
def _configure_worker_logging(**_: object) -> None:
    """Celery would otherwise install its own handlers and we would lose JSON output."""
    from opspilot.core.logging import configure_logging

    configure_logging(settings)
