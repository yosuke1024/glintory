"""add_collection_runs_check_constraints

Revision ID: e3a24bdd14a1
Revises: d445f5753c74
Create Date: 2026-07-11 09:36:52.760912

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a24bdd14a1"
down_revision: str | Sequence[str] | None = "d445f5753c74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Repair invalid trigger_type to 'cli'
    op.execute(
        "UPDATE collection_runs SET trigger_type = 'cli' "
        "WHERE trigger_type NOT IN ('cli', 'web', 'scheduled')"
    )

    # 2. Repair invalid status to 'failed' and fill metadata
    migration_time = datetime.now(UTC).isoformat()
    op.execute(
        sa.text(
            "UPDATE collection_runs SET "
            "status = 'failed', "
            "completed_at = COALESCE(completed_at, :completed_at), "
            "error_count = CASE WHEN error_count < 1 THEN 1 ELSE error_count END, "
            "error_summary = 'Invalid collection run status was repaired during schema migration.' "
            "WHERE status NOT IN ('running', 'succeeded', 'partial', 'failed', 'abandoned')"
        ).bindparams(completed_at=migration_time)
    )

    # 3. Add CHECK constraints using batch_alter_table
    with op.batch_alter_table("collection_runs", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "chk_collection_runs_status_allowed",
            "status IN ('running', 'succeeded', 'partial', 'failed', 'abandoned')",
        )
        batch_op.create_check_constraint(
            "chk_collection_runs_trigger_type_allowed",
            "trigger_type IN ('cli', 'web', 'scheduled')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("collection_runs", schema=None) as batch_op:
        batch_op.drop_constraint("chk_collection_runs_status_allowed", type_="check")
        batch_op.drop_constraint(
            "chk_collection_runs_trigger_type_allowed", type_="check"
        )
