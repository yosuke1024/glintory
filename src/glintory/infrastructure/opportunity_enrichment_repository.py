from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from glintory.domain.models import OpportunityEnrichment


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
        generated_title: str | None = None,
        generated_summary: str | None = None,
        problem_statement: str | None = None,
        target_users: list[str] | None = None,
        why_now: str | None = None,
        evidence_synthesis: str | None = None,
        build_direction: str | None = None,
        risks: list[str] | None = None,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        llm_confidence: str | None = None,
    ) -> None:
        """Update the enrichment execution results."""
        enrichment = self.session.get(OpportunityEnrichment, enrichment_id)
        if enrichment:
            enrichment.status = status
            enrichment.completed_at = completed_at
            enrichment.duration_ms = duration_ms
            enrichment.error_code = error_code
            enrichment.generated_title = generated_title
            enrichment.generated_summary = generated_summary
            enrichment.problem_statement = problem_statement
            enrichment.target_users = target_users or []
            enrichment.why_now = why_now
            enrichment.evidence_synthesis = evidence_synthesis
            enrichment.build_direction = build_direction
            enrichment.risks = risks or []
            enrichment.tags = tags or []
            enrichment.evidence_refs = evidence_refs or []
            enrichment.llm_confidence = llm_confidence
            self.session.flush()
