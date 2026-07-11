"""add_opportunity_clustering_fields

Revision ID: 187355bd71bf
Revises: da4fadf39e75
Create Date: 2026-07-06 19:44:03.793505

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "187355bd71bf"
down_revision: str | Sequence[str] | None = "da4fadf39e75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.add_column(
            sa.Column("generation_method", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cluster_version", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_clustered_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.drop_column("last_clustered_at")
        batch_op.drop_column("cluster_version")
        batch_op.drop_column("generation_method")
