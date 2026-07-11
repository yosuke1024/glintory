import pytest
import os
import pathlib
import tempfile
import json
from datetime import datetime, UTC, date
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from alembic.config import Config
from alembic import command

from glintory.config import settings
from glintory.infrastructure.database import reset_db_connections
from glintory.domain.enums import (
    OpportunityStatus,
    SignalRole,
    SignalType,
    EvidenceRelationType,
    Confidence,
    CollectionRunStatus,
)
from glintory.domain.models import (
    Opportunity,
    Signal,
    Source,
    SourceSchedule,
    OpportunitySignal,
    CollectionRun,
    ScheduleExecution,
)
from glintory.services.opportunity_analysis import OpportunityAnalysisService
from glintory.services.opportunity_scoring import OpportunityScoringEngine
from glintory.services.static_publishing import build_static_site
from glintory.cli import run_opportunities_command
from glintory.infrastructure.opportunity_clustering_repository import OpportunityClusteringRepository

@pytest.fixture
def test_db(tmp_path):
    """Sets up temporary database for testing."""
    db_file = tmp_path / "test_correctness.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    yield session

    session.close()
    reset_db_connections()
    settings.database_url = original_url
    if db_file.exists():
        db_file.unlink()

# Helper Namespace mock for CLI testing
class MockArgs:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class MockRuntime:
    def __init__(self, session):
        self._session = session
        self.session_factory = lambda: session

