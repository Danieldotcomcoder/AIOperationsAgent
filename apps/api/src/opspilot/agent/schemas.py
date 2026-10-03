"""The structured output contract.

The model does not return prose that we parse hopefully. It returns this, or the run
fails validation. Three properties matter and each is enforced here rather than in a
prompt:

* a factual claim carries evidence ids, which are checked against what the run saw;
* what is unknown is stated as unknown, not resolved by invention;
* an action is a *proposal* with no execution semantics whatsoever.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from opspilot.domain.enums import ActionType


class Finding(BaseModel):
    """One claim, with the evidence that supports it."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(max_length=600)
    #: Whether this is something the data shows, or something the model concluded.
    #: The rubric grades these differently, so the model must commit to one.
    kind: str = Field(pattern="^(observed_fact|inference)$")
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class PolicyCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: pol:<document_version_id>#<chunk_ordinal>, validated against evidence grants.
    citation_id: str = Field(max_length=80)
    #: What the passage is taken to require. Graded for actual support in review.
    relevance: str = Field(max_length=400)


class ProposedActionDraft(BaseModel):
    """What the model may *suggest*. It carries no authority and no execution path.

    Note what is absent: no organization id, no approver, no task id, no status. The
    backend supplies every one of those. The model cannot address another tenant, and
    cannot claim an action happened.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: ActionType = ActionType.create_followup_task
    description: str = Field(max_length=500)
    #: Business days from now. An absolute date from the model would be ungrounded;
    #: the backend resolves this against the clock.
    due_in_days: int = Field(ge=1, le=30)
    rationale: str = Field(max_length=600)


class InvestigationResult(BaseModel):
    """The model's complete answer. Anything outside this shape is rejected."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=1500)
    findings: list[Finding] = Field(default_factory=list, max_length=15)
    policy_citations: list[PolicyCitation] = Field(default_factory=list, max_length=10)
    #: Explicit gaps. A run that needs this and leaves it empty fails the rubric.
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    proposed_action: ProposedActionDraft | None = None

    def cited_evidence_ids(self) -> set[str]:
        ids = {e for f in self.findings for e in f.evidence_ids}
        ids |= {c.citation_id for c in self.policy_citations}
        return ids


class ValidatedInvestigation(BaseModel):
    """What the backend persists: the model's answer plus what we established about it."""

    model_config = ConfigDict(extra="forbid")

    result: InvestigationResult
    unsupported_citations: list[str] = Field(default_factory=list)
    validated_at: datetime
    prompt_version: str
    model_name: str
