"""add_retired_at_and_retired_reason_to_opportunities

Revision ID: 14bea315aff7
Revises: 11ed027cca91
Create Date: 2026-07-12 19:37:54.034271

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '14bea315aff7'
down_revision: Union[str, Sequence[str], None] = '11ed027cca91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.add_column(sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("retired_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.drop_column("retired_reason")
        batch_op.drop_column("retired_at")
