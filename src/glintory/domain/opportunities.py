from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from glintory.domain.enums import Confidence, EvidenceRelationType, OpportunityStatus, SignalType


@dataclass(frozen=True, slots=True)
class OpportunityListFilters:
    status: OpportunityStatus | None = None
    confidence: Confidence | None = None
    generation_method: str | None = None
    minimum_score: int | None = None
    page: int = 1
    per_page: int = 25


@dataclass(frozen=True, slots=True)
class OpportunityListItem:
    id: str
    title: str
    generation_method: str
    cluster_version: str | None
    status: OpportunityStatus
    confidence: Confidence

    evidence_score: int
    feasibility_score: int
    penalty_score: int
    total_score: int

    evidence_count: int
    source_type_count: int

    last_clustered_at: datetime | None
    last_scored_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OpportunityEvidenceItem:
    signal_id: str
    title: str
    excerpt: str
    canonical_url: str
    source_id: str
    source_name: str
    source_type: str
    signal_type: SignalType
    relation_type: EvidenceRelationType
    relevance_score: float
    published_at: datetime | None
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class ScoreSnapshotDetail:
    id: str
    scoring_version: str
    as_of_date: date | None
    input_hash: str | None
    evidence_score: int
    feasibility_score: int
    penalty_score: int
    total_score: int
    confidence: Confidence
    explanation: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OpportunityDetail:
    id: str
    title: str
    problem_statement: str | None
    target_user: str | None
    proposed_solution: str | None
    existing_projects: Sequence[str]
    remaining_gap: str | None
    mvp_scope: str | None
    monetization_hypothesis: str | None
    distribution_hypothesis: str | None
    validation_method: str | None

    generation_method: str
    cluster_version: str | None
    status: OpportunityStatus
    confidence: Confidence

    evidence_score: int
    feasibility_score: int
    penalty_score: int
    total_score: int

    current_scoring_version: str | None
    last_clustered_at: datetime | None
    last_scored_at: datetime | None

    evidence: Sequence[OpportunityEvidenceItem]
    latest_snapshot: ScoreSnapshotDetail | None
    score_history: Sequence[ScoreSnapshotDetail]

    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OpportunityListPage:
    items: Sequence[OpportunityListItem]
    total_count: int
    page: int
    per_page: int
    total_pages: int
