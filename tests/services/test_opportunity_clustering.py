from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.domain.clustering import (
    OpportunityClusteringConfig,
    calculate_evidence_origin,
)
from glintory.domain.enums import OpportunityStatus, SignalRole, SignalType
from glintory.domain.models import Base, Opportunity, OpportunitySignal, Signal, Source
from glintory.infrastructure.opportunity_clustering_repository import (
    OpportunityClusteringRepository,
)
from glintory.services.opportunity_analysis import (
    OpportunityAnalysisService,
)
from glintory.services.opportunity_clustering import OpportunityClusteringEngine


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://")

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
    yield session
    session.close()


def test_calculate_evidence_origin():
    # GitHub test
    assert (
        calculate_evidence_origin("github", "https://github.com/google/jax")
        == "github:google/jax"
    )
    assert (
        calculate_evidence_origin("github", "https://api.github.com/repos/google/jax")
        == "github:google/jax"
    )
    assert calculate_evidence_origin("github", "https://github.com/google") == "github:generic"

    # Hacker News test
    assert (
        calculate_evidence_origin(
            "hackernews", "https://news.ycombinator.com/item?id=1"
        )
        == "hackernews:item:1"
    )

    # RSS test
    assert (
        calculate_evidence_origin("rss", "https://blog.rust-lang.org/feed.xml")
        == "blog.rust-lang.org/feed.xml"
    )
    assert (
        calculate_evidence_origin("rss", "https://localhost:8000/feed.xml")
        == "localhost/feed.xml"
    )


def test_clustering_engine_basic():
    engine = OpportunityClusteringEngine(
        OpportunityClusteringConfig(similarity_threshold=0.35)
    )

    now = datetime.now(UTC)
    s1 = Signal(
        id="s1",
        title="Building a self-hosted database",
        excerpt="Let's build a self-hosted database in Rust for fun.",
        canonical_url="https://example.com/1",
        collected_at=now,
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        source_id="src1",
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    s2 = Signal(
        id="s2",
        title="Self-hosted database with Rust",
        excerpt="A project exploring how to build self-hosted databases in Rust.",
        canonical_url="https://example.com/2",
        collected_at=now,
        signal_type=SignalType.PROJECT,
        source_id="src1",
        content_hash="h2",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    s3 = Signal(
        id="s3",
        title="Recipe for sourdough bread",
        excerpt="How to bake the perfect sourdough bread at home.",
        canonical_url="https://example.com/3",
        collected_at=now,
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        source_id="src1",
        content_hash="h3",
        freshness_score=1.0,
        source_quality_score=1.0,
    )

    clusters = engine.cluster_signals([s1, s2, s3])
    # Expect 2 clusters: {s1, s2} and {s3}
    assert len(clusters) == 2

    # Check representative signal logic
    db_cluster = [
        c for c in clusters if "sourdough" not in c["representative_signal"].title
    ][0]
    assert db_cluster["representative_signal"].id == "s1"
    assert len(db_cluster["signals"]) == 2


@pytest.mark.asyncio
async def test_analysis_service_flow(db_session, db_session_factory):
    # Setup source to satisfy foreign key constraint
    src = Source(
        id="src_gh",
        name="GitHub Source",
        source_type="github",
        config={},
    )
    db_session.add(src)
    db_session.commit()

    # Setup signals
    now = datetime.now(UTC)
    s1 = Signal(
        id="sig1",
        source_id="src_gh",
        canonical_url="https://github.com/foo/db",
        title="Database project in Python",
        excerpt="Building a lightweight relational database in pure Python for developers. Currently, users face the problem that SQLite is manually configured and slow. We wish to create an MVP framework to solve this.",
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.SUPPLY,
        collected_at=now,
        content_hash="hash1",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    s2 = Signal(
        id="sig2",
        source_id="src_gh",
        canonical_url="https://github.com/bar/db",
        title="Relational database implementation",
        excerpt="A simple SQL database engine written in Python for developers. Users suffer from the pain of slow manual query runs. We need a fast MVP version.",
        signal_type=SignalType.REQUEST,
        signal_role=SignalRole.DEMAND,
        collected_at=now,
        content_hash="hash2",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    db_session.add_all([s1, s2])
    db_session.commit()

    config = OpportunityClusteringConfig(similarity_threshold=0.01)
    repo = OpportunityClusteringRepository(db_session)
    engine = OpportunityClusteringEngine(config)
    service = OpportunityAnalysisService(db_session, repo, engine, config)

    # 1. First run: should cluster s1 and s2 into a single new opportunity
    result = service.analyze_and_cluster(dry_run=False)
    assert result.analyzed_signals_count == 2
    assert result.created_opportunities_count == 1
    assert result.linked_signals_count == 2

    # Verify DB
    db_session.expire_all()
    opps = db_session.query(Opportunity).all()
    assert len(opps) == 1
    opp = opps[0]
    assert opp.generation_method == "deterministic_cluster"
    assert opp.status == OpportunityStatus.INBOX

    links = db_session.query(OpportunitySignal).all()
    assert len(links) == 2
    assert {link.signal_id for link in links} == {"sig1", "sig2"}

    # 2. Second run: identical run, should do nothing (idempotency)
    result2 = service.analyze_and_cluster(dry_run=False)
    assert result2.analyzed_signals_count == 0  # No unassociated signals
    assert result2.created_opportunities_count == 0

    # 3. Add a new related signal
    s3 = Signal(
        id="sig3",
        source_id="src_gh",
        canonical_url="https://github.com/baz/db",
        title="Python SQL DB engine",
        excerpt="Another SQL database built on Python.",
        signal_type=SignalType.PROJECT,
        collected_at=now,
        content_hash="hash3",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    db_session.add(s3)
    db_session.commit()

    # Third run: should match s3 to the existing opportunity
    result3 = service.analyze_and_cluster(dry_run=False)
    assert result3.analyzed_signals_count == 1
    assert result3.created_opportunities_count == 0
    assert result3.linked_signals_count == 1

    db_session.expire_all()
    links = db_session.query(OpportunitySignal).all()
    assert len(links) == 3
    assert {link.signal_id for link in links} == {"sig1", "sig2", "sig3"}


@pytest.mark.asyncio
async def test_analysis_service_dry_run(db_session):
    # Setup source to satisfy foreign key constraint
    src = Source(
        id="src_gh",
        name="GitHub Source",
        source_type="github",
        config={},
    )
    db_session.add(src)
    db_session.commit()

    now = datetime.now(UTC)
    s1 = Signal(
        id="sig1",
        source_id="src_gh",
        canonical_url="https://github.com/foo/db",
        title="Database project in Python",
        excerpt="Building a lightweight relational database in pure Python.",
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        collected_at=now,
        content_hash="hash1",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    db_session.add(s1)
    db_session.commit()

    config = OpportunityClusteringConfig(similarity_threshold=0.01)
    repo = OpportunityClusteringRepository(db_session)
    engine = OpportunityClusteringEngine(config)
    service = OpportunityAnalysisService(db_session, repo, engine, config)

    result = service.analyze_and_cluster(dry_run=True)
    assert result.analyzed_signals_count == 1
    assert result.created_opportunities_count == 1
    assert result.linked_signals_count == 1

    # Verify DB has NOT changed
    db_session.expire_all()
    opps = db_session.query(Opportunity).all()
    assert len(opps) == 0
    links = db_session.query(OpportunitySignal).all()
    assert len(links) == 0
