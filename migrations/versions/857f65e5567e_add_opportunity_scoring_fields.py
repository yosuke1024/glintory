"""add_opportunity_scoring_fields

Revision ID: 857f65e5567e
Revises: 187355bd71bf
Create Date: 2026-07-06 19:47:37.263814

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '857f65e5567e'
down_revision: Union[str, Sequence[str], None] = '187355bd71bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.add_column(
            sa.Column("current_scoring_version", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "idx_opportunities_last_scored_at", ["last_scored_at"], unique=False
        )

    with op.batch_alter_table("score_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column("input_hash", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("as_of_date", sa.Date(), nullable=True)
        )
        batch_op.create_index(
            "uq_score_snapshots_opp_version_input",
            ["opportunity_id", "scoring_version", "input_hash"],
            unique=True,
            sqlite_where=sa.text("input_hash IS NOT NULL"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("score_snapshots") as batch_op:
        batch_op.drop_index("uq_score_snapshots_opp_version_input")
        batch_op.drop_column("as_of_date")
        batch_op.drop_column("input_hash")

    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.drop_index("idx_opportunities_last_scored_at")
        batch_op.drop_column("last_scored_at")
        batch_op.drop_column("current_scoring_version")

