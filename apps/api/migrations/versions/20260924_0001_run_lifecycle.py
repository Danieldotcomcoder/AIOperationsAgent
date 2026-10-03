"""Cancellation, retention marker and durable budget receipt.

Revision ID: 20260924_lifecycle
Revises: 6de0a0404ed5
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260924_lifecycle"
down_revision = "6de0a0404ed5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("trace_pruned_at", sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
    op.add_column("agent_runs", sa.Column("budget_reservation", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("agent_runs", "budget_reservation")
    op.drop_column("agent_runs", "cancel_requested_at")
    op.drop_column("agent_runs", "trace_pruned_at")
