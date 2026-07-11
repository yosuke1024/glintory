"""add_runtime_audit_fields

Revision ID: ef99f3858ac3
Revises: 9d9d5e869311
Create Date: 2026-07-11 20:45:24.931983

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ef99f3858ac3"
down_revision: str | Sequence[str] | None = "9d9d5e869311"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('opportunity_enrichments', sa.Column('runtime_commit', sa.String(length=100), nullable=True))
    op.add_column('opportunity_enrichments', sa.Column('runtime_binary_sha256', sa.String(length=64), nullable=True))
    with op.batch_alter_table("opportunity_enrichments", schema=None) as batch_op:
        batch_op.drop_constraint(
            "chk_opportunity_enrichments_error_code", type_="check"
        )
        batch_op.create_check_constraint(
            "chk_opportunity_enrichments_error_code",
            condition="error_code IN ('LLM_MODEL_DOWNLOAD_FAILED', 'LLM_MODEL_CHECKSUM_FAILED', 'LLM_RUNTIME_START_FAILED', 'LLM_TIMEOUT', 'LLM_INVALID_JSON', 'LLM_SCHEMA_VALIDATION_FAILED', 'LLM_INFERENCE_FAILED', 'LLM_INPUT_BUDGET_EXCEEDED', 'LLM_PROVIDER_CONTRACT_FAILED', 'LLM_CONFIGURATION_INVALID') OR error_code IS NULL",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("opportunity_enrichments", schema=None) as batch_op:
        batch_op.drop_constraint(
            "chk_opportunity_enrichments_error_code", type_="check"
        )
        batch_op.create_check_constraint(
            "chk_opportunity_enrichments_error_code",
            condition="error_code IN ('LLM_MODEL_DOWNLOAD_FAILED', 'LLM_MODEL_CHECKSUM_FAILED', 'LLM_RUNTIME_START_FAILED', 'LLM_TIMEOUT', 'LLM_INVALID_JSON', 'LLM_SCHEMA_VALIDATION_FAILED', 'LLM_INFERENCE_FAILED', 'LLM_INPUT_BUDGET_EXCEEDED') OR error_code IS NULL",
        )
    op.drop_column('opportunity_enrichments', 'runtime_binary_sha256')
    op.drop_column('opportunity_enrichments', 'runtime_commit')
