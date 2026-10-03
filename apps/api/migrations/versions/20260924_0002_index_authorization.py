"""Record the membership whose authority an indexing job must recheck."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260924_index_auth"
down_revision = "20260924_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_versions", sa.Column("index_requested_by", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        "fk_index_requested_by_tenant_memberships",
        "document_versions",
        "memberships",
        ["organization_id", "index_requested_by"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_index_requested_by_tenant_memberships", "document_versions", type_="foreignkey"
    )
    op.drop_column("document_versions", "index_requested_by")
