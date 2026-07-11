"""add_opportunity_review_fields

Revision ID: 45dd6b740edc
Revises: 857f65e5567e
Create Date: 2026-07-06 20:02:51.089614

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "45dd6b740edc"
down_revision: str | Sequence[str] | None = "857f65e5567e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.add_column(
            sa.Column("evidence_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            "idx_opportunities_evidence_updated_at",
            ["evidence_updated_at"],
            unique=False,
        )

    with op.batch_alter_table("opportunity_signals") as batch_op:
        batch_op.add_column(
            sa.Column(
                "association_source",
                sa.String(length=20),
                nullable=False,
                server_default="clustering",
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_excluded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        # Create check constraint
        batch_op.create_check_constraint(
            "chk_opp_signals_assoc_source",
            "association_source IN ('clustering', 'manual')",
        )
        # Create indexes
        batch_op.create_index(
            "idx_opp_signals_opp_id_is_excluded",
            ["opportunity_id", "is_excluded"],
            unique=False,
        )
        batch_op.create_index(
            "idx_opp_signals_sig_id_is_excluded",
            ["signal_id", "is_excluded"],
            unique=False,
        )

    # Backfill updated_at from created_at
    op.execute("UPDATE opportunity_signals SET updated_at = created_at")

    # Backfill evidence_updated_at for opportunities
    op.execute("""
        UPDATE opportunities
        SET evidence_updated_at = (
            SELECT MAX(created_at)
            FROM opportunity_signals
            WHERE opportunity_signals.opportunity_id = opportunities.id
        )
        WHERE EXISTS (
            SELECT 1
            FROM opportunity_signals
            WHERE opportunity_signals.opportunity_id = opportunities.id
        )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("opportunity_signals") as batch_op:
        batch_op.drop_index("idx_opp_signals_sig_id_is_excluded")
        batch_op.drop_index("idx_opp_signals_opp_id_is_excluded")
        batch_op.drop_constraint("chk_opp_signals_assoc_source", type_="check")
        batch_op.drop_column("updated_at")
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("is_excluded")
        batch_op.drop_column("association_source")

    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.drop_index("idx_opportunities_evidence_updated_at")
        batch_op.drop_column("evidence_updated_at")
