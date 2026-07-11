from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from glintory.domain.enums import (
    Confidence,
    EvidenceRelationType,
    SignalRole,
    SignalType,
)


@dataclass(frozen=True, slots=True)
class ScoringEvidenceSignal:
    signal_id: str
    source_id: str
    source_type: str
    signal_type: SignalType
    signal_role: SignalRole
    relation_type: EvidenceRelationType
    relevance_score: float
    evidence_origin: str
    published_at: datetime | None
    collected_at: datetime
    title: str
    excerpt: str
    canonical_url: str | None
    tags: tuple[str, ...]
    raw_metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OpportunityScoringInput:
    opportunity_id: str
    generation_method: str
    status: str  # OpportunityStatus as string or Enum
    signals: tuple[ScoringEvidenceSignal, ...]


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    name: str
    score: int
    maximum: int
    explanation: str
    facts: Mapping[str, int | float | str | bool | None]


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    opportunity_id: str
    scoring_version: str
    as_of_date: date
    input_hash: str

    evidence_score: int
    feasibility_score: int
    penalty_score: int
    total_score: int
    confidence: Confidence

    evidence_components: tuple[ScoreComponent, ...]
    feasibility_components: tuple[ScoreComponent, ...]
    penalty_components: tuple[ScoreComponent, ...]

    supporting_signal_count: int
    related_signal_count: int
    contradicting_signal_count: int
    distinct_origin_count: int
    distinct_source_type_count: int


@dataclass(frozen=True, slots=True)
class OpportunityScoringResult:
    scoring_version: str
    as_of_date: date
    dry_run: bool

    analyzed_opportunity_count: int
    scored_opportunity_count: int
    unchanged_opportunity_count: int
    skipped_opportunity_count: int

    created_snapshot_count: int
    updated_opportunity_count: int

    scored_opportunity_ids: tuple[str, ...]
    warnings: tuple[str, ...]
