import math
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import case, func, nulls_last, or_
from sqlalchemy.orm import Session

from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.models import (
    Decision,
    Note,
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)
from glintory.domain.opportunities import (
    DecisionHistoryItem,
    OpportunityDetail,
    OpportunityEvidenceItem,
    OpportunityListFilters,
    OpportunityListItem,
    OpportunityListPage,
    OpportunityNoteItem,
    ScoreSnapshotDetail,
)


def check_stale(
    current_scoring_version: str | None,
    last_scored_at: datetime | None,
    evidence_updated_at: datetime | None,
) -> bool:
    """Helper to determine if an opportunity score is outdated."""
    if not current_scoring_version or not last_scored_at:
        return True
    if evidence_updated_at:
        e_dt = (
            evidence_updated_at.astimezone(UTC)
            if evidence_updated_at.tzinfo
            else evidence_updated_at.replace(tzinfo=UTC)
        )
        l_dt = (
            last_scored_at.astimezone(UTC)
            if last_scored_at.tzinfo
            else last_scored_at.replace(tzinfo=UTC)
        )
        return e_dt > l_dt
    return False


class OpportunityQueryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _get_sorting_clauses(self, sort_by_stale: bool = False) -> list:
        # Custom CASE expression to sort confidence: High -> Medium -> Low
        confidence_order = case(
            (Opportunity.confidence == Confidence.HIGH, 1),
            (Opportunity.confidence == Confidence.MEDIUM, 2),
            (Opportunity.confidence == Confidence.LOW, 3),
            else_=4,
        ).asc()

        sorting = []
        if sort_by_stale:
            # Sort stale scores to the bottom (stale=1, current=0)
            stale_order = case(
                (Opportunity.current_scoring_version.is_(None), 1),
                (Opportunity.last_scored_at.is_(None), 1),
                (
                    (Opportunity.evidence_updated_at.isnot(None))
                    & (Opportunity.evidence_updated_at > Opportunity.last_scored_at),
                    1,
                ),
                else_=0,
            ).asc()
            sorting.append(stale_order)

        sorting.extend(
            [
                Opportunity.total_score.desc(),
                Opportunity.evidence_score.desc(),
                confidence_order,
                nulls_last(Opportunity.last_scored_at.desc()),
                Opportunity.created_at.desc(),
                Opportunity.id.asc(),
            ]
        )
        return sorting

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

        # 2. Subquery to count signals and distinct source types in a single query (active only)
        count_subq = (
            self.session.query(
                OpportunitySignal.opportunity_id,
                func.count(OpportunitySignal.signal_id).label("evidence_count"),
                func.count(func.distinct(Source.source_type)).label(
                    "source_type_count"
                ),
            )
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .filter(OpportunitySignal.is_excluded.is_(False))
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
        sort_by_stale = filters.status == OpportunityStatus.WATCH
        sorting = self._get_sorting_clauses(sort_by_stale=sort_by_stale)
        query = query.order_by(*sorting)

        offset = (page - 1) * per_page
        results = query.offset(offset).limit(per_page).all()

        items = []
        for opp, ev_count, src_count in results:
            stale = check_stale(
                opp.current_scoring_version, opp.last_scored_at, opp.evidence_updated_at
            )
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
                    score_is_stale=stale,
                    evidence_updated_at=opp.evidence_updated_at,
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

        # Fetch decisions ordered by created_at DESC
        decisions_rows = (
            self.session.query(Decision)
            .filter(Decision.opportunity_id == opportunity_id)
            .order_by(Decision.created_at.desc())
            .all()
        )
        decisions = [
            DecisionHistoryItem(
                id=d.id,
                from_status=d.from_status,
                to_status=d.to_status,
                reason=d.reason,
                created_at=d.created_at,
            )
            for d in decisions_rows
        ]

        # Fetch notes ordered by created_at DESC
        notes_rows = (
            self.session.query(Note)
            .filter(Note.opportunity_id == opportunity_id)
            .order_by(Note.created_at.desc())
            .all()
        )
        notes = [
            OpportunityNoteItem(
                id=n.id,
                body=n.body,
                created_at=n.created_at,
                updated_at=n.updated_at,
            )
            for n in notes_rows
        ]

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
        active_evidence = []
        excluded_evidence = []

        for opp_sig, sig, src in evidence_rows:
            item = OpportunityEvidenceItem(
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
                association_source=opp_sig.association_source,
                is_excluded=opp_sig.is_excluded,
                reviewed_at=opp_sig.reviewed_at,
                review_note=opp_sig.review_note,
            )
            evidence_items.append(item)
            if opp_sig.is_excluded:
                excluded_evidence.append(item)
            else:
                active_evidence.append(item)

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
        raw_existing_projects = opp.existing_projects
        stale = check_stale(
            opp.current_scoring_version, opp.last_scored_at, opp.evidence_updated_at
        )

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
            score_is_stale=stale,
            evidence_updated_at=opp.evidence_updated_at,
            decisions=decisions,
            notes=notes,
            active_evidence=active_evidence,
            excluded_evidence=excluded_evidence,
        )

    def get_top_opportunities(
        self,
        limit: int = 3,
    ) -> Sequence[OpportunityListItem]:
        """Fetch scored top-scoring opportunities for Today screen (excluding stale ones)."""
        count_subq = (
            self.session.query(
                OpportunitySignal.opportunity_id,
                func.count(OpportunitySignal.signal_id).label("evidence_count"),
                func.count(func.distinct(Source.source_type)).label(
                    "source_type_count"
                ),
            )
            .join(Signal, OpportunitySignal.signal_id == Signal.id)
            .join(Source, Signal.source_id == Source.id)
            .filter(OpportunitySignal.is_excluded.is_(False))
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
                Opportunity.status.in_(
                    [
                        OpportunityStatus.INBOX,
                        OpportunityStatus.WATCH,
                        OpportunityStatus.VALIDATE,
                        OpportunityStatus.BUILD,
                    ]
                )
            )
            .filter(Opportunity.current_scoring_version.isnot(None))
            .filter(Opportunity.last_scored_at.isnot(None))
            .filter(Opportunity.total_score > 0)
            .filter(
                or_(
                    Opportunity.evidence_updated_at.is_(None),
                    Opportunity.evidence_updated_at <= Opportunity.last_scored_at,
                )
            )
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
                    score_is_stale=False,  # Excluded by query filter
                    evidence_updated_at=opp.evidence_updated_at,
                )
            )
        return items