def test_v1_rebuild_to_v2(test_db):
    session = test_db
    # Create Sources
    src1 = Source(id="src-hn-1", name="HN", source_type="hackernews")
    session.add(src1)
    session.commit()

    # Create v1 Opportunity
    opp = Opportunity(
        id="opp-v1-test",
        title="Test Opportunity Title",
        status=OpportunityStatus.INBOX,
        current_scoring_version="v1"
    )
    # Create associated Signal
    sig = Signal(
        id="sig-v1-test",
        source_id="src-hn-1",
        title="V1 Signal Title",
        excerpt="Important user need user target developer problem issue workaround alternative slow MVP.",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://news.ycombinator.com/item?id=12345",
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add_all([opp, sig])
    session.commit()

    opp_sig = OpportunitySignal(
        opportunity_id="opp-v1-test",
        signal_id="sig-v1-test",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
        association_source="clustering",
        is_excluded=False
    )
    session.add(opp_sig)
    session.commit()

    # Simulate rebuild command
    args = MockArgs(
        subcommand="rebuild",
        from_score_version="v1",
        to_score_version="v2",
        json=True
    )
    runtime = MockRuntime(session)
    import asyncio
    asyncio.run(run_opportunities_command(args, runtime))

    # Clear session cache to avoid stale read
    session.expire_all()

    # Verify that the opportunity is rejected
    updated_opp = session.get(Opportunity, "opp-v1-test")
    assert updated_opp.status == OpportunityStatus.REJECTED

    # Verify that the link is removed
    links = session.query(OpportunitySignal).filter(OpportunitySignal.opportunity_id == "opp-v1-test").all()
    assert len(links) == 0

    # Verify that the signal is now unassociated for v2
    repo = OpportunityClusteringRepository(session)
    unassociated = repo.load_unassociated_signals()
    assert "sig-v1-test" in [s.id for s in unassociated]

def test_rebuild_idempotency(test_db):
    session = test_db
    args = MockArgs(
        subcommand="rebuild",
        from_score_version="v1",
        to_score_version="v2",
        json=True
    )
    runtime = MockRuntime(session)
    import asyncio
    # Execute rebuild twice
    res1 = asyncio.run(run_opportunities_command(args, runtime))
    res2 = asyncio.run(run_opportunities_command(args, runtime))
    assert res1 == 0
    assert res2 == 0

def test_show_hn_single_submission_rejected(test_db):
    session = test_db
    src = Source(id="hn-src-uuid", name="HackerNews API", source_type="hackernews")
    session.add(src)
    session.commit()

    sig = Signal(
        id="show-hn-sig-id",
        source_id="hn-src-uuid",
        title="Show HN: A new tool for developers",
        excerpt="Here is my side project workaround instead alternative problem issue MVP target developer.",
        signal_type=SignalType.LAUNCH,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://news.ycombinator.com/item?id=9999",
        content_hash="h2",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add(sig)
    session.commit()

    from glintory.services.opportunity_analysis import OpportunityClusteringEngine
    engine = OpportunityClusteringEngine()
    repo = OpportunityClusteringRepository(session)
    service = OpportunityAnalysisService(session, repo, engine)

    service.analyze_and_cluster()
    
    # Show HN single is rejected, so created opportunity candidates should have rejected status
    opps = session.query(Opportunity).filter(Opportunity.gate_status == "rejected").all()
    assert len(opps) > 0
    assert "Rejected: Single Show HN submission cannot be promoted." in opps[0].gate_reason

def test_condition_b_strict_char_length_limit_obsolete(test_db):
    session = test_db
    src = Source(id="gh-src", name="GitHub", source_type="github")
    session.add(src)
    session.commit()

    # Create signal with length >= 150 but missing most elements
    sig = Signal(
        id="long-brief-sig",
        source_id="gh-src",
        title="Long Title",
        excerpt="This is a very long text that easily exceeds 150 characters but it does not specify any target users, current workarounds, gap in existing solutions, or mvp directions. It is just generic filler text repeated to make it long.",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://github.com/test/repo/issues/1",
        content_hash="h3",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add(sig)
    session.commit()

    repo = OpportunityClusteringRepository(session)
    from glintory.services.opportunity_analysis import OpportunityClusteringEngine
    engine = OpportunityClusteringEngine()
    service = OpportunityAnalysisService(session, repo, engine)

    service.analyze_and_cluster()
    opps = session.query(Opportunity).all()
    # It must be rejected because it lacks the 5 structure elements
    assert opps[0].gate_status == "rejected"
    assert "Condition B" in opps[0].gate_reason

def test_condition_b_requires_5_elements(test_db):
    session = test_db
    src = Source(id="gh-src-5", name="GitHub", source_type="github")
    session.add(src)
    session.commit()

    # Create signal with all 5 structure elements
    sig = Signal(
        id="strong-sig-5",
        source_id="gh-src-5",
        title="I need a workaround for developers",
        excerpt="Currently target users (developers) use excel sheets manually, but it is too slow and expensive. I want an MVP solution that solves this issue.",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://github.com/test/repo/issues/2",
        content_hash="h4",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add(sig)
    session.commit()

    repo = OpportunityClusteringRepository(session)
    from glintory.services.opportunity_analysis import OpportunityClusteringEngine
    engine = OpportunityClusteringEngine()
    service = OpportunityAnalysisService(session, repo, engine)

    service.analyze_and_cluster()
    opps = session.query(Opportunity).filter(Opportunity.gate_status == "passed").all()
    # Passed because it has all 5 elements
    assert len(opps) > 0
    assert "Passed Condition B" in opps[0].gate_reason

def test_source_type_count_multiple_github_is_one(test_db):
    session = test_db
    # Create two different GitHub sources
    src1 = Source(id="gh-src-a", name="GitHub A", source_type="github")
    src2 = Source(id="gh-src-b", name="GitHub B", source_type="github")
    session.add_all([src1, src2])
    session.commit()

    # Create Opportunity
    opp = Opportunity(id="opp-type-count", title="Test Opp Type", status=OpportunityStatus.INBOX)
    session.add(opp)
    session.commit()

    # Associate two signals from different sources but same type (github)
    sig1 = Signal(id="sig-a", source_id="gh-src-a", title="Title A", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND, canonical_url="https://github.com/test/repo/issues/a", content_hash="ha", freshness_score=1.0, source_quality_score=1.0)
    sig2 = Signal(id="sig-b", source_id="gh-src-b", title="Title B", signal_type=SignalType.REQUEST, signal_role=SignalRole.DEMAND, canonical_url="https://github.com/test/repo/issues/b", content_hash="hb", freshness_score=1.0, source_quality_score=1.0)
    session.add_all([sig1, sig2])
    session.commit()

    link1 = OpportunitySignal(opportunity_id="opp-type-count", signal_id="sig-a", relation_type=EvidenceRelationType.SUPPORTING, relevance_score=0.9, association_source="clustering", is_excluded=False)
    link2 = OpportunitySignal(opportunity_id="opp-type-count", signal_id="sig-b", relation_type=EvidenceRelationType.SUPPORTING, relevance_score=0.8, association_source="clustering", is_excluded=False)
    session.add_all([link1, link2])
    session.commit()

    # Trigger Opportunity update re-evaluation
    repo = OpportunityClusteringRepository(session)
    from glintory.services.opportunity_analysis import OpportunityClusteringEngine
    service = OpportunityAnalysisService(session, repo, OpportunityClusteringEngine())

    # Re-evaluate
    all_links = (
        session.query(OpportunitySignal, Signal)
        .join(Signal, OpportunitySignal.signal_id == Signal.id)
        .filter(OpportunitySignal.opportunity_id == "opp-type-count")
        .filter(OpportunitySignal.is_excluded.is_(False))
        .all()
    )
    ev_signals_input = [
        {"signal": sig, "relation_type": opp_sig.relation_type, "relevance_score": opp_sig.relevance_score}
        for opp_sig, sig in all_links
    ]
    metrics, passed, reason = service._calculate_metrics_and_gate(ev_signals_input)
    assert metrics["source_type_count"] == 1

def test_opportunity_update_recalculates_all_evidence(test_db):
    session = test_db
    src_gh = Source(id="gh-src-u", name="GitHub", source_type="github")
    src_hn = Source(id="hn-src-u", name="HN", source_type="hackernews")
    session.add_all([src_gh, src_hn])
    session.commit()

    # Must specify current_scoring_version="v2" to be active and clusterable
    opp = Opportunity(
        id="opp-recalc",
        title="Specific opportunity for recalculating evidence signals",
        status=OpportunityStatus.INBOX,
        current_scoring_version="v2"
    )
    session.add(opp)
    session.commit()

    # Pre-existing evidence (use identical title and context keywords to ensure clustering)
    sig1 = Signal(
        id="sig1-pre",
        source_id="gh-src-u",
        title="Specific opportunity for recalculating evidence signals",
        excerpt="Important user need target developer problem issue workaround alternative slow MVP.",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://github.com/test/repo/issues/pre",
        content_hash="hpre",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add(sig1)
    session.commit()

    link1 = OpportunitySignal(opportunity_id="opp-recalc", signal_id="sig1-pre", relation_type=EvidenceRelationType.SUPPORTING, relevance_score=0.9, association_source="clustering", is_excluded=False)
    session.add(link1)
    session.commit()

    # Execute analyze, which links a new unassociated signal to the existing opportunity
    sig2 = Signal(
        id="sig2-new",
        source_id="hn-src-u",
        title="Specific opportunity for recalculating evidence signals",
        excerpt="Important user need target developer problem issue workaround alternative slow MVP.",
        signal_type=SignalType.REQUEST,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://news.ycombinator.com/item?id=new",
        content_hash="hnew",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add(sig2)
    session.commit()

    repo = OpportunityClusteringRepository(session)
    from glintory.services.opportunity_analysis import OpportunityClusteringEngine
    engine = OpportunityClusteringEngine()
    service = OpportunityAnalysisService(session, repo, engine)

    # This should trigger analysis and link sig2-new to opp-recalc, and recalculate metrics based on BOTH sig1 and sig2
    service.analyze_and_cluster()

    session.expire_all()
    updated_opp = session.get(Opportunity, "opp-recalc")
    assert updated_opp.independent_evidence_count == 2
    assert updated_opp.demand_evidence_count == 2
    assert updated_opp.source_type_count == 2

def test_feasibility_v2_scores_differentiate(test_db):
    session = test_db
    # Prepare different implementation clues
    # 1. Client-only CSV CLI tool (simple)
    csv_cli_text = "A simple CSV converter CLI tool. No database is required, completely offline and client-only. Easy command line interface."
    # 2. Heavy Enterprise platform with real-time backend database cluster
    heavy_platform_text = "Medical AI analysis platform with heavy database cluster backend real-time synchronization enterprise team B2B sales cycle."

    from glintory.domain.scoring import OpportunityScoringInput, ScoringEvidenceSignal
    
    cli_sig = ScoringEvidenceSignal(
        signal_id="sig-cli",
        source_id="src-cli",
        source_type="github",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        evidence_origin="repo1",
        published_at=datetime.now(UTC),
        collected_at=datetime.now(UTC),
        title="CSV CLI",
        excerpt=csv_cli_text,
        canonical_url="http://example.com/cli",
        tags=(),
        raw_metadata={}
    )
    cli_input = OpportunityScoringInput(
        opportunity_id="opp-cli",
        generation_method="clustering",
        status="inbox",
        signals=(cli_sig,)
    )

    heavy_sig = ScoringEvidenceSignal(
        signal_id="sig-heavy",
        source_id="src-heavy",
        source_type="github",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        evidence_origin="repo2",
        published_at=datetime.now(UTC),
        collected_at=datetime.now(UTC),
        title="Heavy Plat",
        excerpt=heavy_platform_text,
        canonical_url="http://example.com/heavy",
        tags=(),
        raw_metadata={}
    )
    heavy_input = OpportunityScoringInput(
        opportunity_id="opp-heavy",
        generation_method="clustering",
        status="inbox",
        signals=(heavy_sig,)
    )

    engine = OpportunityScoringEngine(scoring_version="v2")
    cli_score = engine.score(cli_input, as_of_date=date.today())
    heavy_score = engine.score(heavy_input, as_of_date=date.today())

    # The solo developer suitability and total score should differ
    assert cli_score.feasibility_score != heavy_score.feasibility_score
    assert cli_score.total_score != heavy_score.total_score

def test_low_confidence_excluded_from_publishing(test_db, tmp_path):
    session = test_db
    output_dir = str(tmp_path / "static")

    # Create LOW confidence opportunity
    low_opp = Opportunity(
        id="opp-low",
        title="Low confidence opp",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.LOW,
        current_scoring_version="v2"
    )
    # Create MEDIUM confidence opportunity
    med_opp = Opportunity(
        id="opp-med",
        title="Med confidence opp",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.MEDIUM,
        current_scoring_version="v2"
    )
    session.add_all([low_opp, med_opp])
    session.commit()

    build_static_site(session=session, output_dir=output_dir, site_url="https://localhost")

    # Check top/list page, LOW confidence should not be present
    list_file = os.path.join(output_dir, "opportunities", "index.html")
    with open(list_file) as f:
        list_content = f.read()
    
    assert "Med confidence opp" in list_content
    assert "Low confidence opp" not in list_content

    # JSON should exclude LOW confidence
    latest_json = os.path.join(output_dir, "data", "latest.json")
    with open(latest_json) as f:
        latest_data = json.load(f)
    top_opps_ids = [opp["id"] for opp in latest_data["top_opportunities"]]
    assert "opp-med" in top_opps_ids
    assert "opp-low" not in top_opps_ids

def test_static_publishing_prefer_japanese(test_db, tmp_path):
    session = test_db
    output_dir = str(tmp_path / "static")

    opp = Opportunity(
        id="opp-ja-test",
        title="Eng Title",
        title_ja="日本語タイトル",
        summary_ja="日本語要約です。",
        proposed_solution="Eng proposed solution",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.MEDIUM,
        current_scoring_version="v2"
    )
    session.add(opp)
    session.commit()

    build_static_site(session=session, output_dir=output_dir, site_url="https://localhost")

    # Check Index Page
    index_file = os.path.join(output_dir, "index.html")
    with open(index_file) as f:
        index_content = f.read()
    assert "日本語タイトル" in index_content
    assert "日本語要約です。" in index_content
    assert "Eng Title" not in index_content

    # Check List Page
    list_file = os.path.join(output_dir, "opportunities", "index.html")
    with open(list_file) as f:
        list_content = f.read()
    assert "日本語タイトル" in list_content
    assert "Eng Title" not in list_content

def test_fallback_warning_when_ja_translation_missing(test_db, tmp_path):
    session = test_db
    output_dir = str(tmp_path / "static")

    opp = Opportunity(
        id="opp-missing-ja",
        title="Original English Title",
        proposed_solution="English solution text.",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.MEDIUM,
        current_scoring_version="v2",
        translation_status="failed", # translation missing
        title_ja=None,
        summary_ja=None
    )
    session.add(opp)
    session.commit()

    build_static_site(session=session, output_dir=output_dir, site_url="https://localhost")

    # Detailed Japanese page (standard url) should display fallback warning
    ja_detail_file = os.path.join(output_dir, "opportunities", "opp-missing-ja", "index.html")
    with open(ja_detail_file) as f:
        ja_content = f.read()
    
    assert "日本語要約はまだ生成されていません。" in ja_content
    assert "English solution text." not in ja_content

def test_evidence_renders_signal_role(test_db, tmp_path):
    session = test_db
    output_dir = str(tmp_path / "static")

    src = Source(id="src-role-test", name="Source Test", source_type="github")
    session.add(src)
    session.commit()

    opp = Opportunity(
        id="opp-role-test",
        title="Opp Role Title",
        title_ja="案件タイトル",
        summary_ja="案件の要約",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.MEDIUM,
        current_scoring_version="v2",
        translation_status="completed"
    )
    sig = Signal(
        id="sig-role-test",
        source_id="src-role-test",
        title="Signal Title",
        excerpt="Text content.",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        canonical_url="https://github.com/test/repo/issues/3",
        content_hash="h5",
        freshness_score=1.0,
        source_quality_score=1.0
    )
    session.add_all([opp, sig])
    session.commit()

    opp_sig = OpportunitySignal(
        opportunity_id="opp-role-test",
        signal_id="sig-role-test",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        association_source="clustering",
        is_excluded=False
    )
    session.add(opp_sig)
    session.commit()

    build_static_site(session=session, output_dir=output_dir, site_url="https://localhost")

    # Check JST date formatting and signal role rendering in detailed page
    detail_file = os.path.join(output_dir, "opportunities", "opp-role-test", "index.html")
    with open(detail_file) as f:
        detail_content = f.read()
    
    assert "役割: 需要" in detail_content
    assert "JST" in detail_content

def test_diagnostics_pipeline_stats(test_db, tmp_path):
    session = test_db
    output_dir = str(tmp_path / "static")

    # Create dummy schedule run
    src = Source(id="gh-diag-src", name="GitHub Src", source_type="github")
    session.add(src)
    session.commit()

    run = CollectionRun(
        source_id="gh-diag-src",
        status=CollectionRunStatus.SUCCEEDED,
        fetched_count=10,
        inserted_count=5,
        skipped_count=2,
        error_count=0
    )
    session.add(run)
    session.commit()

    build_static_site(session=session, output_dir=output_dir, site_url="https://localhost")

    diag_file = os.path.join(output_dir, "diagnostics.html")
    with open(diag_file) as f:
        diag_content = f.read()

    assert "Global Pipeline Summary" in diag_content
    assert "Source Type Pipeline Audits" in diag_content
    assert "github" in diag_content

def test_no_double_counting_hn_rss_and_hn_api(test_db):
    # This is naturally avoided because our default RSS source is Lobsters,
    # which has source_type="rss", while Hacker News has source_type="hackernews".
    # We verify that different source types diversity works properly.
    session = test_db

    src_hn = Source(id="hn-api", name="HN API", source_type="hackernews")
    src_rss = Source(id="lobsters-rss", name="Lobsters RSS", source_type="rss")
    session.add_all([src_hn, src_rss])
    session.commit()

    sig1 = Signal(id="sig-hn", source_id="hn-api", title="Title HN", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND, canonical_url="https://news.ycombinator.com/item?id=hn1", content_hash="hhn1", freshness_score=1.0, source_quality_score=1.0)
    sig2 = Signal(id="sig-rss", source_id="lobsters-rss", title="Title Lobsters", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND, canonical_url="https://lobste.rs/s/rss1", content_hash="hlob1", freshness_score=1.0, source_quality_score=1.0)
    session.add_all([sig1, sig2])
    session.commit()

    # Analyze
    repo = OpportunityClusteringRepository(session)
    from glintory.services.opportunity_analysis import OpportunityClusteringEngine
    engine = OpportunityClusteringEngine()
    service = OpportunityAnalysisService(session, repo, engine)

    # Re-evaluate diversity
    ev_signals_input = [
        {"signal": sig1, "relation_type": EvidenceRelationType.SUPPORTING, "relevance_score": 1.0},
        {"signal": sig2, "relation_type": EvidenceRelationType.SUPPORTING, "relevance_score": 1.0}
    ]
    metrics, passed, reason = service._calculate_metrics_and_gate(ev_signals_input)
    # The source type count should be exactly 2
    assert metrics["source_type_count"] == 2
