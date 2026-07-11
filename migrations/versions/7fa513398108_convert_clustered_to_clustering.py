"""convert_clustered_to_clustering

Revision ID: 7fa513398108
Revises: 45dd6b740edc
Create Date: 2026-07-07 08:15:23.724092

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7fa513398108"
down_revision: str | Sequence[str] | None = "45dd6b740edc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema. Convert 'clustered' association source to 'clustering'."""
    op.execute(
        "UPDATE opportunity_signals "
        "SET association_source = 'clustering' "
        "WHERE association_source = 'clustered'"
    )


def downgrade() -> None:
    """Downgrade schema.
    Note: It is not possible to safely rollback 'clustering' back to 'clustered'
    without affecting records that were originally 'clustering' from the start.
    Hence, downgrade is a no-op (pass).
    """
    pass
