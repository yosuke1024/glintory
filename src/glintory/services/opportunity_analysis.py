from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from glintory.domain.clustering import OpportunityClusteringConfig
from glintory.domain.enums import EvidenceRelationType, OpportunityStatus, SignalRole
from glintory.domain.models import AnalysisRun, Opportunity, OpportunitySignal, Signal
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

    def _calculate_metrics_and_gate(
        self, cluster_signals: list[dict]
    ) -> tuple[dict, bool, str]:
        from glintory.services.gate_v3 import calculate_metrics_and_gate_v3
        metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(cluster_signals)
        return metrics, passed_published, reason

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

        now = datetime.now(UTC)

        # Create AnalysisRun record
        analysis_run = AnalysisRun(
            started_at=now,
            status="running",
            submitted_signal_count=len(unassociated_signals),
            created_candidate_count=0,
            updated_candidate_count=0,
            gate_passed_count=0,
            gate_rejected_count=0,
        )
        if not dry_run:
            self.session.add(analysis_run)
            self.session.flush()

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

            # Centroid Requirement: Filter where is_excluded = false (done in repo) and relation_type is supporting or related
            centroid_sigs = [
                (sig, rel_score)
                for sig, rel_score, rel_type in signals_with_score
                if rel_type
                in (
                    EvidenceRelationType.SUPPORTING,
                    EvidenceRelationType.RELATED,
                )
            ]
            if not centroid_sigs:
                continue

            # Sort to find the representative signal (oldest collected_at)
            sorted_sigs = sorted(
                centroid_sigs,
                key=lambda x: (x[0].collected_at, x[0].id),
            )
            rep_sig = sorted_sigs[0][0]
            rep_sig._is_existing_rep = True
            existing_rep_signals.append(rep_sig)
            signal_id_to_opp_id[rep_sig.id] = opp.id

        # 3. Combine representative signals from existing opportunities and unassociated signals
        all_signals = existing_rep_signals + unassociated_signals

        # 4. Perform clustering
        clusters = self.engine.cluster_signals(all_signals)

        # 5. Process clusters inside transaction
        created_opps_count = 0
        linked_sigs_count = 0
        gate_passed_count = 0
        gate_rejected_count = 0

        # Load excluded pairs to prevent automatically re-linking to previously excluded opportunities
        excluded_rows = (
            self.session.query(OpportunitySignal)
            .filter(OpportunitySignal.is_excluded)
            .all()
        )
        excluded_pairs = {}
        for row in excluded_rows:
            excluded_pairs.setdefault(row.signal_id, set()).add(row.opportunity_id)

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
                any_linked = False
                for sig_info in cluster["signals"]:
                    sig = sig_info["signal"]
                    if sig in unassociated_signals:
                        # Excluded Pair Check: Do not automatically re-link to excluded opportunity
                        if matched_opp_id in excluded_pairs.get(sig.id, set()):
                            continue

                        opp_sig = OpportunitySignal(
                            opportunity_id=matched_opp_id,
                            signal_id=sig.id,
                            relation_type=sig_info["relation_type"],
                            relevance_score=sig_info["relevance_score"],
                            association_source="clustering",
                            is_excluded=False,
                            updated_at=now,
                        )
                        if not dry_run:
                            self.repository.save_opportunity_signal(opp_sig)
                        linked_sigs_count += 1
                        any_linked = True

                # Only update evidence_updated_at if a new link was successfully created
                if any_linked and not dry_run:
                    opp = self.session.get(Opportunity, matched_opp_id)
                    if opp:
                        # Fetch all active evidence signals for this opportunity from DB
                        all_links = (
                            self.session.query(OpportunitySignal, Signal)
                            .join(Signal, OpportunitySignal.signal_id == Signal.id)
                            .filter(OpportunitySignal.opportunity_id == opp.id)
                            .filter(OpportunitySignal.is_excluded.is_(False))
                            .all()
                        )
                        ev_signals_input = [
                            {
                                "signal": sig,
                                "relation_type": opp_sig.relation_type,
                                "relevance_score": opp_sig.relevance_score,
                            }
                            for opp_sig, sig in all_links
                        ]

                        from glintory.services.gate_v3 import calculate_metrics_and_gate_v3
                        metrics, gate_status_str, passed_published, reason = calculate_metrics_and_gate_v3(
                            ev_signals_input
                        )
                        opp.independent_evidence_count = metrics[
                            "independent_evidence_count"
                        ]
                        opp.demand_evidence_count = metrics["demand_evidence_count"]
                        opp.source_type_count = metrics["source_type_count"]
                        opp.source_domain_count = metrics["source_domain_count"]

                        opp.gate_version = "v3"
                        opp.gate_status = gate_status_str
                        opp.gate_reason = reason
                        opp.gate_checked_at = now

                        if gate_status_str == "passed":
                            gate_passed_count += 1
                            opp.status = OpportunityStatus.INBOX
                        else:
                            gate_rejected_count += 1
                            if reason.startswith("Research Candidate:"):
                                opp.status = OpportunityStatus.RESEARCH
                            else:
                                opp.status = OpportunityStatus.REJECTED
                        opp.evidence_updated_at = now
            else:
                # Create a new opportunity
                rep_signal = cluster["representative_signal"]
                title = rep_signal.title or "Unnamed Opportunity"
                if len(title) > 200:
                    title = title[:197] + "..."

                from glintory.services.gate_v3 import calculate_metrics_and_gate_v3
                metrics, gate_status_str, passed_published, reason = calculate_metrics_and_gate_v3(
                    cluster["signals"]
                )
                if gate_status_str == "passed":
                    gate_passed_count += 1
                    final_status = OpportunityStatus.INBOX
                else:
                    gate_rejected_count += 1
                    if reason.startswith("Research Candidate:"):
                        final_status = OpportunityStatus.RESEARCH
                    else:
                        final_status = OpportunityStatus.REJECTED

                opp = Opportunity(
                    title=title,
                    generation_method="deterministic_cluster",
                    cluster_version=self.config.cluster_version,
                    last_clustered_at=now,
                    status=final_status,
                    evidence_updated_at=now,
                    independent_evidence_count=metrics["independent_evidence_count"],
                    demand_evidence_count=metrics["demand_evidence_count"],
                    source_type_count=metrics["source_type_count"],
                    source_domain_count=metrics["source_domain_count"],
                    gate_version="v3",
                    gate_status=gate_status_str,
                    gate_reason=reason,
                    gate_checked_at=now,
                    current_scoring_version="v2",
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
                        association_source="clustering",
                        is_excluded=False,
                        updated_at=now,
                    )
                    if not dry_run:
                        self.repository.save_opportunity_signal(opp_sig)
                    linked_sigs_count += 1

        if not dry_run:
            analysis_run.created_candidate_count = created_opps_count
            analysis_run.updated_candidate_count = linked_sigs_count
            analysis_run.gate_passed_count = gate_passed_count
            analysis_run.gate_rejected_count = gate_rejected_count
            analysis_run.completed_at = datetime.now(UTC)
            analysis_run.status = "succeeded"
            self.session.commit()
        else:
            self.session.rollback()

        return OpportunityAnalysisResult(
            analyzed_signals_count=len(unassociated_signals),
            created_opportunities_count=created_opps_count,
            linked_signals_count=linked_sigs_count,
            dry_run=dry_run,
        )
