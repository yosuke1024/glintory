import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from glintory.domain.clustering import OpportunityClusteringConfig
from glintory.domain.enums import EvidenceRelationType, OpportunityStatus, SignalRole
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
from glintory.services.content_hashing import calculate_opportunity_content_hash
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
        signals = [item["signal"] for item in cluster_signals]
        if not signals:
            return (
                {
                    "independent_evidence_count": 0,
                    "demand_evidence_count": 0,
                    "source_type_count": 0,
                    "source_domain_count": 0,
                },
                False,
                "No signals in cluster.",
            )

        def get_thread_key(sig: Any) -> str:
            url = sig.canonical_url or ""
            hn_match = re.search(r"news\.ycombinator\.com/item\?id=(\d+)", url)
            if hn_match:
                return f"hn_thread_{hn_match.group(1)}"
            gh_issue_match = re.search(
                r"github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", url
            )
            if gh_issue_match:
                return (
                    f"github_issue_{gh_issue_match.group(1)}_{gh_issue_match.group(3)}"
                )
            gh_repo_match = re.search(r"github\.com/([^/]+/[^/]+)", url)
            if gh_repo_match:
                return f"github_repo_{gh_repo_match.group(1)}"
            return url

        threads: dict[str, list[Any]] = {}
        for sig in signals:
            key = get_thread_key(sig)
            threads.setdefault(key, []).append(sig)

        independent_count = len(threads)
        demand_count = sum(1 for sig in signals if sig.signal_role == SignalRole.DEMAND)

        source_types = {
            sig.source.source_type
            for sig in signals
            if sig.source and sig.source.source_type
        }
        source_type_count = len(source_types)

        domains = set()
        for sig in signals:
            if sig.canonical_url:
                parsed = urlparse(sig.canonical_url)
                if parsed.netloc:
                    domains.add(parsed.netloc)
        source_domain_count = len(domains)

        metrics = {
            "independent_evidence_count": independent_count,
            "demand_evidence_count": demand_count,
            "source_type_count": source_type_count,
            "source_domain_count": source_domain_count,
        }

        if independent_count == 1:
            single_thread_sigs = list(threads.values())[0]
            is_show_hn = any(
                (
                    sig.source
                    and sig.source.source_type == "hackernews"
                    and (sig.title or "").lower().startswith("show hn:")
                )
                for sig in single_thread_sigs
            )
            if is_show_hn:
                return (
                    metrics,
                    False,
                    "Rejected: Single Show HN submission cannot be promoted.",
                )

        combined_text = "\n".join(
            f"{sig.title or ''}\n{sig.excerpt or ''}" for sig in signals
        ).lower()

        def has_word(pattern: str, text: str) -> bool:
            if pattern.replace(" ", "").isalnum() and pattern.isascii():
                escaped = re.escape(pattern)
                return bool(re.search(rf"\b{escaped}\b", text))
            return pattern in text

        user_kws = [
            "customer",
            "target user",
            "developer",
            "target audience",
            "for developers",
            "for users",
            "ユーザー",
            "顧客",
            "開発者",
            "ターゲットユーザー",
        ]
        problem_kws = [
            "problem",
            "pain",
            "issue",
            "difficult",
            "annoy",
            "error",
            "fail",
            "broken",
            "limit",
            "課題",
            "問題",
            "困っ",
            "痛手",
            "バグ",
            "エラー",
        ]
        workaround_kws = [
            "workaround",
            "instead of",
            "alternative",
            "current tool",
            "manually",
            "excel",
            "scripts",
            "回避",
            "代替",
            "手動",
            "スプレッドシート",
        ]
        gap_kws = [
            "why",
            "limit",
            "lack",
            "cannot",
            "expensive",
            "slow",
            "不足",
            "できない",
            "高価",
            "遅い",
        ]
        mvp_kws = [
            "mvp",
            "solution",
            "feature",
            "idea",
            "should",
            "wish",
            "提案",
            "欲しい",
            "必要",
            "機能",
        ]

        has_user = any(has_word(kw, combined_text) for kw in user_kws)
        has_problem = any(has_word(kw, combined_text) for kw in problem_kws)
        has_workaround = any(has_word(kw, combined_text) for kw in workaround_kws)
        has_gap = any(has_word(kw, combined_text) for kw in gap_kws)
        has_mvp = any(has_word(kw, combined_text) for kw in mvp_kws)

        is_solo_realistic = not any(
            has_word(kw, combined_text)
            for kw in [
                "enterprise-grade",
                "multi-tenant",
                "collaboration",
                "rbac",
                "salesforce integration",
                "large scale",
                "組織向け",
                "共同編集",
                "権限管理",
            ]
        )
        is_heavy_backend_free = not any(
            has_word(kw, combined_text)
            for kw in [
                "heavy backend",
                "complex backend",
                "microservices",
                "kubernetes",
                "k8s",
                "large scale database",
                "heavy server",
                "重いバックエンド",
                "マイクロサービス",
            ]
        )
        is_ai_cost_free = not any(
            has_word(kw, combined_text)
            for kw in [
                "heavy api cost",
                "expensive api",
                "expensive ai",
                "high hosting cost",
                "high running cost",
                "高額なapi",
                "ai費用",
                "高額なホスティング",
            ]
        )
        is_enterprise_sales_free = not any(
            has_word(kw, combined_text)
            for kw in [
                "enterprise sales",
                "sales cycle",
                "sales team",
                "b2b sales",
                "エンタープライズ営業",
                "営業チーム",
                "営業プロセス",
            ]
        )

        quality_passed = (
            has_user
            and has_problem
            and has_workaround
            and has_gap
            and has_mvp
            and is_solo_realistic
            and is_heavy_backend_free
            and is_ai_cost_free
            and is_enterprise_sales_free
        )

        if not quality_passed:
            details = (
                f"User:{has_user}, Problem:{has_problem}, Workaround:{has_workaround}, "
                f"Gap:{has_gap}, MVP:{has_mvp}, Solo:{is_solo_realistic}, "
                f"Backend:{is_heavy_backend_free}, AICost:{is_ai_cost_free}, Sales:{is_enterprise_sales_free}"
            )
            return (
                metrics,
                False,
                f"Rejected: Quality gate failed. Missing structural elements or violating constraints. ({details})",
            )

        has_demand = demand_count > 0

        if independent_count >= 2:
            if has_demand:
                return (
                    metrics,
                    True,
                    "Passed Condition A: Multiple independent evidences with demand.",
                )
            return (
                metrics,
                False,
                "Rejected Condition A: Multiple independent evidences but no demand.",
            )

        single_sig = signals[0]
        if single_sig.signal_role == SignalRole.DEMAND:
            return (
                metrics,
                True,
                "Passed Condition B: Strong detailed single demand with all 5 structure elements.",
            )

        return (metrics, False, "Rejected Condition B: Single evidence is not demand.")

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

            metrics, passed, reason = self._calculate_metrics_and_gate(
                cluster["signals"]
            )
            if passed:
                gate_passed_count += 1
            else:
                gate_rejected_count += 1

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
                    opp.gate_version = "v2"
                    opp.gate_status = "passed" if passed else "rejected"
                    opp.gate_reason = reason
                    opp.gate_checked_at = now
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
                    status=OpportunityStatus.INBOX
                    if passed
                    else OpportunityStatus.RESEARCH,
                    evidence_updated_at=now,
                    independent_evidence_count=metrics["independent_evidence_count"],
                    demand_evidence_count=metrics["demand_evidence_count"],
                    source_type_count=metrics["source_type_count"],
                    source_domain_count=metrics["source_domain_count"],
                    gate_version="v2",
                    gate_status="passed" if passed else "rejected",
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

        # Revision & Hash verification
        v2_opps = (
            self.session.query(Opportunity)
            .filter(Opportunity.current_scoring_version == to_version)
            .all()
        )

        for opp in v2_opps:
            links = (
                self.session.query(OpportunitySignal, Signal)
                .join(Signal, OpportunitySignal.signal_id == Signal.id)
                .filter(
                    OpportunitySignal.opportunity_id == opp.id,
                    OpportunitySignal.is_excluded.is_(False),
                )
                .all()
            )

            ev_list = []
            for opp_sig, sig in links:
                ev_list.append(
                    {
                        "signal_id": sig.id,
                        "role": sig.signal_role.value
                        if hasattr(sig.signal_role, "value")
                        else str(sig.signal_role),
                        "title": sig.title,
                        "url": sig.canonical_url,
                        "published_at": sig.published_at,
                        "relevance_score": opp_sig.relevance_score,
                        "summary_ja": opp_sig.evidence_summary_ja,
                        "summary_en": opp_sig.evidence_summary_en,
                        "excerpt": sig.excerpt,
                    }
                )

            new_hash = calculate_opportunity_content_hash(opp, ev_list)

            if opp.public_content_hash is None:
                opp.public_content_hash = new_hash
                opp.public_revision = 1
                opp.first_published_at = now
                opp.last_published_at = now
            elif opp.public_content_hash != new_hash:
                opp.public_content_hash = new_hash
                opp.public_revision += 1
                opp.last_published_at = now

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
