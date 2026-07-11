"""add_source_ops_fields

Revision ID: d445f5753c74
Revises: 7fa513398108
Create Date: 2026-07-07 09:54:18.221047

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d445f5753c74"
down_revision: str | Sequence[str] | None = "7fa513398108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add trigger_type column with 'cli' as default
    op.add_column(
        "collection_runs",
        sa.Column(
            "trigger_type",
            sa.Enum(
                "cli",
                "web",
                "scheduled",
                name="collectiontriggertype",
                native_enum=False,
            ),
            nullable=False,
            server_default="cli",
        ),
    )

    # 2. Update status column constraint to include 'abandoned'
    with op.batch_alter_table("collection_runs") as batch_op:
        batch_op.alter_column(
            "status",
            type_=sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                "abandoned",
                name="collectionrunstatus",
                native_enum=False,
            ),
            existing_type=sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                name="collectionrunstatus",
                native_enum=False,
            ),
            nullable=False,
        )

    # 3. Create indexes
    op.create_index(
        "uq_collection_runs_source_running",
        "collection_runs",
        ["source_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "idx_collection_runs_source_started",
        "collection_runs",
        ["source_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "idx_collection_runs_status_started",
        "collection_runs",
        ["status", "started_at"],
        unique=False,
    )
    op.create_index(
        "idx_collection_runs_trigger_started",
        "collection_runs",
        ["trigger_type", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Convert 'abandoned' status to 'failed' and write to error_summary
    op.execute(
        "UPDATE collection_runs SET status = 'failed', error_summary = 'Abandoned run converted to failed during downgrade.' WHERE status = 'abandoned'"
    )

    # 2. Drop indexes
    op.drop_index("idx_collection_runs_trigger_started", table_name="collection_runs")
    op.drop_index("idx_collection_runs_status_started", table_name="collection_runs")
    op.drop_index("idx_collection_runs_source_started", table_name="collection_runs")
    op.drop_index("uq_collection_runs_source_running", table_name="collection_runs")

    # 3. Revert status column constraint
    with op.batch_alter_table("collection_runs") as batch_op:
        batch_op.alter_column(
            "status",
            type_=sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                name="collectionrunstatus",
                native_enum=False,
            ),
            existing_type=sa.Enum(
                "running",
                "succeeded",
                "partial",
                "failed",
                "abandoned",
                name="collectionrunstatus",
                native_enum=False,
            ),
            nullable=False,
        )

    # 4. Drop trigger_type column
    with op.batch_alter_table("collection_runs") as batch_op:
        batch_op.drop_column("trigger_type")
