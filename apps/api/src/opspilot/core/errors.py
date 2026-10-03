"""One exception hierarchy, mapped to HTTP status and to a stable ``error_code``.

The codes are part of the contract: evaluation cases assert on them, the UI switches
on them, and the runbook refers to them. Renaming one is a breaking change.
"""

from __future__ import annotations

from typing import Any


class OpsPilotError(Exception):
    """Base class. Every deliberate failure in the system is a subclass of this."""

    status_code: int = 500
    error_code: str = "internal.error"
    # Whether the message is safe to show a user. Internal detail is logged, not sent.
    public: bool = False

    def __init__(self, message: str = "", **context: Any) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        self.context = context

    def to_payload(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message if self.public else "Request could not be completed.",
        }


# -- authentication and authorization ---------------------------------------


class NotAuthenticated(OpsPilotError):
    status_code = 401
    error_code = "auth.unauthenticated"
    public = True


class NotAuthorized(OpsPilotError):
    """Authenticated, but wrong organization or insufficient role."""

    status_code = 403
    error_code = "auth.forbidden"
    public = True


class ResourceNotFound(OpsPilotError):
    """Also raised for cross-tenant reads.

    A cross-tenant read must be indistinguishable from a missing row: confirming that
    an id exists in another organization is itself a disclosure. Never raise
    ``NotAuthorized`` for a row that belongs to a different organization.
    """

    status_code = 404
    error_code = "resource.not_found"
    public = True


# -- state and validation ----------------------------------------------------


class ConflictError(OpsPilotError):
    """Payload hash mismatch, stale source version, or an illegal state transition."""

    status_code = 409
    error_code = "state.conflict"
    public = True


class IllegalTransition(ConflictError):
    error_code = "state.illegal_transition"


class StaleProposal(ConflictError):
    error_code = "approval.stale"


class ProposalExpired(ConflictError):
    error_code = "approval.expired"


class RequestInvalid(OpsPilotError):
    status_code = 422
    error_code = "request.invalid"
    public = True


# -- budget ------------------------------------------------------------------


class QuotaExceeded(OpsPilotError):
    status_code = 429
    error_code = "budget.exhausted"
    public = True


class WritesDisabled(OpsPilotError):
    """The operational kill switch is engaged. Reads continue; writes stop."""

    status_code = 503
    error_code = "feature.writes_disabled"
    public = True


# -- upstream and downstream -------------------------------------------------


class ProviderUnavailable(OpsPilotError):
    status_code = 503
    error_code = "provider.unavailable"
    public = True


class ProviderOutputInvalid(OpsPilotError):
    """The model returned something that does not satisfy the response schema."""

    status_code = 502
    error_code = "provider.invalid_output"


class UnsupportedCitation(ProviderOutputInvalid):
    """A citation that was never granted to this run. Never surfaced to a user."""

    error_code = "provider.unsupported_citation"


class DownstreamUncertain(OpsPilotError):
    """The write may or may not have taken effect. Reconcile by key; never retry blind."""

    status_code = 500
    error_code = "ops.uncertain"


class DownstreamRejected(OpsPilotError):
    """Terminal downstream refusal. Not retryable."""

    status_code = 502
    error_code = "ops.rejected"
