"""Tenant-scoped data access (ADR 0002).

Every read of a tenant-owned table goes through this class. It is not a generic
repository abstraction for its own sake — it exists so that the organization filter is
applied in one place that can be reviewed, rather than in two hundred call sites that
cannot.

A bare ``select(Order)`` anywhere outside this module is a review-blocking defect.
The tenancy audit script (``scripts/audit_tenancy.py``) greps for exactly that.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from opspilot.core.errors import ResourceNotFound
from opspilot.models.base import Base, TenantMixin

ModelT = TypeVar("ModelT", bound=Base)


class TenantScopedRepository:
    """Bound to one organization for its whole lifetime.

    Construct it from a verified ``Principal`` (API) or from a membership row
    re-resolved at execution time (worker). Never from a request parameter.
    """

    def __init__(self, session: Session, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id

    # -- query construction -------------------------------------------------
    def query(self, model: type[ModelT]) -> Select[tuple[ModelT]]:
        if not issubclass(model, TenantMixin):
            raise TypeError(
                f"{model.__name__} is not tenant-owned; query it directly and justify why."
            )
        return select(model).where(model.organization_id == self.organization_id)

    # -- reads --------------------------------------------------------------
    def get(self, model: type[ModelT], entity_id: uuid.UUID) -> ModelT | None:
        return self.session.scalars(self.query(model).where(model.id == entity_id)).one_or_none()

    def get_or_404(self, model: type[ModelT], entity_id: uuid.UUID) -> ModelT:
        """A row in another organization raises ``ResourceNotFound``, never a 403.

        Distinguishing "exists elsewhere" from "does not exist" is a disclosure.
        """
        found = self.get(model, entity_id)
        if found is None:
            # The message is public; the table name is internal detail, kept in context.
            raise ResourceNotFound(
                "Not found",
                model=model.__tablename__,
                entity_id=str(entity_id),
            )
        return found

    def find_by(self, model: type[ModelT], **filters: Any) -> ModelT | None:
        stmt = self.query(model)
        for field, value in filters.items():
            stmt = stmt.where(getattr(model, field) == value)
        return self.session.scalars(stmt).one_or_none()

    def list_by(
        self, model: type[ModelT], *, limit: int = 50, offset: int = 0, **filters: Any
    ) -> Sequence[ModelT]:
        """``limit`` is capped by the caller's contract, never by the model's request.

        Tool result sizes are bounded in the tool definition, not here.
        """
        stmt = self.query(model)
        for field, value in filters.items():
            stmt = stmt.where(getattr(model, field) == value)
        return self.session.scalars(stmt.limit(limit).offset(offset)).all()

    # -- writes -------------------------------------------------------------
    def add(self, entity: ModelT) -> ModelT:
        """Stamps the organization rather than trusting the caller to have done it."""
        if not isinstance(entity, TenantMixin):
            raise TypeError(f"{type(entity).__name__} is not tenant-owned")
        entity.organization_id = self.organization_id
        self.session.add(entity)
        return entity
