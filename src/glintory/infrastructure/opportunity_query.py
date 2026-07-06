import math
from typing import Sequence

from sqlalchemy import case, func, nulls_last
from sqlalchemy.orm import Session

from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.models import Opportunity, OpportunitySignal, ScoreSnapshot, Signal, Source
from glintory.domain.opportunities import (
    OpportunityDetail,
    OpportunityEvidenceItem,
    OpportunityListFilters,
    OpportunityListItem,
    OpportunityListPage,
    ScoreSnapshotDetail,
)


class OpportunityQueryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _get_sorting_clauses(self) -> list:
        # Custom CASE expression to sort confidence: High -> Medium -> Low
        confidence_order = case(
            (Opportunity.confidence == Confidence.HIGH, 1),
            (Opportunity.confidence == Confidence.MEDIUM, 2),
            (Opportunity.confidence == Confidence.LOW, 3),
            else_=4,
        ).asc()

        return [
            Opportunity.total_score.desc(),
            Opportunity.evidence_score.desc(),
            confidence_order,
            nulls_last(Opportunity.last_scored_at.desc()),
            Opportunity.created_at.desc(),
            Opportunity.id.asc(),
        ]

    def list_opportunities(
        self,
        filters: OpportunityListFilters,
    ) -> OpportunityListPage:
        """Query and paginate opportunities with filters."""
        # 1. Validation of pagination parameters
        page = max(1, filters.page)
        per_page = filters.per_page
        if per_page not in (10, 25, 50, 100):
            per_page = 25

        # 2. Subquery to count signals and distinct source types in a single query
        count_subq = (
            self.session.query(
                OpportunitySignal.opportunity_id,
                func.count(OpportunitySignal.signal_id).label("evidence_count"),
                func.count(func.distinct(Source.source_type)).label("source_type_count"),
            )
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .group_by(OpportunitySignal.opportunity_id)
            .subquery()
        )

        query = self.session.query(
            Opportunity,
            func.coalesce(count_subq.c.evidence_count, 0).label("evidence_count"),
            func.coalesce(count_subq.c.source_type_count, 0).label("source_type_count"),
        ).outerjoin(count_subq, Opportunity.id == count_subq.c.opportunity_id)

        # 3. Apply status filtering
        if filters.status:
            query = query.filter(Opportunity.status == filters.status)
        else:
            # Default active status filter
            query = query.filter(
                Opportunity.status.notin_(
                    [OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED]
                )
            )

        # 4. Apply other filters
        if filters.confidence:
            query = query.filter(Opportunity.confidence == filters.confidence)
        if filters.generation_method:
            query = query.filter(
                Opportunity.generation_method == filters.generation_method
            )
        if filters.minimum_score is not None:
            query = query.filter(Opportunity.total_score >= filters.minimum_score)

        # 5. Get total count
        total_count = query.count()

        # 6. Apply sorting and pagination
        sorting = self._get_sorting_clauses()
        query = query.order_by(*sorting)

        offset = (page - 1) * per_page
        results = query.offset(offset).limit(per_page).all()

        items = []
        for opp, ev_count, src_count in results:
            items.append(
                OpportunityListItem(
                    id=opp.id,
                    title=opp.title,
                    generation_method=opp.generation_method or "manual",
                    cluster_version=opp.cluster_version,
                    status=opp.status,
                    confidence=opp.confidence,
                    evidence_score=opp.evidence_score,
                    feasibility_score=opp.feasibility_score,
                    penalty_score=opp.penalty_score,
                    total_score=opp.total_score,
                    evidence_count=ev_count,
                    source_type_count=src_count,
                    last_clustered_at=opp.last_clustered_at,
                    last_scored_at=opp.last_scored_at,
                    created_at=opp.created_at,
                    updated_at=opp.updated_at,
                )
            )

        total_pages = math.ceil(total_count / per_page) if total_count > 0 else 0

        return OpportunityListPage(
            items=items,
            total_count=total_count,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
        )

    def get_detail(
        self,
        opportunity_id: str,
        history_limit: int = 20,
    ) -> OpportunityDetail | None:
        """Query detailed opportunity fields, evidence signals, and score history."""
        opp = self.session.get(Opportunity, opportunity_id)
        if not opp:
            return None

        # Fetch evidence signals (excluding source config for security)
        evidence_rows = (
            self.session.query(OpportunitySignal, Signal, Source)
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .filter(OpportunitySignal.opportunity_id == opportunity_id)
            .order_by(Signal.collected_at.desc(), Signal.id.asc())
            .all()
        )

        evidence_items = []
        for opp_sig, sig, src in evidence_rows:
            evidence_items.append(
                OpportunityEvidenceItem(
                    signal_id=sig.id,
                    title=sig.title,
                    excerpt=sig.excerpt or "",
                    canonical_url=sig.canonical_url,
                    source_id=src.id,
                    source_name=src.name,
                    source_type=src.source_type,
                    signal_type=sig.signal_type,
                    relation_type=opp_sig.relation_type,
                    relevance_score=opp_sig.relevance_score,
                    published_at=sig.published_at,
                    collected_at=sig.collected_at,
                )
            )

        # Fetch score history
        history_rows = (
            self.session.query(ScoreSnapshot)
            .filter(ScoreSnapshot.opportunity_id == opportunity_id)
            .order_by(ScoreSnapshot.created_at.desc())
            .limit(history_limit)
            .all()
        )

        history_items = []
        for snap in history_rows:
            history_items.append(
                ScoreSnapshotDetail(
                    id=snap.id,
                    scoring_version=snap.scoring_version,
                    as_of_date=snap.as_of_date,
                    input_hash=snap.input_hash,
                    evidence_score=snap.evidence_score,
                    feasibility_score=snap.feasibility_score,
                    penalty_score=snap.penalty_score,
                    total_score=snap.total_score,
                    confidence=snap.confidence,
                    explanation=snap.explanation or {},
                    created_at=snap.created_at,
                )
            )

        latest_snapshot = history_items[0] if history_items else None

        # parse existing projects (could be raw text or JSON string/list)
        # Note: formatting and safety handling of existing_projects is done at the service layer
        raw_existing_projects = opp.existing_projects

        return OpportunityDetail(
            id=opp.id,
            title=opp.title,
            problem_statement=opp.problem_statement,
            target_user=opp.target_user,
            proposed_solution=opp.proposed_solution,
            existing_projects=[raw_existing_projects] if raw_existing_projects else [],
            remaining_gap=opp.remaining_gap,
            mvp_scope=opp.mvp_scope,
            monetization_hypothesis=opp.monetization_hypothesis,
            distribution_hypothesis=opp.distribution_hypothesis,
            validation_method=opp.validation_method,
            generation_method=opp.generation_method or "manual",
            cluster_version=opp.cluster_version,
            status=opp.status,
            confidence=opp.confidence,
            evidence_score=opp.evidence_score,
            feasibility_score=opp.feasibility_score,
            penalty_score=opp.penalty_score,
            total_score=opp.total_score,
            current_scoring_version=opp.current_scoring_version,
            last_clustered_at=opp.last_clustered_at,
            last_scored_at=opp.last_scored_at,
            evidence=evidence_items,
            latest_snapshot=latest_snapshot,
            score_history=history_items,
            created_at=opp.created_at,
            updated_at=opp.updated_at,
        )

    def get_top_opportunities(
        self,
        limit: int = 3,
    ) -> Sequence[OpportunityListItem]:
        """Fetch scored top-scoring opportunities for Today screen."""
        count_subq = (
            self.session.query(
                OpportunitySignal.opportunity_id,
                func.count(OpportunitySignal.signal_id).label("evidence_count"),
                func.count(func.distinct(Source.source_type)).label("source_type_count"),
            )
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .group_by(OpportunitySignal.opportunity_id)
            .subquery()
        )

        query = (
            self.session.query(
                Opportunity,
                func.coalesce(count_subq.c.evidence_count, 0).label("evidence_count"),
                func.coalesce(count_subq.c.source_type_count, 0).label(
                    "source_type_count"
                ),
            )
            .outerjoin(count_subq, Opportunity.id == count_subq.c.opportunity_id)
            .filter(
                Opportunity.status.notin_(
                    [OpportunityStatus.REJECTED, OpportunityStatus.ARCHIVED]
                )
            )
            .filter(Opportunity.current_scoring_version.isnot(None))
            .filter(Opportunity.last_scored_at.isnot(None))
            .filter(Opportunity.total_score > 0)
        )

        sorting = self._get_sorting_clauses()
        query = query.order_by(*sorting).limit(limit)

        results = query.all()
        items = []
        for opp, ev_count, src_count in results:
            items.append(
                OpportunityListItem(
                    id=opp.id,
                    title=opp.title,
                    generation_method=opp.generation_method or "manual",
                    cluster_version=opp.cluster_version,
                    status=opp.status,
                    confidence=opp.confidence,
                    evidence_score=opp.evidence_score,
                    feasibility_score=opp.feasibility_score,
                    penalty_score=opp.penalty_score,
                    total_score=opp.total_score,
                    evidence_count=ev_count,
                    source_type_count=src_count,
                    last_clustered_at=opp.last_clustered_at,
                    last_scored_at=opp.last_scored_at,
                    created_at=opp.created_at,
                    updated_at=opp.updated_at,
                )
            )
        return items
