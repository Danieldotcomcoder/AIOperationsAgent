"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Created: ${create_date}

Expand/contract reminder (docs/DATA-MODEL.md): the previous application image may
still be running during a rollback window. Add nullable, backfill, switch reads, and
drop in a later release. Never rename a column in one step.
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy  # noqa: F401 - autogenerate emits pgvector types unqualified
import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
