import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from glintory.domain.enums import (
    CollectionRunStatus,
    Confidence,
    EvidenceRelationType,
    OpportunityStatus,
    SignalType,
)
from glintory.domain.operations import CollectionTriggerType


class Base(DeclarativeBase):
    pass


def generate_uuid() -> str:
    # Generates a string representation of UUID4 to store UUIDs as 36-char strings in SQLite
    return str(uuid.uuid4())


def utc_now() -> datetime:
    # Standardizes all timestamps to UTC
    return datetime.now(UTC)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auth_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    # ondelete="SET NULL" prevents run deletion when its parent Source is deleted.
    source_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    trigger_type: Mapped[CollectionTriggerType] = mapped_column(
        Enum(
            CollectionTriggerType,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=CollectionTriggerType.CLI,
    )
    status: Mapped[CollectionRunStatus] = mapped_column(
        Enum(
            CollectionRunStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "uq_collection_runs_source_running",
            "source_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
        Index("idx_collection_runs_source_started", "source_id", "started_at"),
        Index("idx_collection_runs_status_started", "status", "started_at"),
        Index("idx_collection_runs_trigger_started", "trigger_type", "started_at"),
        CheckConstraint(
            "fetched_count >= 0", name="chk_runs_fetched_count_nonnegative"
        ),
        CheckConstraint(
            "inserted_count >= 0", name="chk_runs_inserted_count_nonnegative"
        ),
        CheckConstraint(
            "updated_count >= 0", name="chk_runs_updated_count_nonnegative"
        ),
        CheckConstraint(
            "duplicate_count >= 0", name="chk_runs_duplicate_count_nonnegative"
        ),
        CheckConstraint(
            "warning_count >= 0", name="chk_runs_warning_count_nonnegative"
        ),
        CheckConstraint("error_count >= 0", name="chk_runs_error_count_nonnegative"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    # RESTRICT is implicit by not defining ondelete. This prevents deleting a Source
    # if it has referenced Signals (failsafe).
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sources.id"), nullable=False
    )
    collection_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("collection_runs.id"), nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signal_type: Mapped[SignalType] = mapped_column(
        Enum(
            SignalType,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    freshness_score: Mapped[float] = mapped_column(Float, nullable=False)
    source_quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "freshness_score >= 0.0 AND freshness_score <= 1.0",
            name="chk_signals_freshness_score_range",
        ),
        CheckConstraint(
            "source_quality_score >= 0.0 AND source_quality_score <= 1.0",
            name="chk_signals_source_quality_score_range",
        ),
        # Partial unique index is used to only enforce uniqueness of external_id within a Source
        # if the external_id is actually provided.
        Index(
            "idx_signals_source_external",
            "source_id",
            "external_id",
            unique=True,
            sqlite_where=text("external_id IS NOT NULL"),
        ),
        UniqueConstraint(
            "source_id",
            "canonical_url",
            name="uq_signals_source_canonical_url",
        ),
        Index("idx_signals_canonical_url", "canonical_url"),
        Index("idx_signals_content_hash", "content_hash"),
        Index("idx_signals_published_at", "published_at"),
        Index("idx_signals_signal_type", "signal_type"),
        Index("idx_signals_source_id", "source_id"),
        Index("idx_signals_collection_run_id", "collection_run_id"),
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    problem_statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_user: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    existing_projects: Mapped[str | None] = mapped_column(Text, nullable=True)
    remaining_gap: Mapped[str | None] = mapped_column(Text, nullable=True)
    mvp_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    monetization_hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    distribution_hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_method: Mapped[str | None] = mapped_column(Text, nullable=True)

    evidence_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feasibility_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    penalty_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    confidence: Mapped[Confidence] = mapped_column(
        Enum(
            Confidence,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=Confidence.LOW,
        nullable=False,
    )
    status: Mapped[OpportunityStatus] = mapped_column(
        Enum(
            OpportunityStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=OpportunityStatus.INBOX,
        nullable=False,
    )
    generation_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cluster_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_clustered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_scoring_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    last_scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    evidence_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "evidence_score >= 0 AND evidence_score <= 50",
            name="chk_opportunities_evidence_score_range",
        ),
        CheckConstraint(
            "feasibility_score >= 0 AND feasibility_score <= 50",
            name="chk_opportunities_feasibility_score_range",
        ),
        CheckConstraint(
            "penalty_score <= 0", name="chk_opportunities_penalty_score_range"
        ),
        CheckConstraint(
            "total_score >= 0 AND total_score <= 100",
            name="chk_opportunities_total_score_range",
        ),
        Index("idx_opportunities_status", "status"),
        Index("idx_opportunities_total_score", "total_score"),
        Index("idx_opportunities_confidence", "confidence"),
        Index("idx_opportunities_created_at", "created_at"),
        Index("idx_opportunities_last_scored_at", "last_scored_at"),
        Index("idx_opportunities_evidence_updated_at", "evidence_updated_at"),
    )


class OpportunitySignal(Base):
    __tablename__ = "opportunity_signals"

    # CASCADE ensures relation cleanup when either Opportunity or Signal is deleted
    opportunity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    signal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    relation_type: Mapped[EvidenceRelationType] = mapped_column(
        Enum(
            EvidenceRelationType,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    association_source: Mapped[str] = mapped_column(
        String(20), default="clustering", nullable=False
    )
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "relevance_score >= 0.0 AND relevance_score <= 1.0",
            name="chk_opp_signals_relevance_score_range",
        ),
        CheckConstraint(
            "association_source IN ('clustering', 'manual')",
            name="chk_opp_signals_assoc_source",
        ),
        Index("idx_opp_signals_opp_id_is_excluded", "opportunity_id", "is_excluded"),
        Index("idx_opp_signals_sig_id_is_excluded", "signal_id", "is_excluded"),
    )


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    feasibility_score: Mapped[int] = mapped_column(Integer, nullable=False)
    penalty_score: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Confidence] = mapped_column(
        Enum(
            Confidence,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    scoring_version: Mapped[str] = mapped_column(String(50), nullable=False)
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_score_snapshots_opp_created", "opportunity_id", "created_at"),
        Index(
            "uq_score_snapshots_opp_version_input",
            "opportunity_id",
            "scoring_version",
            "input_hash",
            unique=True,
            sqlite_where=text("input_hash IS NOT NULL"),
        ),
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[OpportunityStatus | None] = mapped_column(
        Enum(
            OpportunityStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=True,
    )
    to_status: Mapped[OpportunityStatus] = mapped_column(
        Enum(
            OpportunityStatus,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("idx_decisions_opp_created", "opportunity_id", "created_at"),
    )


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    opportunity_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        # Reject empty or whitespace-only bodies to ensure notes have meaningful content.
        CheckConstraint("length(trim(body)) > 0", name="chk_notes_body_nonempty"),
    )
