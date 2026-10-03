"""State machines as pure functions.

No service assigns a status directly. It calls ``transition_run`` or
``transition_action``, which raises on an undefined edge. An illegal state change then
fails loudly at the moment of the mistake, instead of becoming a data problem
discovered weeks later in a support ticket.

See docs/STATE-MACHINES.md for the diagrams these tables encode.
"""

from __future__ import annotations

from opspilot.core.errors import IllegalTransition
from opspilot.domain.enums import ActionStatus, RunStatus

# --------------------------------------------------------------------------- runs

_RUN_EDGES: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.queued: frozenset({RunStatus.investigating, RunStatus.cancelled, RunStatus.failed}),
    RunStatus.investigating: frozenset(
        {RunStatus.needs_review, RunStatus.completed, RunStatus.failed, RunStatus.cancelled}
    ),
    # A run whose proposal has been decided is finished; the proposal lives on.
    RunStatus.needs_review: frozenset({RunStatus.completed, RunStatus.failed}),
    RunStatus.completed: frozenset(),
    RunStatus.failed: frozenset(),
    RunStatus.cancelled: frozenset(),
}


def can_transition_run(current: RunStatus, target: RunStatus) -> bool:
    return target in _RUN_EDGES[current]


def transition_run(current: RunStatus, target: RunStatus) -> RunStatus:
    if not can_transition_run(current, target):
        raise IllegalTransition(
            f"run: {current} -> {target} is not a defined transition",
            current=current,
            target=target,
        )
    return target


# ------------------------------------------------------------------------ actions

_ACTION_EDGES: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.pending: frozenset(
        {ActionStatus.approved, ActionStatus.rejected, ActionStatus.expired}
    ),
    # Approved work is dispatched through the outbox; it is never executed inline.
    ActionStatus.approved: frozenset({ActionStatus.executing, ActionStatus.expired}),
    ActionStatus.executing: frozenset(
        {ActionStatus.succeeded, ActionStatus.failed, ActionStatus.reconciliation_required}
    ),
    # Reconciliation either proves the effect happened, or proves it did not and we
    # retry with the same idempotency key. There is no third answer.
    ActionStatus.reconciliation_required: frozenset(
        {ActionStatus.succeeded, ActionStatus.executing, ActionStatus.failed}
    ),
    ActionStatus.rejected: frozenset(),
    ActionStatus.expired: frozenset(),
    ActionStatus.succeeded: frozenset(),
    ActionStatus.failed: frozenset(),
}


def can_transition_action(current: ActionStatus, target: ActionStatus) -> bool:
    return target in _ACTION_EDGES[current]


def transition_action(current: ActionStatus, target: ActionStatus) -> ActionStatus:
    if not can_transition_action(current, target):
        raise IllegalTransition(
            f"action: {current} -> {target} is not a defined transition",
            current=current,
            target=target,
        )
    return target
