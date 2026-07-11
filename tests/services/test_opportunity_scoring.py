from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from glintory.domain.enums import (
    Confidence,
    EvidenceRelationType,
    OpportunityStatus,
    SignalRole,
    SignalType,
)
from glintory.domain.models import (
    Base,
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)
from glintory.domain.scoring import OpportunityScoringInput, ScoringEvidenceSignal
from glintory.infrastructure.opportunity_scoring_repository import (
    OpportunityScoringRepository,
)
from glintory.services.opportunity_scoring import OpportunityScoringEngine
from glintory.services.opportunity_scoring_service import OpportunityScoringService
from glintory.services.scoring_hash import calculate_scoring_input_hash


@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory


@pytest.fixture
def db_session(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def test_deterministic_scoring_hash():
    published = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    collected = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    sig1 = ScoringEvidenceSignal(
        signal_id="sig-1",
        source_id="src-1",
        source_type="github",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.UNKNOWN,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
        evidence_origin="owner/repo-a",
        published_at=published,
        collected_at=collected,
        title="Pain point 1",
        excerpt="Important excerpt",
        canonical_url=None,
        tags=("python", "ml"),
        raw_metadata={
            "full_name": "owner/repo-a",
            "html_url": "https://github.com/owner/repo-a",
        },
    )

    sig2 = ScoringEvidenceSignal(
        signal_id="sig-2",
        source_id="src-2",
        source_type="hackernews",
        signal_type=SignalType.LAUNCH,
        signal_role=SignalRole.UNKNOWN,
        relation_type=EvidenceRelationType.RELATED,
        relevance_score=0.7,
        evidence_origin="hackernews",
        published_at=None,
        collected_at=collected,
        title="Launch 1",
        excerpt="Launch excerpt",
        canonical_url=None,
        tags=(),
        raw_metadata={"outbound_host": "hackernews"},
    )

    inp = OpportunityScoringInput(
        opportunity_id="opp-123",
        generation_method="deterministic_cluster",
        status="inbox",
        signals=(sig1, sig2),
    )

    h1 = calculate_scoring_input_hash("v1", date(2026, 7, 1), inp)
    h2 = calculate_scoring_input_hash("v1", date(2026, 7, 1), inp)
    assert h1 == h2

    # Change date
    h3 = calculate_scoring_input_hash("v1", date(2026, 7, 2), inp)
    assert h1 != h3

    # Change signal relevance
    sig1_changed = ScoringEvidenceSignal(
        signal_id="sig-1",
        source_id="src-1",
        source_type="github",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.UNKNOWN,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.8,  # changed
        evidence_origin="owner/repo-a",
        published_at=published,
        collected_at=collected,
        title="Pain point 1",
        excerpt="Important excerpt",
        canonical_url=None,
        tags=("python", "ml"),
        raw_metadata={
            "full_name": "owner/repo-a",
            "html_url": "https://github.com/owner/repo-a",
        },
    )
    inp_changed = OpportunityScoringInput(
        opportunity_id="opp-123",
        generation_method="deterministic_cluster",
        status="inbox",
        signals=(sig1_changed, sig2),
    )
    h4 = calculate_scoring_input_hash("v1", date(2026, 7, 1), inp_changed)
    assert h1 != h4


def test_scoring_engine_basic_rules():
    engine = OpportunityScoringEngine("v1")

    published = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    collected = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

    # 1 signal -> Low diversity
    sig = ScoringEvidenceSignal(
        signal_id="sig-1",
        source_id="src-1",
        source_type="github",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.UNKNOWN,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        evidence_origin="owner/repo-a",
        published_at=published,
        collected_at=collected,
        title="Pain point",
        excerpt="Excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text excerpt text",  # >= 120 chars
        canonical_url=None,
        tags=("python", "ml"),
        raw_metadata={"full_name": "owner/repo-a"},
    )

    inp = OpportunityScoringInput(
        opportunity_id="opp-1",
        generation_method="deterministic_cluster",
        status="inbox",
        signals=(sig,),
    )

    score = engine.score(inp, as_of_date=date(2026, 7, 1))

    # Evidence Score expected breakdown:
    # - Volume: 1 supporting -> effective count 1.0 -> 3
    # - Origin Diversity: 1 origin -> 2
    # - Source Type Diversity: 1 type -> 2
    # - Coverage: 1 demand type -> 2
    # - Freshness: published 2026-06-30 vs 2026-07-01 -> days_diff 1 <= 7 -> f_val = 1.0. Weight = 1.0. relevance = 1.0. Avg freshness = 1.0 -> 8
    # - Relevance: weighted relevance avg 1.0 -> 4
    # Total evidence = 3 + 2 + 2 + 2 + 8 + 4 = 21
    assert score.evidence_score == 21

    # Feasibility Score expected breakdown:
    # - Implementation Precedent: 0 build origins -> 0
    # - Direct Demand Clarity: 1 demand origin -> 4
    # - Cluster Cohesion: weighted relevance 1.0 * 10 = 10
    # - Technical Specificity: 1 / 1 specific (has full_name) -> 5
    # - Validation Reach: 1 source type -> 1
    # - Evidence Detail Quality: excerpt >= 120 chars -> 1 detailed -> 5
    # Total feasibility = 0 + 4 + 10 + 5 + 1 + 5 = 25
    assert score.feasibility_score == 25

    # Penalty Score expected breakdown:
    # - Contradicting Evidence: 0 -> 0
    # - Origin Concentration: 1 positive signal -> -6 (automatically -6)
    # - Stale Evidence: avg freshness 1.0 >= 0.60 -> 0
    # - Competition Saturation: 0 build origins -> 0
    # Total penalty = -6
    assert score.penalty_score == -6

    # Total Score = 21 + 25 - 6 = 40
    assert score.total_score == 40
    # Confidence: evidence_score 21 < 24 -> LOW
    assert score.confidence == Confidence.LOW


@pytest.mark.anyio
async def test_opportunity_scoring_service_integration(
    db_session: Session, db_session_factory
):
    # Setup test source
    src = Source(id="src-1", name="Test Source", source_type="github", config={})
    db_session.add(src)
    db_session.commit()

    # Create an opportunity
    opp = Opportunity(
        id="opp-1",
        title="Test Opportunity",
        generation_method="deterministic_cluster",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.LOW,
        current_scoring_version="v1",
    )
    db_session.add(opp)
    db_session.commit()

    # Create associated signal
    sig = Signal(
        id="sig-1",
        source_id="src-1",
        title="Test Signal",
        excerpt="Signal excerpt text",
        canonical_url="https://github.com/owner/repo-a",
        signal_type=SignalType.PAIN,
        collected_at=datetime.now(UTC),
        raw_metadata={"full_name": "owner/repo-a"},
        content_hash="test-content-hash",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    db_session.add(sig)
    db_session.commit()

    # Link signal to opportunity
    opp_sig = OpportunitySignal(
        opportunity_id="opp-1",
        signal_id="sig-1",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
    )
    db_session.add(opp_sig)
    db_session.commit()

    # Instantiate service
    engine = OpportunityScoringEngine("v1")
    service = OpportunityScoringService(
        session_factory=db_session_factory,
        repository_factory=OpportunityScoringRepository,
        engine=engine,
        scoring_version="v1",
        clock=lambda: datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )

    # 1. Run scoring (not dry-run)
    res = service.score_opportunities()
    assert res.scored_opportunity_count == 1
    assert res.created_snapshot_count == 1
    assert "opp-1" in res.scored_opportunity_ids

    # Verify DB update
    db_session.expire_all()
    updated_opp = db_session.get(Opportunity, "opp-1")
    assert updated_opp is not None
    assert updated_opp.last_scored_at is not None
    assert updated_opp.current_scoring_version == "v1"
    assert updated_opp.total_score > 0

    # Verify Snapshot creation
    snaps = db_session.query(ScoreSnapshot).filter_by(opportunity_id="opp-1").all()
    assert len(snaps) == 1
    assert snaps[0].input_hash is not None

    # 2. Re-run scoring (should be unchanged)
    res2 = service.score_opportunities()
    assert res2.scored_opportunity_count == 0
    assert res2.unchanged_opportunity_count == 1

    # Verify no duplicate snapshot created
    snaps2 = db_session.query(ScoreSnapshot).filter_by(opportunity_id="opp-1").all()
    assert len(snaps2) == 1

    # 3. Dry run
    # Modify relevance score to force hash change
    opp_sig.relevance_score = 0.8
    db_session.commit()

    res_dry = service.score_opportunities(dry_run=True)
    assert res_dry.scored_opportunity_count == 1
    assert res_dry.created_snapshot_count == 0
    assert res_dry.dry_run is True

    # DB should not be updated and no snapshots added
    snaps3 = db_session.query(ScoreSnapshot).filter_by(opportunity_id="opp-1").all()
    assert len(snaps3) == 1  # Still 1
