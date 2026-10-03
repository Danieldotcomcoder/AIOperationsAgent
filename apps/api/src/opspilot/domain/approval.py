"""Approval preconditions, expressed as a pure decision (ADR 0004).

The same function is called in two places, deliberately:

1. in the API, when a supervisor decides;
2. in the execute worker, immediately before the write.

Both callers pass freshly-read state. The worker trusts nothing that arrived in the
queue message except the identifiers. Duplicating the check is the point — the queue
is not a trust boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from opspilot.domain.enums import ActionStatus, MembershipStatus, Role


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """Everything the decision depends on, read fresh at decision time."""

    action_status: ActionStatus
    action_organization_id: UUID
    action_payload_hash: str
    action_source_version: int
    action_expires_at: datetime

    approver_organization_id: UUID
    approver_role: Role
    approver_status: MembershipStatus

    # What the client is asserting it saw. A mismatch is a conflict, not a re-read.
    asserted_payload_hash: str
    current_source_version: int

    now: datetime


@dataclass(frozen=True, slots=True)
class ApprovalCheck:
    permitted: bool
    reason: str | None = None
    error_code: str | None = None

    @staticmethod
    def deny(reason: str, error_code: str) -> ApprovalCheck:
        return ApprovalCheck(permitted=False, reason=reason, error_code=error_code)


ALLOWED = ApprovalCheck(permitted=True)


def evaluate_approval(ctx: ApprovalContext) -> ApprovalCheck:
    """Checks are ordered from least to most disclosive.

    Tenancy first, so that a cross-organization attempt never learns anything about
    the action's state, payload or version.
    """
    if ctx.approver_organization_id != ctx.action_organization_id:
        return ApprovalCheck.deny("action belongs to another organization", "resource.not_found")
    if ctx.approver_status is not MembershipStatus.active:
        return ApprovalCheck.deny("membership is revoked", "auth.forbidden")
    if ctx.approver_role is not Role.supervisor:
        return ApprovalCheck.deny("approval requires the supervisor role", "auth.forbidden")
    if ctx.action_status is not ActionStatus.pending:
        return ApprovalCheck.deny(
            f"action is {ctx.action_status}, not pending", "state.illegal_transition"
        )
    if ctx.now >= ctx.action_expires_at:
        return ApprovalCheck.deny("proposal expired", "approval.expired")
    if ctx.asserted_payload_hash != ctx.action_payload_hash:
        # The supervisor approved a payload that is not the one on file. Never
        # resolve this by reading the current payload; it defeats the whole control.
        return ApprovalCheck.deny(
            "payload hash does not match the stored proposal", "approval.stale"
        )
    if ctx.current_source_version != ctx.action_source_version:
        return ApprovalCheck.deny(
            "the source record changed since the proposal was created", "approval.stale"
        )
    return ALLOWED
