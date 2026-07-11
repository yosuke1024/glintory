import logging
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from glintory.domain.models import OpportunityEnrichment, OpportunityEnrichmentLocalization

logger = logging.getLogger(__name__)


class OpportunityEnrichmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_latest_successful_enrichment(self, opportunity_id: str) -> OpportunityEnrichment | None:
        """Fetch the latest succeeded enrichment for an opportunity."""
        return (
            self.session.query(OpportunityEnrichment)
            .filter(
                OpportunityEnrichment.opportunity_id == opportunity_id,
                OpportunityEnrichment.status == "succeeded",
            )
            .order_by(OpportunityEnrichment.completed_at.desc())
            .first()
        )

    def get_enrichment_by_input_hash(
        self, opportunity_id: str, input_hash: str
    ) -> OpportunityEnrichment | None:
        """Fetch enrichment by opportunity_id and input_hash."""
        return (
            self.session.query(OpportunityEnrichment)
            .filter(
                OpportunityEnrichment.opportunity_id == opportunity_id,
                OpportunityEnrichment.input_hash == input_hash,
            )
            .first()
        )

    def create_enrichment(
        self,
        opportunity_id: str,
        status: str,
        model_provider: str,
        model_id: str,
        model_revision: str,
        model_sha256: str,
        runtime: str,
        runtime_version: str,
        prompt_version: str,
        input_hash: str,
        started_at: datetime,
    ) -> OpportunityEnrichment:
        """Create a new opportunity enrichment record in running state."""
        enrichment = OpportunityEnrichment(
            opportunity_id=opportunity_id,
            status=status,
            model_provider=model_provider,
            model_id=model_id,
            model_revision=model_revision,
            model_sha256=model_sha256,
            runtime=runtime,
            runtime_version=runtime_version,
            prompt_version=prompt_version,
            input_hash=input_hash,
            started_at=started_at,
        )
        self.session.add(enrichment)
        self.session.flush()
        return enrichment

    def update_enrichment_result(
        self,
        enrichment_id: str,
        status: str,
        completed_at: datetime,
        duration_ms: int,
        error_code: str | None = None,
        english: Any | None = None,
        japanese: Any | None = None,
        evidence_refs: list[str] | None = None,
        llm_confidence: str | None = None,
    ) -> None:
        """Update the enrichment execution results."""
        enrichment = self.session.get(OpportunityEnrichment, enrichment_id)
        if not enrichment:
            return

        enrichment.status = status
        enrichment.completed_at = completed_at
        enrichment.duration_ms = duration_ms
        enrichment.error_code = error_code
        enrichment.evidence_refs = evidence_refs or []
        enrichment.llm_confidence = llm_confidence

        if english:
            enrichment.generated_title = english.title
            enrichment.generated_summary = english.summary
            enrichment.problem_statement = english.problem_statement
            enrichment.target_users = english.target_users
            enrichment.why_now = english.why_now
            enrichment.evidence_synthesis = english.evidence_synthesis
            enrichment.build_direction = english.build_direction
            enrichment.risks = english.risks
            enrichment.tags = english.tags

        # Remove old localizations to prevent uniqueness constraints violations
        self.session.query(OpportunityEnrichmentLocalization).filter(
            OpportunityEnrichmentLocalization.enrichment_id == enrichment_id
        ).delete()
        self.session.flush()

        if english:
            en_loc = OpportunityEnrichmentLocalization(
                enrichment_id=enrichment_id,
                locale="en",
                generated_title=english.title,
                generated_summary=english.summary,
                problem_statement=english.problem_statement,
                target_users=english.target_users,
                why_now=english.why_now,
                evidence_synthesis=english.evidence_synthesis,
                build_direction=english.build_direction,
                risks=english.risks,
                tags=english.tags,
            )
            self.session.add(en_loc)

        if japanese:
            ja_loc = OpportunityEnrichmentLocalization(
                enrichment_id=enrichment_id,
                locale="ja",
                generated_title=japanese.title,
                generated_summary=japanese.summary,
                problem_statement=japanese.problem_statement,
                target_users=japanese.target_users,
                why_now=japanese.why_now,
                evidence_synthesis=japanese.evidence_synthesis,
                build_direction=japanese.build_direction,
                risks=japanese.risks,
                tags=japanese.tags,
            )
            self.session.add(ja_loc)

        self.session.flush()
