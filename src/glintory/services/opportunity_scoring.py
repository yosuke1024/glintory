import math
from collections import Counter
from datetime import date

from glintory.domain.enums import Confidence, EvidenceRelationType, SignalType
from glintory.domain.scoring import (
    OpportunityScore,
    OpportunityScoringInput,
    ScoreComponent,
    ScoringEvidenceSignal,
)
from glintory.services.scoring_hash import calculate_scoring_input_hash


def round_half_up(val: float) -> int:
    """Standard mathematical rounding (half up)."""
    if val >= 0:
        return math.floor(val + 0.5)
    return math.ceil(val - 0.5)


class OpportunityScoringEngine:
    def __init__(self, scoring_version: str = "v1") -> None:
        if scoring_version not in ("v1", "v2"):
            raise ValueError(f"Unsupported scoring version: {scoring_version}")
        self.scoring_version = scoring_version

    def score(
        self,
        scoring_input: OpportunityScoringInput,
        *,
        as_of_date: date,
    ) -> OpportunityScore:
        if self.scoring_version == "v2":
            return self._score_v2(scoring_input, as_of_date=as_of_date)

        """Score an opportunity based on its associated evidence signals."""
        signals = scoring_input.signals

        # Filter positive evidence signals (supporting and related)
        positive_signals = [
            s
            for s in signals
            if s.relation_type
            in (EvidenceRelationType.SUPPORTING, EvidenceRelationType.RELATED)
        ]

        # Calculate counts
        supporting_count = sum(
            1 for s in signals if s.relation_type == EvidenceRelationType.SUPPORTING
        )
        related_count = sum(
            1 for s in signals if s.relation_type == EvidenceRelationType.RELATED
        )
        contradicting_count = sum(
            1 for s in signals if s.relation_type == EvidenceRelationType.CONTRADICTING
        )

        # ----------------------------------------------------
        # 1. Evidence Score (0-50)
        # ----------------------------------------------------
        evidence_components = []

        # 1.1 Evidence Volume (0-12)
        effective_count = supporting_count + 0.5 * related_count
        if effective_count == 0:
            vol_score = 0
        elif effective_count < 2:
            vol_score = 3
        elif effective_count < 3:
            vol_score = 6
        elif effective_count < 5:
            vol_score = 8
        elif effective_count < 8:
            vol_score = 10
        else:
            vol_score = 12

        evidence_components.append(
            ScoreComponent(
                name="evidence_volume",
                score=vol_score,
                maximum=12,
                explanation="Volume of positive evidence (supporting + 0.5 * related)",
                facts={"effective_signal_count": effective_count},
            )
        )

        # 1.2 Evidence Origin Diversity (0-12)
        positive_origins = {s.evidence_origin for s in positive_signals}
        distinct_origin_count = len(positive_origins)
        if distinct_origin_count == 0:
            origin_score = 0
        elif distinct_origin_count == 1:
            origin_score = 2
        elif distinct_origin_count == 2:
            origin_score = 6
        elif distinct_origin_count == 3:
            origin_score = 9
        else:
            origin_score = 12

        evidence_components.append(
            ScoreComponent(
                name="evidence_origin_diversity",
                score=origin_score,
                maximum=12,
                explanation="Diversity of evidence origins (domain or repository)",
                facts={"distinct_origin_count": distinct_origin_count},
            )
        )

        # 1.3 Source Type Diversity (0-8)
        positive_sources = {s.source_type for s in positive_signals}
        distinct_source_type_count = len(positive_sources)
        if distinct_source_type_count == 0:
            source_score = 0
        elif distinct_source_type_count == 1:
            source_score = 2
        elif distinct_source_type_count == 2:
            source_score = 5
        else:
            source_score = 8

        evidence_components.append(
            ScoreComponent(
                name="source_type_diversity",
                score=source_score,
                maximum=8,
                explanation="Diversity of source types (e.g. github, hackernews, rss)",
                facts={"distinct_source_type_count": distinct_source_type_count},
            )
        )

        # 1.4 Evidence Coverage (0-6)
        # Groups:
        # Demand: pain, request, complaint, job_demand
        # Build: project, launch, hackathon_project, adoption
        # Market: trend, comparison, migration, funding
        demand_types = {
            SignalType.PAIN,
            SignalType.REQUEST,
            SignalType.COMPLAINT,
            SignalType.JOB_DEMAND,
        }
        build_types = {
            SignalType.PROJECT,
            SignalType.LAUNCH,
            SignalType.HACKATHON_PROJECT,
            SignalType.ADOPTION,
        }
        market_types = {
            SignalType.TREND,
            SignalType.COMPARISON,
            SignalType.MIGRATION,
            SignalType.FUNDING,
        }

        has_demand = any(s.signal_type in demand_types for s in positive_signals)
        has_build = any(s.signal_type in build_types for s in positive_signals)
        has_market = any(s.signal_type in market_types for s in positive_signals)

        coverage_score = 0
        if has_demand:
            coverage_score += 2
        if has_build:
            coverage_score += 2
        if has_market:
            coverage_score += 2

        evidence_components.append(
            ScoreComponent(
                name="evidence_coverage",
                score=coverage_score,
                maximum=6,
                explanation="Coverage of signal categories (demand, build, market)",
                facts={
                    "has_demand": has_demand,
                    "has_build": has_build,
                    "has_market": has_market,
                },
            )
        )

        # Helper to calculate weight & relevance for positive signals
        def get_sig_weight(s: ScoringEvidenceSignal) -> float:
            return 1.0 if s.relation_type == EvidenceRelationType.SUPPORTING else 0.5

        def get_safe_relevance(s: ScoringEvidenceSignal) -> float:
            r = s.relevance_score
            if math.isnan(r) or math.isinf(r) or r < 0.0:
                return 0.0
            if r > 1.0:
                return 1.0
            return r

        # 1.5 Freshness (0-8)
        # Calculate freshness score for each positive signal
        weighted_freshness_sum = 0.0
        total_weight = 0.0

        for s in positive_signals:
            w = get_sig_weight(s) * get_safe_relevance(s)
            if s.published_at is None:
                f_val = 0.50
            else:
                # Convert both to date to compare
                pub_date = s.published_at.date()
                if pub_date > as_of_date:
                    f_val = 1.00
                else:
                    days_diff = (as_of_date - pub_date).days
                    if days_diff <= 7:
                        f_val = 1.00
                    elif days_diff <= 30:
                        f_val = 0.85
                    elif days_diff <= 90:
                        f_val = 0.65
                    elif days_diff <= 365:
                        f_val = 0.40
                    else:
                        f_val = 0.20

            weighted_freshness_sum += f_val * w
            total_weight += w

        weighted_avg_freshness = (
            weighted_freshness_sum / total_weight if total_weight > 0.0 else 0.0
        )
        freshness_score = round_half_up(weighted_avg_freshness * 8)
        freshness_score = max(0, min(8, freshness_score))

        evidence_components.append(
            ScoreComponent(
                name="freshness",
                score=freshness_score,
                maximum=8,
                explanation="Time freshness weighted average",
                facts={"weighted_average_freshness": weighted_avg_freshness},
            )
        )

        # 1.6 Relevance (0-4)
        weighted_relevance_sum = 0.0
        total_rel_weight = 0.0

        for s in positive_signals:
            w = get_sig_weight(s)
            r = get_safe_relevance(s)
            weighted_relevance_sum += r * w
            total_rel_weight += w

        weighted_avg_relevance = (
            weighted_relevance_sum / total_rel_weight if total_rel_weight > 0.0 else 0.0
        )
        relevance_score = round_half_up(weighted_avg_relevance * 4)
        relevance_score = max(0, min(4, relevance_score))

        evidence_components.append(
            ScoreComponent(
                name="relevance",
                score=relevance_score,
                maximum=4,
                explanation="Weighted average of relevance score",
                facts={"weighted_average_relevance": weighted_avg_relevance},
            )
        )

        evidence_score = sum(c.score for c in evidence_components)

        # ----------------------------------------------------
        # 2. Feasibility Score (0-50)
        # ----------------------------------------------------
        feasibility_components = []

        # 2.1 Implementation Precedent (0-15)
        build_origins = {
            s.evidence_origin for s in positive_signals if s.signal_type in build_types
        }
        build_origins_count = len(build_origins)
        if build_origins_count == 0:
            impl_prec_score = 0
        elif build_origins_count == 1:
            impl_prec_score = 6
        elif build_origins_count == 2:
            impl_prec_score = 10
        else:
            impl_prec_score = 15

        feasibility_components.append(
            ScoreComponent(
                name="implementation_precedent",
                score=impl_prec_score,
                maximum=15,
                explanation="Count of distinct origins having build/project signals",
                facts={"build_origins_count": build_origins_count},
            )
        )

        # 2.2 Direct Demand Clarity (0-10)
        demand_clarity_types = {
            SignalType.PAIN,
            SignalType.REQUEST,
            SignalType.COMPLAINT,
        }
        demand_origins = {
            s.evidence_origin
            for s in positive_signals
            if s.signal_type in demand_clarity_types
        }
        demand_origins_count = len(demand_origins)
        if demand_origins_count == 0:
            demand_clarity_score = 0
        elif demand_origins_count == 1:
            demand_clarity_score = 4
        elif demand_origins_count == 2:
            demand_clarity_score = 7
        else:
            demand_clarity_score = 10

        feasibility_components.append(
            ScoreComponent(
                name="direct_demand_clarity",
                score=demand_clarity_score,
                maximum=10,
                explanation="Count of distinct origins having pain/request/complaint signals",
                facts={"demand_origins_count": demand_origins_count},
            )
        )

        # 2.3 Cluster Cohesion (0-10)
        cohesion_score = round_half_up(weighted_avg_relevance * 10)
        cohesion_score = max(0, min(10, cohesion_score))

        feasibility_components.append(
            ScoreComponent(
                name="cluster_cohesion",
                score=cohesion_score,
                maximum=10,
                explanation="Cohesion based on weighted average relevance",
                facts={"weighted_average_relevance": weighted_avg_relevance},
            )
        )

        # 2.4 Technical Specificity (0-5)
        specific_count = 0
        for s in positive_signals:
            meta = s.raw_metadata or {}
            has_tags = len(s.tags) >= 1
            has_github_fullname = "full_name" in meta
            has_github_repo_url = "repository_url" in meta or "html_url" in meta
            has_prog_lang = "language" in meta
            has_outbound_host = "outbound_host" in meta or "outbound_url" in meta

            if (
                has_tags
                or has_github_fullname
                or has_github_repo_url
                or has_prog_lang
                or has_outbound_host
            ):
                specific_count += 1

        specific_ratio = (
            specific_count / len(positive_signals) if positive_signals else 0.0
        )
        tech_spec_score = round_half_up(specific_ratio * 5)
        tech_spec_score = max(0, min(5, tech_spec_score))

        feasibility_components.append(
            ScoreComponent(
                name="technical_specificity",
                score=tech_spec_score,
                maximum=5,
                explanation="Ratio of positive signals with technical specifications",
                facts={
                    "specific_signal_count": specific_count,
                    "positive_signal_count": len(positive_signals),
                    "specific_ratio": specific_ratio,
                },
            )
        )

        # 2.5 Validation Reach (0-5)
        if distinct_source_type_count == 0:
            val_reach_score = 0
        elif distinct_source_type_count == 1:
            val_reach_score = 1
        elif distinct_source_type_count == 2:
            val_reach_score = 3
        else:
            val_reach_score = 5

        feasibility_components.append(
            ScoreComponent(
                name="validation_reach",
                score=val_reach_score,
                maximum=5,
                explanation="Validation channels (distinct source types)",
                facts={"distinct_source_type_count": distinct_source_type_count},
            )
        )

        # 2.6 Evidence Detail Quality (0-5)
        detailed_count = 0
        for s in positive_signals:
            excerpt_len = len(s.excerpt or "")
            tags_len = len(s.tags) if s.tags else 0
            if excerpt_len >= 120 or tags_len >= 2:
                detailed_count += 1

        detail_ratio = (
            detailed_count / len(positive_signals) if positive_signals else 0.0
        )
        detail_score = round_half_up(detail_ratio * 5)
        detail_score = max(0, min(5, detail_score))

        feasibility_components.append(
            ScoreComponent(
                name="evidence_detail_quality",
                score=detail_score,
                maximum=5,
                explanation="Ratio of detailed signals (excerpt >= 120 chars or tags >= 2)",
                facts={
                    "detailed_signal_count": detailed_count,
                    "positive_signal_count": len(positive_signals),
                    "detail_ratio": detail_ratio,
                },
            )
        )

        feasibility_score = sum(c.score for c in feasibility_components)

        # ----------------------------------------------------
        # 3. Penalty Score (-30 to 0)
        # ----------------------------------------------------
        penalty_components = []

        # 3.1 Contradicting Evidence (0 to -12)
        contradicting_signals = [
            s for s in signals if s.relation_type == EvidenceRelationType.CONTRADICTING
        ]
        contradicting_origins = {s.evidence_origin for s in contradicting_signals}
        contra_origins_count = len(contradicting_origins)
        if contra_origins_count == 0:
            contra_score = 0
        elif contra_origins_count == 1:
            contra_score = -4
        elif contra_origins_count == 2:
            contra_score = -8
        else:
            contra_score = -12

        penalty_components.append(
            ScoreComponent(
                name="contradicting_evidence",
                score=contra_score,
                maximum=0,
                explanation="Penalty for contradicting evidence origins",
                facts={"contradicting_origins_count": contra_origins_count},
            )
        )

        # 3.2 Origin Concentration (0 to -6)
        if len(positive_signals) <= 1:
            concentration_score = -6
            dominant_origin_ratio = 1.0
        else:
            origin_counts = Counter(s.evidence_origin for s in positive_signals)
            dominant_origin, dominant_count = origin_counts.most_common(1)[0]
            dominant_origin_ratio = dominant_count / len(positive_signals)

            if dominant_origin_ratio <= 0.50:
                concentration_score = 0
            elif dominant_origin_ratio <= 0.70:
                concentration_score = -2
            elif dominant_origin_ratio <= 0.90:
                concentration_score = -4
            else:
                concentration_score = -6

        penalty_components.append(
            ScoreComponent(
                name="origin_concentration",
                score=concentration_score,
                maximum=0,
                explanation="Penalty if a single origin dominates positive signals",
                facts={"dominant_origin_ratio": dominant_origin_ratio},
            )
        )

        # 3.3 Stale Evidence (0 to -6)
        # Use the already calculated weighted_avg_freshness
        if weighted_avg_freshness >= 0.60:
            stale_score = 0
        elif weighted_avg_freshness >= 0.40:
            stale_score = -3
        else:
            stale_score = -6

        penalty_components.append(
            ScoreComponent(
                name="stale_evidence",
                score=stale_score,
                maximum=0,
                explanation="Penalty for stale/old evidence (weighted freshness)",
                facts={"weighted_average_freshness": weighted_avg_freshness},
            )
        )

        # 3.4 Competition Saturation (0 to -6)
        if build_origins_count <= 2:
            saturation_score = 0
        elif build_origins_count <= 4:
            saturation_score = -3
        else:
            saturation_score = -6

        penalty_components.append(
            ScoreComponent(
                name="competition_saturation",
                score=saturation_score,
                maximum=0,
                explanation="Penalty for competitive saturation (build origins)",
                facts={"build_origins_count": build_origins_count},
            )
        )

        penalty_score = sum(c.score for c in penalty_components)
        penalty_score = max(-30, min(0, penalty_score))

        # ----------------------------------------------------
        # 4. Total Score (0-100)
        # ----------------------------------------------------
        raw_total = evidence_score + feasibility_score + penalty_score
        total_score = max(0, min(100, raw_total))

        # ----------------------------------------------------
        # 5. Confidence
        # ----------------------------------------------------
        # High Check
        is_high = (
            evidence_score >= 38
            and total_score >= 65
            and distinct_origin_count >= 4
            and distinct_source_type_count >= 2
            and penalty_score >= -10
        )

        # Medium Check
        is_medium = (
            not is_high
            and evidence_score >= 24
            and total_score >= 40
            and distinct_origin_count >= 2
        )

        if is_high:
            confidence = Confidence.HIGH
        elif is_medium:
            confidence = Confidence.MEDIUM
        else:
            confidence = Confidence.LOW

        # Generate Input Hash
        input_hash = calculate_scoring_input_hash(
            self.scoring_version, as_of_date, scoring_input
        )

        return OpportunityScore(
            opportunity_id=scoring_input.opportunity_id,
            scoring_version=self.scoring_version,
            as_of_date=as_of_date,
            input_hash=input_hash,
            evidence_score=evidence_score,
            feasibility_score=feasibility_score,
            penalty_score=penalty_score,
            total_score=total_score,
            confidence=confidence,
            evidence_components=tuple(evidence_components),
            feasibility_components=tuple(feasibility_components),
            penalty_components=tuple(penalty_components),
            supporting_signal_count=supporting_count,
            related_signal_count=related_count,
            contradicting_signal_count=contradicting_count,
            distinct_origin_count=distinct_origin_count,
            distinct_source_type_count=distinct_source_type_count,
        )

    def _score_v2(
        self,
        scoring_input: OpportunityScoringInput,
        *,
        as_of_date: date,
    ) -> OpportunityScore:
        import re
        from urllib.parse import urlparse

        from glintory.domain.enums import SignalRole

        signals = scoring_input.signals
        positive_signals = [
            s
            for s in signals
            if s.relation_type
            in (EvidenceRelationType.SUPPORTING, EvidenceRelationType.RELATED)
        ]

        # Calculate counts and metrics
        supporting_count = sum(
            1 for s in signals if s.relation_type == EvidenceRelationType.SUPPORTING
        )
        related_count = sum(
            1 for s in signals if s.relation_type == EvidenceRelationType.RELATED
        )
        contradicting_count = sum(
            1 for s in signals if s.relation_type == EvidenceRelationType.CONTRADICTING
        )

        # Thread grouping for independent evidence count
        def get_thread_key(sig) -> str:
            url = sig.canonical_url or ""
            hn_match = re.search(r"news\.ycombinator\.com/item\?id=(\d+)", url)
            if hn_match:
                return f"hn_thread_{hn_match.group(1)}"
            gh_issue_match = re.search(r"github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", url)
            if gh_issue_match:
                return f"github_issue_{gh_issue_match.group(1)}_{gh_issue_match.group(3)}"
            gh_repo_match = re.search(r"github\.com/([^/]+/[^/]+)", url)
            if gh_repo_match:
                return f"github_repo_{gh_repo_match.group(1)}"
            return url

        threads = {}
        for sig in positive_signals:
            key = get_thread_key(sig)
            threads.setdefault(key, []).append(sig)

        independent_evidence_count = len(threads)
        demand_evidence_count = sum(1 for sig in positive_signals if sig.signal_role == SignalRole.DEMAND)

        source_types = {sig.source_type for sig in positive_signals if sig.source_type}
        distinct_source_type_count = len(source_types)

        domains = set()
        for sig in positive_signals:
            if sig.canonical_url:
                parsed = urlparse(sig.canonical_url)
                if parsed.netloc:
                    domains.add(parsed.netloc)
        distinct_origin_count = len(domains)

        # Freshness calculation
        def get_sig_weight(s: ScoringEvidenceSignal) -> float:
            return 1.0 if s.relation_type == EvidenceRelationType.SUPPORTING else 0.5

        def get_safe_relevance(s: ScoringEvidenceSignal) -> float:
            r = s.relevance_score
            if math.isnan(r) or math.isinf(r) or r < 0.0:
                return 0.0
            if r > 1.0:
                return 1.0
            return r

        weighted_freshness_sum = 0.0
        total_weight = 0.0
        for s in positive_signals:
            w = get_sig_weight(s) * get_safe_relevance(s)
            if s.published_at is None:
                f_val = 0.50
            else:
                pub_date = s.published_at.date()
                if pub_date > as_of_date:
                    f_val = 1.00
                else:
                    days_diff = (as_of_date - pub_date).days
                    if days_diff <= 7:
                        f_val = 1.00
                    elif days_diff <= 30:
                        f_val = 0.85
                    elif days_diff <= 90:
                        f_val = 0.65
                    elif days_diff <= 365:
                        f_val = 0.40
                    else:
                        f_val = 0.20
            weighted_freshness_sum += f_val * w
            total_weight += w

        weighted_avg_freshness = (
            weighted_freshness_sum / total_weight if total_weight > 0.0 else 0.0
        )

        # ----------------------------------------------------
        # Evidence Score Components (0-45)
        # ----------------------------------------------------
        evidence_components = []

        # 1. Problem Clarity (0-25)
        prob_score = 0
        if demand_evidence_count == 1:
            prob_score = 10
        elif demand_evidence_count == 2:
            prob_score = 18
        elif demand_evidence_count >= 3:
            prob_score = 25

        evidence_components.append(
            ScoreComponent(
                name="problem_clarity_and_severity",
                score=prob_score,
                maximum=25,
                explanation="Clarity and severity based on demand signal counts.",
                facts={"demand_evidence_count": demand_evidence_count},
            )
        )

        # 2. Quality and Independence (0-20)
        qual_score = 0
        if independent_evidence_count == 1:
            qual_score = 5
        elif independent_evidence_count == 2:
            qual_score = 12
        elif independent_evidence_count >= 3:
            qual_score = 20

        evidence_components.append(
            ScoreComponent(
                name="evidence_quality_and_independence",
                score=qual_score,
                maximum=20,
                explanation="Independence based on distinct threads count.",
                facts={"independent_evidence_count": independent_evidence_count},
            )
        )

        evidence_score = sum(c.score for c in evidence_components)

        # ----------------------------------------------------
        # Feasibility Score Components (0-55)
        # ----------------------------------------------------
        feasibility_components = []

        # 3. Solo Developer Suitability (0-20)
        solo_suitability = 15
        feasibility_components.append(
            ScoreComponent(
                name="solo_developer_suitability",
                score=solo_suitability,
                maximum=20,
                explanation="Ease of development for a solo creator (default 15).",
                facts={},
            )
        )

        # 4. Distribution and Reach (0-15)
        reach_score = 10
        feasibility_components.append(
            ScoreComponent(
                name="distribution_and_reach",
                score=reach_score,
                maximum=15,
                explanation="Ability to acquire users easily (default 10).",
                facts={},
            )
        )

        # 5. Monetization and Asset Value (0-10)
        mon_score = 5
        feasibility_components.append(
            ScoreComponent(
                name="monetization_and_asset_value",
                score=mon_score,
                maximum=10,
                explanation="Monetization hypothesis or long term asset value (default 5).",
                facts={},
            )
        )

        # 6. Timing (0-10)
        timing_score = round_half_up(weighted_avg_freshness * 10)
        timing_score = max(0, min(10, timing_score))
        feasibility_components.append(
            ScoreComponent(
                name="market_timing",
                score=timing_score,
                maximum=10,
                explanation="Timing based on freshness of signals.",
                facts={"weighted_average_freshness": weighted_avg_freshness},
            )
        )

        feasibility_score = sum(c.score for c in feasibility_components)

        # ----------------------------------------------------
        # Penalty Score Components (-100 to 0)
        # ----------------------------------------------------
        penalty_components = []
        combined_text = "\n".join(
            f"{s.title or ''} {s.excerpt or ''}" for s in positive_signals
        ).lower()

        # Penalty check helper
        def check_penalty(name: str, keywords: list[str], penalty_val: int, explanation: str) -> ScoreComponent:
            has_penalty = any(kw in combined_text for kw in keywords)
            score_val = penalty_val if has_penalty else 0
            return ScoreComponent(
                name=name,
                score=score_val,
                maximum=0,
                explanation=explanation,
                facts={"detected": has_penalty},
            )

        # 1. Continuous AI Cost
        penalty_components.append(
            check_penalty("continuous_ai_cost", ["openai api", "llm cost", "expensive api", "gpt-4 cost", "token consumption"], -20, "High continuous API runtime cost.")
        )
        # 2. Sales Required
        penalty_components.append(
            check_penalty("sales_required", ["enterprise sales", "outbound sales", "contact sales", "b2b sales cycle"], -20, "Needs outbound or direct sales effort.")
        )
        # 3. Heavy Backend
        penalty_components.append(
            check_penalty("heavy_backend", ["database cluster", "high bandwidth", "infra cost", "heavy processing", "gpu cluster"], -15, "Requires heavy backend resources or high infrastructure cost.")
        )
        # 4. Support High Load
        penalty_components.append(
            check_penalty("high_support_load", ["high support", "customer ticket", "24/7 support", "support overhead"], -15, "Expected high support or operations load.")
        )
        # 5. Strong Competitors
        penalty_components.append(
            check_penalty("strong_competitors", ["strong competitor", "highly saturated", "crowded market", "incumbents"], -10, "Crowded or highly competitive space.")
        )
        # 6. Abstract Problem
        penalty_components.append(
            check_penalty("abstract_problem", ["generic problem", "abstract issue", "vague request"], -10, "Problem statement is abstract or generic.")
        )
        # 7. Tech Demo
        penalty_components.append(
            check_penalty("tech_demo", ["proof of concept only", "toy project", "experimental demo", "just a demo"], -20, "Signal is primarily a technical demo rather than a real-world demand.")
        )
        # 8. Unknown Target User
        penalty_components.append(
            check_penalty("unknown_target_user", ["unknown user", "target unclear", "who is this for"], -15, "Target audience or user segment is unclear.")
        )
        # 9. Copycat
        penalty_components.append(
            check_penalty("copycat", ["clone of", "copycat", "duplicate features"], -10, "Simple clone without meaningful differentiator.")
        )

        penalty_score = sum(c.score for c in penalty_components)

        raw_total = evidence_score + feasibility_score + penalty_score
        total_score = max(0, min(100, raw_total))

        # ----------------------------------------------------
        # Confidence Version 2
        # ----------------------------------------------------
        if independent_evidence_count >= 3 and demand_evidence_count >= 2 and distinct_source_type_count >= 2:
            confidence = Confidence.HIGH
        elif independent_evidence_count >= 2 and demand_evidence_count >= 1:
            confidence = Confidence.MEDIUM
        elif demand_evidence_count >= 1:
            confidence = Confidence.LOW
        else:
            confidence = Confidence.LOW  # Fallback

        input_hash = calculate_scoring_input_hash(
            self.scoring_version, as_of_date, scoring_input
        )

        return OpportunityScore(
            opportunity_id=scoring_input.opportunity_id,
            scoring_version=self.scoring_version,
            as_of_date=as_of_date,
            input_hash=input_hash,
            evidence_score=evidence_score,
            feasibility_score=feasibility_score,
            penalty_score=penalty_score,
            total_score=total_score,
            confidence=confidence,
            evidence_components=tuple(evidence_components),
            feasibility_components=tuple(feasibility_components),
            penalty_components=tuple(penalty_components),
            supporting_signal_count=supporting_count,
            related_signal_count=related_count,
            contradicting_signal_count=contradicting_count,
            distinct_origin_count=distinct_origin_count,
            distinct_source_type_count=distinct_source_type_count,
        )

