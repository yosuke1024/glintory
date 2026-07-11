"""add_budget_error_code

Revision ID: 9d9d5e869311
Revises: dddafacb2e7a
Create Date: 2026-07-11 20:27:01.751929

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d9d5e869311"
down_revision: str | Sequence[str] | None = "dddafacb2e7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("opportunity_enrichments", schema=None) as batch_op:
        # Drop old constraint and create new one with LLM_INPUT_BUDGET_EXCEEDED
        batch_op.drop_constraint(
            "chk_opportunity_enrichments_error_code", type_="check"
        )
        batch_op.create_check_constraint(
            "chk_opportunity_enrichments_error_code",
            condition="error_code IN ('LLM_MODEL_DOWNLOAD_FAILED', 'LLM_MODEL_CHECKSUM_FAILED', 'LLM_RUNTIME_START_FAILED', 'LLM_TIMEOUT', 'LLM_INVALID_JSON', 'LLM_SCHEMA_VALIDATION_FAILED', 'LLM_INFERENCE_FAILED', 'LLM_INPUT_BUDGET_EXCEEDED') OR error_code IS NULL",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("opportunity_enrichments", schema=None) as batch_op:
        batch_op.drop_constraint(
            "chk_opportunity_enrichments_error_code", type_="check"
        )
        batch_op.create_check_constraint(
            "chk_opportunity_enrichments_error_code",
            condition="error_code IN ('LLM_MODEL_DOWNLOAD_FAILED', 'LLM_MODEL_CHECKSUM_FAILED', 'LLM_RUNTIME_START_FAILED', 'LLM_TIMEOUT', 'LLM_INVALID_JSON', 'LLM_SCHEMA_VALIDATION_FAILED', 'LLM_INFERENCE_FAILED') OR error_code IS NULL",
        )
