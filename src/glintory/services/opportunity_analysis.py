from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from glintory.domain.clustering import OpportunityClusteringConfig
from glintory.domain.enums import OpportunityStatus
from glintory.domain.models import Opportunity, OpportunitySignal
from glintory.infrastructure.opportunity_clustering_repository import (
    OpportunityClusteringRepository,
)
from glintory.services.opportunity_clustering import OpportunityClusteringEngine


@dataclass(frozen=True, slots=True)
class OpportunityAnalysisResult:
    analyzed_signals_count: int
    created_opportunities_count: int
    linked_signals_count: int
    dry_run: bool


class OpportunityAnalysisService:
    def __init__(
        self,
        session: Session,
        repository: OpportunityClusteringRepository,
        engine: OpportunityClusteringEngine,
        config: OpportunityClusteringConfig | None = None,
    ) -> None:
        self.session = session
        self.repository = repository
        self.engine = engine
        self.config = config or OpportunityClusteringConfig()

    def analyze_and_cluster(
        self, *, dry_run: bool = False
    ) -> OpportunityAnalysisResult:
        """Run the clustering analysis over unassociated signals.

        Matches signals to existing opportunities or creates new ones.
        """
        # 1. Load unassociated signals
        unassociated_signals = self.repository.load_unassociated_signals()
        if not unassociated_signals:
            return OpportunityAnalysisResult(
                analyzed_signals_count=0,
                created_opportunities_count=0,
                linked_signals_count=0,
                dry_run=dry_run,
            )

        # 2. Load active opportunities and their associated signals
        active_opp_items = self.repository.load_active_opportunities_with_signals()

        # Map each active opportunity's representative signal ID to the opportunity ID
        signal_id_to_opp_id = {}
        existing_rep_signals = []

        for item in active_opp_items:
            opp = item["opportunity"]
            signals_with_score = item["signals"]
            if not signals_with_score:
                continue
            # Sort to find the representative signal (oldest collected_at)
            sorted_sigs = sorted(
                signals_with_score,
                key=lambda x: (x[0].collected_at, x[0].id)
            )
            rep_sig = sorted_sigs[0][0]
            existing_rep_signals.append(rep_sig)
            signal_id_to_opp_id[rep_sig.id] = opp.id

        # 3. Combine representative signals from existing opportunities and unassociated signals
        all_signals = existing_rep_signals + unassociated_signals

        # 4. Perform clustering
        clusters = self.engine.cluster_signals(all_signals)

        # 5. Process clusters inside transaction
        created_opps_count = 0
        linked_sigs_count = 0
        now = datetime.now(UTC)

        for cluster in clusters:
            # Check if this cluster contains any existing representative signals
            matched_opp_id = None
            for sig_info in cluster["signals"]:
                sig = sig_info["signal"]
                if sig.id in signal_id_to_opp_id:
                    matched_opp_id = signal_id_to_opp_id[sig.id]
                    break

            if matched_opp_id:
                # Link unassociated signals to this existing opportunity
                for sig_info in cluster["signals"]:
                    sig = sig_info["signal"]
                    if sig in unassociated_signals:
                        opp_sig = OpportunitySignal(
                            opportunity_id=matched_opp_id,
                            signal_id=sig.id,
                            relation_type=sig_info["relation_type"],
                            relevance_score=sig_info["relevance_score"],
                        )
                        if not dry_run:
                            self.repository.save_opportunity_signal(opp_sig)
                        linked_sigs_count += 1
            else:
                # Create a new opportunity
                rep_signal = cluster["representative_signal"]
                title = rep_signal.title or "Unnamed Opportunity"
                if len(title) > 200:
                    title = title[:197] + "..."

                opp = Opportunity(
                    title=title,
                    generation_method="deterministic_cluster",
                    cluster_version=self.config.cluster_version,
                    last_clustered_at=now,
                    status=OpportunityStatus.INBOX,
                )
                if not dry_run:
                    self.repository.save_opportunity(opp)
                    self.session.flush()  # Populates opp.id

                created_opps_count += 1

                for sig_info in cluster["signals"]:
                    sig = sig_info["signal"]
                    opp_sig = OpportunitySignal(
                        opportunity_id=opp.id if not dry_run else "dry-run-id",
                        signal_id=sig.id,
                        relation_type=sig_info["relation_type"],
                        relevance_score=sig_info["relevance_score"],
                    )
                    if not dry_run:
                        self.repository.save_opportunity_signal(opp_sig)
                    linked_sigs_count += 1

        if not dry_run:
            self.session.commit()
        else:
            self.session.rollback()

        return OpportunityAnalysisResult(
            analyzed_signals_count=len(unassociated_signals),
            created_opportunities_count=created_opps_count,
            linked_signals_count=linked_sigs_count,
            dry_run=dry_run,
        )
