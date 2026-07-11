from sqlalchemy.orm import Session

from glintory.domain.enums import OpportunityStatus
from glintory.domain.models import Opportunity, OpportunitySignal, Signal


class OpportunityClusteringRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load_unassociated_signals(self) -> list[Signal]:
        """Load all signals that are not linked to any active opportunity."""
        subq = self.session.query(OpportunitySignal.signal_id).filter(
            OpportunitySignal.is_excluded.is_(False)
        )
        return self.session.query(Signal).filter(Signal.id.notin_(subq)).all()

    def load_active_opportunities_with_signals(self) -> list[dict]:
        """Load all active opportunities along with their active associated signals.

        Returns a list of dicts:
        {
            "opportunity": Opportunity,
            "signals": list[tuple[Signal, float, EvidenceRelationType]]  # (Signal, relevance_score, relation_type)
        }
        """
        opps = (
            self.session.query(Opportunity)
            .filter(
                Opportunity.status.notin_(
                    [OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED]
                )
            )
            .all()
        )
        if not opps:
            return []

        opp_ids = [opp.id for opp in opps]

        links = (
            self.session.query(OpportunitySignal, Signal)
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .filter(OpportunitySignal.opportunity_id.in_(opp_ids))
            .filter(OpportunitySignal.is_excluded.is_(False))
            .all()
        )

        opp_to_signals = {opp.id: [] for opp in opps}
        for opp_sig, sig in links:
            opp_to_signals[opp_sig.opportunity_id].append(
                (sig, opp_sig.relevance_score, opp_sig.relation_type)
            )

        return [{"opportunity": opp, "signals": opp_to_signals[opp.id]} for opp in opps]

    def save_opportunity(self, opportunity: Opportunity) -> None:
        self.session.add(opportunity)

    def save_opportunity_signal(self, opp_sig: OpportunitySignal) -> None:
        self.session.add(opp_sig)
