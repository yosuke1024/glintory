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
        import re
        from urllib.parse import urlparse

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

        # Thread grouping
        def get_thread_key(sig) -> str:
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

        threads = {}
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

        # Show HN single check
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

        # Build combined lowercase text for keyword parsing
        combined_text = "\n".join(
            f"{sig.title or ''}\n{sig.excerpt or ''}" for sig in signals
        ).lower()

        # Word boundary matching helper for english keywords
        def has_word(pattern: str, text: str) -> bool:
            if pattern.replace(" ", "").isalnum() and pattern.isascii():
                escaped = re.escape(pattern)
                return bool(re.search(rf"\b{escaped}\b", text))
            return pattern in text

        # 1. Quality Gate Checks (Must satisfy all 9)
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

        # 2. Evidence Gate Checks (Must satisfy Condition A or Condition B)
        has_demand = demand_count > 0

        # Condition A: Multiple independent evidences with demand
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

        # Condition B: Single independent evidence, must be demand
        single_sig = signals[0]
        if single_sig.signal_role == SignalRole.DEMAND:
            return (
                metrics,
                True,
                "Passed Condition B: Strong detailed single demand with all 5 structure elements.",
            )

        return (
            metrics,
            False,
            "Rejected Condition B: Single evidence is not demand.",
        )

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

                        metrics, passed, reason = self._calculate_metrics_and_gate(
                            ev_signals_input
                        )
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

                        if passed:
                            gate_passed_count += 1
                        else:
                            gate_rejected_count += 1

                        if opp.status in (
                            OpportunityStatus.INBOX,
                            OpportunityStatus.RESEARCH,
                            OpportunityStatus.REJECTED,
                            OpportunityStatus.ARCHIVED,
                        ):
                            opp.status = (
                                OpportunityStatus.INBOX
                                if passed
                                else OpportunityStatus.RESEARCH
                            )
                        opp.evidence_updated_at = now
            else:
                # Create a new opportunity
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

                opp = Opportunity(
                    title=title,
                    generation_method="deterministic_cluster",
                    cluster_version=self.config.cluster_version,
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
