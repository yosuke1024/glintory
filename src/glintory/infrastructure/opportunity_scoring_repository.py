from datetime import date, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glintory.domain.clustering import calculate_evidence_origin
from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.models import Opportunity, OpportunitySignal, ScoreSnapshot, Signal, Source
from glintory.domain.scoring import (
    OpportunityScoringInput,
    ScoringEvidenceSignal,
)


class OpportunityScoringRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _to_scoring_input(
        self, opp: Opportunity, signals_with_sources: list[tuple[Signal, Source, OpportunitySignal]]
    ) -> OpportunityScoringInput:
        scoring_signals = []
        for sig, src, opp_sig in signals_with_sources:
            origin = calculate_evidence_origin(src.source_type, sig.canonical_url)
            
            # Extract tags as tuple
            tags_tuple = tuple(sig.tags) if sig.tags else ()

            scoring_signals.append(
                ScoringEvidenceSignal(
                    signal_id=sig.id,
                    source_id=src.id,
                    source_type=src.source_type,
                    signal_type=sig.signal_type,
                    relation_type=opp_sig.relation_type,
                    relevance_score=opp_sig.relevance_score,
                    evidence_origin=origin,
                    published_at=sig.published_at,
                    collected_at=sig.collected_at,
                    title=sig.title,
                    excerpt=sig.excerpt or "",
                    tags=tags_tuple,
                    raw_metadata=sig.raw_metadata or {},
                )
            )

        # opp.status can be string or Enum, standardise to string
        status_str = opp.status.value if hasattr(opp.status, "value") else str(opp.status)

        return OpportunityScoringInput(
            opportunity_id=opp.id,
            generation_method=opp.generation_method or "manual",
            status=status_str,
            signals=tuple(scoring_signals),
        )

    def load_scoring_inputs(
        self, active_only: bool = True, max_opportunities: int = 1000
    ) -> list[OpportunityScoringInput]:
        """Load all qualifying opportunities and their associated signals."""
        query = self.session.query(Opportunity)
        if active_only:
            query = query.filter(
                Opportunity.status.notin_(
                    [OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED]
                )
            )

        opps = query.order_by(Opportunity.id.asc()).limit(max_opportunities).all()
        if not opps:
            return []

        opp_ids = [opp.id for opp in opps]

        # Bulk load signals and sources to avoid N+1
        links = (
            self.session.query(OpportunitySignal, Signal, Source)
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .filter(OpportunitySignal.opportunity_id.in_(opp_ids))
            .all()
        )

        # Group links by opportunity ID
        opp_to_links = {opp_id: [] for opp_id in opp_ids}
        for opp_sig, sig, src in links:
            opp_to_links[opp_sig.opportunity_id].append((sig, src, opp_sig))

        inputs = []
        for opp in opps:
            linked = opp_to_links[opp.id]
            # Skip if opportunity has zero evidence signals
            if not linked:
                continue
            inputs.append(self._to_scoring_input(opp, linked))

        return inputs

    def load_scoring_input_by_id(self, opportunity_id: str) -> OpportunityScoringInput | None:
        """Load a single opportunity scoring input by ID."""
        opp = self.session.get(Opportunity, opportunity_id)
        if not opp:
            return None

        links = (
            self.session.query(OpportunitySignal, Signal, Source)
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .filter(OpportunitySignal.opportunity_id == opportunity_id)
            .all()
        )

        # Return None if no signals associated (skipped)
        if not links:
            return None

        signals_data = [(sig, src, opp_sig) for opp_sig, sig, src in links]
        return self._to_scoring_input(opp, signals_data)

    def load_latest_snapshot(
        self, opportunity_id: str, scoring_version: str
    ) -> ScoreSnapshot | None:
        """Load the latest score snapshot for an opportunity."""
        return (
            self.session.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.opportunity_id == opportunity_id,
                ScoreSnapshot.scoring_version == scoring_version,
            )
            .order_by(ScoreSnapshot.created_at.desc())
            .first()
        )

    def persist_score(
        self,
        opportunity_id: str,
        evidence_score: int,
        feasibility_score: int,
        penalty_score: int,
        total_score: int,
        confidence: Confidence,
        scoring_version: str,
        input_hash: str,
        as_of_date: date,
        explanation: dict[str, Any],
        last_scored_at: datetime,
    ) -> None:
        """Persist computed score snapshot and update Opportunity score fields."""
        # 1. Double check unique snapshot to avoid duplicate snapshots (IntegrityError)
        existing = (
            self.session.query(ScoreSnapshot)
            .filter(
                ScoreSnapshot.opportunity_id == opportunity_id,
                ScoreSnapshot.scoring_version == scoring_version,
                ScoreSnapshot.input_hash == input_hash,
            )
            .first()
        )
        if existing:
            # Already exists (unchanged input), do not insert a duplicate
            return

        # 2. Insert ScoreSnapshot
        snapshot = ScoreSnapshot(
            opportunity_id=opportunity_id,
            evidence_score=evidence_score,
            feasibility_score=feasibility_score,
            penalty_score=penalty_score,
            total_score=total_score,
            confidence=confidence,
            scoring_version=scoring_version,
            input_hash=input_hash,
            as_of_date=as_of_date,
            explanation=explanation,
            created_at=last_scored_at,
        )
        self.session.add(snapshot)

        # 3. Update Opportunity
        opp = self.session.get(Opportunity, opportunity_id)
        if opp:
            opp.evidence_score = evidence_score
            opp.feasibility_score = feasibility_score
            opp.penalty_score = penalty_score
            opp.total_score = total_score
            opp.confidence = confidence
            opp.current_scoring_version = scoring_version
            opp.last_scored_at = last_scored_at
            opp.updated_at = last_scored_at
        
        try:
            self.session.flush()
        except IntegrityError as e:
            self.session.rollback()
            # Do not expose raw database URL or raw SQL parameters for security
            raise ValueError("Database integrity error occurred during persistence.") from None
