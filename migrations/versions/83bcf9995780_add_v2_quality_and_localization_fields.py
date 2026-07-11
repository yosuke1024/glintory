"""add_v2_quality_and_localization_fields

Revision ID: 83bcf9995780
Revises: ef99f3858ac3
Create Date: 2026-07-11 23:55:29.601963

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '83bcf9995780'
down_revision: str | Sequence[str] | None = 'ef99f3858ac3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite compatibility: use batch operations for modifying tables and define server defaults
    with op.batch_alter_table('signals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('signal_role', sa.Enum('demand', 'supply', 'context', 'unknown', name='signalrole', native_enum=False), nullable=False, server_default='unknown'))

    with op.batch_alter_table('opportunity_signals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('evidence_summary_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('evidence_summary_ja', sa.Text(), nullable=True))

    with op.batch_alter_table('collection_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('skipped_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('error_type', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('sanitized_error_message', sa.Text(), nullable=True))

    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title_en', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('title_ja', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('summary_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('summary_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('target_user_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('target_user_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('problem_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('problem_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('current_workaround_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('current_workaround_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('existing_solution_gap_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('existing_solution_gap_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('mvp_direction_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('mvp_direction_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('why_selected_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('why_selected_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('risks_en', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('risks_ja', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('enrichment_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('translation_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('enrichment_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('enriched_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('independent_evidence_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('demand_evidence_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('source_type_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('source_domain_count', sa.Integer(), nullable=False, server_default='0'))

    # Data migration: archive/reject existing v1 opportunities
    op.execute("UPDATE opportunities SET status = 'rejected', current_scoring_version = 'v1' WHERE current_scoring_version IS NULL OR current_scoring_version = 'v1';")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.drop_column('source_domain_count')
        batch_op.drop_column('source_type_count')
        batch_op.drop_column('demand_evidence_count')
        batch_op.drop_column('independent_evidence_count')
        batch_op.drop_column('enriched_at')
        batch_op.drop_column('enrichment_error')
        batch_op.drop_column('translation_status')
        batch_op.drop_column('enrichment_status')
        batch_op.drop_column('risks_ja')
        batch_op.drop_column('risks_en')
        batch_op.drop_column('why_selected_ja')
        batch_op.drop_column('why_selected_en')
        batch_op.drop_column('mvp_direction_ja')
        batch_op.drop_column('mvp_direction_en')
        batch_op.drop_column('existing_solution_gap_ja')
        batch_op.drop_column('existing_solution_gap_en')
        batch_op.drop_column('current_workaround_ja')
        batch_op.drop_column('current_workaround_en')
        batch_op.drop_column('problem_ja')
        batch_op.drop_column('problem_en')
        batch_op.drop_column('target_user_ja')
        batch_op.drop_column('target_user_en')
        batch_op.drop_column('summary_ja')
        batch_op.drop_column('summary_en')
        batch_op.drop_column('title_ja')
        batch_op.drop_column('title_en')

    with op.batch_alter_table('collection_runs', schema=None) as batch_op:
        batch_op.drop_column('sanitized_error_message')
        batch_op.drop_column('error_type')
        batch_op.drop_column('skipped_count')

    with op.batch_alter_table('opportunity_signals', schema=None) as batch_op:
        batch_op.drop_column('evidence_summary_ja')
        batch_op.drop_column('evidence_summary_en')

    with op.batch_alter_table('signals', schema=None) as batch_op:
        batch_op.drop_column('signal_role')

