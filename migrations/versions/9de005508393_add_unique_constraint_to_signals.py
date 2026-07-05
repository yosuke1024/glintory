"""add_unique_constraint_to_signals

Revision ID: 9de005508393
Revises: cced3ad721f1
Create Date: 2026-07-06 03:03:07.207111

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9de005508393"
down_revision: str | Sequence[str] | None = "cced3ad721f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_signals_source_canonical_url", ["source_id", "canonical_url"]
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_constraint("uq_signals_source_canonical_url", type_="unique")
