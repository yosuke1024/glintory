import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from glintory.domain.clustering import OpportunityClusteringConfig
from glintory.domain.enums import EvidenceRelationType, OpportunityStatus
from glintory.domain.models import (
    AnalysisRun,
    Opportunity,
    OpportunityPublicAlias,
    OpportunitySignal,
    ScoringRun,
    Signal,
)
from glintory.infrastructure.opportunity_scoring_repository import (
    OpportunityScoringRepository,
)
from glintory.services.opportunity_clustering import OpportunityClusteringEngine
from glintory.services.opportunity_scoring import OpportunityScoringEngine
from glintory.services.opportunity_scoring_service import OpportunityScoringService
from glintory.services.signal_classification import classify_signal_role


class OpportunityRebuildService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _calculate_metrics_and_gate(
        self, cluster_signals: list[dict[str, Any]]
    ) -> tuple[dict[str, int], bool, str]:
        from glintory.services.gate_v3 import calculate_metrics_and_gate_v3

        metrics, gate_status, passed_published, reason = calculate_metrics_and_gate_v3(
            cluster_signals
        )
        return metrics, passed_published, reason

    def rebuild_v2(
        self, from_version: str = "v1", to_version: str = "v2"
    ) -> dict[str, Any]:
        """Perform a non-destructive rebuild of opportunities for version to_version."""
        now = datetime.now(UTC)

        # Create AnalysisRun record
        analysis_run = AnalysisRun(
            started_at=now,
            status="running",
            submitted_signal_count=0,
            created_candidate_count=0,
            updated_candidate_count=0,
            gate_passed_count=0,
            gate_rejected_count=0,
        )
        self.session.add(analysis_run)
        self.session.flush()

        existing_opps = (
            self.session.query(Opportunity)
            .filter(Opportunity.current_scoring_version == to_version)
            .all()
        )

        opp_data_map = {}
        for opp in existing_opps:
            opp_sigs = (
                self.session.query(OpportunitySignal)
                .filter(OpportunitySignal.opportunity_id == opp.id)
                .all()
            )
            sig_ids = {osig.signal_id for osig in opp_sigs}

            oldest_sig_id = None
            if opp_sigs:
                sigs = (
                    self.session.query(Signal)
                    .filter(Signal.id.in_(list(sig_ids)))
                    .all()
                )
                if sigs:
                    sorted_sigs = sorted(sigs, key=lambda s: (s.collected_at, s.id))
                    oldest_sig_id = sorted_sigs[0].id

            rep_sig_id = None
            centroid_links = [
                osig
                for osig in opp_sigs
                if osig.relation_type
                in (EvidenceRelationType.SUPPORTING, EvidenceRelationType.RELATED)
            ]
            if centroid_links:
                c_sigs = (
                    self.session.query(Signal)
                    .filter(Signal.id.in_([link.signal_id for link in centroid_links]))
                    .all()
                )
                if c_sigs:
                    sorted_c_sigs = sorted(c_sigs, key=lambda s: (s.collected_at, s.id))
                    rep_sig_id = sorted_c_sigs[0].id

            opp_data_map[opp.id] = {
                "opportunity": opp,
                "sig_ids": sig_ids,
                "oldest_sig_id": oldest_sig_id,
                "rep_sig_id": rep_sig_id,
                "public_id": opp.public_id,
                "first_published_at": opp.first_published_at,
                "last_published_at": opp.last_published_at,
                "status": opp.status,
                "created_at": opp.created_at,
                "public_revision": opp.public_revision,
                "public_content_hash": opp.public_content_hash,
            }

        # Reclassify from_version signals
        from_opps = (
            self.session.query(Opportunity)
            .filter(Opportunity.current_scoring_version == from_version)
            .all()
        )
        from_opp_ids = [opp.id for opp in from_opps]
        from_opp_sigs = (
            self.session.query(OpportunitySignal)
            .filter(OpportunitySignal.opportunity_id.in_(from_opp_ids))
            .all()
            if from_opp_ids
            else []
        )
        from_sig_ids = list({osig.signal_id for osig in from_opp_sigs})

        signals_to_reclassify = (
            self.session.query(Signal).filter(Signal.id.in_(from_sig_ids)).all()
            if from_sig_ids
            else []
        )
        for sig in signals_to_reclassify:
            src = sig.source
            src_type = src.source_type if src else "unknown"
            sig.signal_role = classify_signal_role(
                src_type, sig.signal_type, sig.title, sig.excerpt
            )
        self.session.flush()

        # Clean existing links
        existing_opp_ids = [opp.id for opp in existing_opps]
        if existing_opp_ids:
            self.session.query(OpportunitySignal).filter(
                OpportunitySignal.opportunity_id.in_(existing_opp_ids)
            ).delete(synchronize_session=False)
            self.session.flush()
            self.session.expire_all()

        # Zero-based clustering
        all_signals = self.session.query(Signal).all()
        config = OpportunityClusteringConfig()
        engine = OpportunityClusteringEngine(config)
        clusters = engine.cluster_signals(all_signals)

        # Align clusters
        canonical_opps_to_update = {}
        merged_opps = {}

        for idx, cluster in enumerate(clusters):
            cluster_sig_ids = {s_info["signal"].id for s_info in cluster["signals"]}
            cluster_sigs = [s_info["signal"] for s_info in cluster["signals"]]
            sorted_cluster_sigs = sorted(
                cluster_sigs, key=lambda s: (s.collected_at, s.id)
            )
            cluster_oldest_sig_id = (
                sorted_cluster_sigs[0].id if sorted_cluster_sigs else None
            )

            matched_candidates = []
            for _, data in opp_data_map.items():
                is_match = False
                if (
                    data["rep_sig_id"]
                    and data["rep_sig_id"] in cluster_sig_ids
                    or cluster_sig_ids & data["sig_ids"]
                    or data["oldest_sig_id"] == cluster_oldest_sig_id
                ):
                    is_match = True

                if is_match:
                    matched_candidates.append(data)

            if matched_candidates:
                sorted_candidates = sorted(
                    matched_candidates, key=lambda x: x["created_at"]
                )
                canonical = sorted_candidates[0]
                canonical_id = canonical["opportunity"].id
                canonical_opps_to_update[idx] = canonical_id

                for merged in sorted_candidates[1:]:
                    merged_opps.setdefault(canonical_id, []).append(merged)

        # Apply updates and creations
        saved_opp_ids = set()
        created_count = 0
        updated_count = 0
        gate_passed_count = 0
        gate_rejected_count = 0

        for idx, cluster in enumerate(clusters):
            canonical_id = canonical_opps_to_update.get(idx)

            rep_signal = cluster["representative_signal"]
            title = rep_signal.title or "Unnamed Opportunity"
            if len(title) > 200:
                title = title[:197] + "..."

            from glintory.services.gate_v3 import calculate_metrics_and_gate_v3

            metrics, gate_status_str, passed_published, reason = (
                calculate_metrics_and_gate_v3(cluster["signals"])
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

            if canonical_id:
                opp = self.session.get(Opportunity, canonical_id)
                if opp:
                    opp.title = title
                    opp.evidence_updated_at = now
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
                    opp.status = final_status
                    opp.current_scoring_version = to_version

                    saved_opp_ids.add(opp.id)
                    updated_count += 1

                    for merged in merged_opps.get(canonical_id, []):
                        m_opp = merged["opportunity"]
                        alias = OpportunityPublicAlias(
                            old_public_id=merged["public_id"],
                            canonical_public_id=opp.public_id,
                            created_at=now,
                        )
                        self.session.add(alias)
                        if m_opp.id not in saved_opp_ids:
                            m_opp.public_lifecycle = "merged"
            else:
                new_id = str(uuid.uuid4())
                public_id = f"opp_{uuid.uuid4().hex}"
                opp = Opportunity(
                    id=new_id,
                    public_id=public_id,
                    public_revision=1,
                    title=title,
                    generation_method="deterministic_cluster",
                    cluster_version=config.cluster_version,
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
                    current_scoring_version=to_version,
                )
                self.session.add(opp)
                self.session.flush()
                canonical_id = opp.id
                saved_opp_ids.add(canonical_id)
                created_count += 1

            for sig_info in cluster["signals"]:
                sig = sig_info["signal"]
                opp_sig = OpportunitySignal(
                    opportunity_id=canonical_id,
                    signal_id=sig.id,
                    relation_type=sig_info["relation_type"],
                    relevance_score=sig_info["relevance_score"],
                    association_source="clustering",
                    is_excluded=False,
                    updated_at=now,
                )
                self.session.add(opp_sig)

        # Mark unused opportunities as retired (instead of physical deletion)
        for opp_id, data in opp_data_map.items():
            if opp_id not in saved_opp_ids:
                opp_to_ret = data["opportunity"]
                opp_to_ret.public_lifecycle = "retired"
                saved_opp_ids.add(opp_to_ret.id)

        self.session.flush()

        # Update AnalysisRun metrics
        analysis_run.submitted_signal_count = len(all_signals)
        analysis_run.created_candidate_count = created_count
        analysis_run.updated_candidate_count = updated_count
        analysis_run.gate_passed_count = gate_passed_count
        analysis_run.gate_rejected_count = gate_rejected_count
        analysis_run.completed_at = datetime.now(UTC)
        analysis_run.status = "succeeded"
        self.session.flush()

        # Create ScoringRun record
        scoring_run = ScoringRun(
            started_at=datetime.now(UTC),
            status="running",
            analyzed_count=0,
            scored_count=0,
            unchanged_count=0,
        )
        self.session.add(scoring_run)
        self.session.flush()

        # Run scoring
        scoring_engine = OpportunityScoringEngine(scoring_version=to_version)
        scoring_service = OpportunityScoringService(
            session_factory=lambda: self.session,
            repository_factory=OpportunityScoringRepository,
            engine=scoring_engine,
            scoring_version=to_version,
        )
        scoring_res = scoring_service.score_opportunities(dry_run=False)
        self.session.flush()

        scoring_run.analyzed_count = scoring_res.analyzed_opportunity_count
        scoring_run.scored_count = scoring_res.scored_opportunity_count
        scoring_run.unchanged_count = scoring_res.unchanged_opportunity_count
        scoring_run.completed_at = datetime.now(UTC)
        scoring_run.status = "succeeded"
        self.session.flush()

        # Revision & Hash verification is handled solely during publish.

        self.session.commit()

        return {
            "rebuild_status": "success",
            "source_opportunities": len(from_opps),
            "source_signals": len(all_signals),
            "created_v2_opportunities": created_count,
            "updated_v2_opportunities": updated_count,
            "gate_passed": gate_passed_count,
            "gate_rejected": gate_rejected_count,
            "scored_opportunities": scoring_res.scored_opportunity_count,
            "submitted_signal_count": len(all_signals),
            "created_candidate_count": created_count,
            "updated_candidate_count": updated_count,
        }
