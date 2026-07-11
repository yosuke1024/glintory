from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.enums import (
    Confidence,
    EvidenceRelationType,
    OpportunityStatus,
    SignalRole,
    SignalType,
)
from glintory.domain.models import (
    AnalysisRun,
    Base,
    Opportunity,
    OpportunitySignal,
    ScoringRun,
    Signal,
    Source,
)
from glintory.services.content_hashing import calculate_opportunity_content_hash
from glintory.services.contract_validation import (
    inspect_jurypress_feed,
    validate_public_contract,
)
from glintory.services.opportunity_rebuild_service import OpportunityRebuildService
from glintory.services.static_publishing import build_static_site


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


def test_calculate_content_hash_deterministic():
    # Setup dummy opportunity
    opp = Opportunity(
        id="opp-1",
        title="Test Opportunity",
        public_id="opp_12345",
        public_revision=1
    )
    evidences = [
        {
            "signal_id": "sig-1",
            "role": "demand",
            "title": "Sig Title 1",
            "url": "https://example.com/1",
            "published_at": None,
            "relevance_score": 0.9,
            "summary_ja": "要約1",
            "summary_en": "Summary 1",
            "excerpt": "A" * 600  # Will be limited to 500 chars in hashing
        },
        {
            "signal_id": "sig-2",
            "role": "pain",
            "title": "Sig Title 2",
            "url": "https://example.com/2",
            "published_at": None,
            "relevance_score": 0.95,
            "summary_ja": "要約2",
            "summary_en": "Summary 2",
            "excerpt": "B" * 10
        }
    ]

    h1 = calculate_opportunity_content_hash(opp, evidences)
    h2 = calculate_opportunity_content_hash(opp, evidences)
    assert h1 == h2

    # Verify stable sorting (order mismatch in input list should yield same hash)
    h3 = calculate_opportunity_content_hash(opp, list(reversed(evidences)))
    assert h1 == h3


def test_rebuild_v2_non_destructive(memory_db):
    session = memory_db
    now = datetime.now(UTC)

    # 1. Create Source & Signal
    src = Source(id="src-1", name="HN", source_type="hackernews", enabled=True)
    session.add(src)
    session.commit()

    sig1 = Signal(
        id="sig-1", source_id="src-1", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative slow MVP.",
        excerpt="Workaround present. Pain is high.", canonical_url="https://example.com/1",
        content_hash="h1", freshness_score=1.0, source_quality_score=1.0, collected_at=now
    )
    session.add(sig1)
    session.commit()

    # 2. Rebuild Service execution (from v1 to v2)
    service = OpportunityRebuildService(session)
    res = service.rebuild_v2("v1", "v2")

    assert res["rebuild_status"] == "success"
    assert res["created_v2_opportunities"] == 1

    # Verify AnalysisRun and ScoringRun log entries
    an_run = session.query(AnalysisRun).order_by(AnalysisRun.started_at.desc()).first()
    assert an_run is not None
    assert an_run.status == "succeeded"
    assert an_run.submitted_signal_count == 1

    sc_run = session.query(ScoringRun).order_by(ScoringRun.started_at.desc()).first()
    assert sc_run is not None
    assert sc_run.status == "succeeded"


def test_static_publishing_v1_contract_flow(memory_db, tmp_path):
    session = memory_db
    now = datetime.now(UTC)
    output_dir = tmp_path / "static_build"

    # Setup opportunity and components
    src = Source(id="src-1", name="GitHub", source_type="github", enabled=True)
    session.add(src)

    opp = Opportunity(
        id="opp-1",
        public_id="opp_test123",
        public_revision=1,
        title="Test Opportunity",
        title_ja="テスト案件",
        summary_ja="テスト要約",
        problem_ja="課題",
        target_user_ja="対象",
        current_workaround_ja="回避策",
        existing_solution_gap_ja="ギャップ",
        mvp_direction_ja="MVP",
        why_selected_ja="理由",
        risks_ja="リスク",
        title_en="Test Title EN",
        summary_en="Test Summary EN",
        problem_en="Problem EN",
        target_user_en="Target EN",
        current_workaround_en="Workaround EN",
        existing_solution_gap_en="Gap EN",
        mvp_direction_en="MVP EN",
        why_selected_en="Why EN",
        risks_en="Risks EN",
        total_score=85,
        confidence=Confidence.HIGH,
        independent_evidence_count=2,
        demand_evidence_count=1,
        source_type_count=1,
        source_domain_count=1,
        status=OpportunityStatus.INBOX,
        current_scoring_version="v2",
        gate_status="passed",
        enrichment_status="completed",
        translation_status="completed",
        enriched_at=now,
        evidence_updated_at=now,
    )
    session.add(opp)
    session.commit()

    sig = Signal(
        id="sig-1", source_id="src-1", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative slow MVP.",
        excerpt="Workaround present. Pain is high.", canonical_url="https://example.com/1",
        content_hash="h1", freshness_score=1.0, source_quality_score=1.0, collected_at=now
    )
    session.add(sig)
    session.commit()

    opp_sig = OpportunitySignal(
        opportunity_id="opp-1", signal_id="sig-1", relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0, association_source="clustering", is_excluded=False,
        evidence_summary_ja="テスト証拠要約"
    )
    session.add(opp_sig)
    session.commit()

    # Trigger publishing
    res = build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        base_path="/dist",
        generated_at=now
    )

    assert res["opportunities_generated"] > 0

    # 1. Verify JSON Feeds presence
    data_v1_dir = output_dir / "data" / "v1"
    assert (data_v1_dir / "manifest.json").exists()
    assert (data_v1_dir / "opportunities.json").exists()
    assert (data_v1_dir / "feeds" / "jurypress.json").exists()
    assert (data_v1_dir / "opportunities" / "opp_test123.json").exists()

    # 2. Verify JSON Schemas presence
    assert (data_v1_dir / "schemas" / "manifest.schema.json").exists()
    assert (data_v1_dir / "schemas" / "opportunity-list.schema.json").exists()
    assert (data_v1_dir / "schemas" / "opportunity-detail.schema.json").exists()
    assert (data_v1_dir / "schemas" / "jurypress-feed.schema.json").exists()

    # 3. Verify HTML details use public_id
    assert (output_dir / "opportunities" / "opp_test123" / "index.html").exists()

    # 4. Verify Redirect pages
    assert (output_dir / "opportunities" / "opp-1" / "index.html").exists()

    # 5. Verify Sitemap content
    sitemap = (output_dir / "sitemap.xml").read_text()
    assert "/opportunities/opp_test123/" in sitemap
    assert "/opportunities/opp_test123/en/" in sitemap

    # 6. Verify CLI contract validation
    val_errors = validate_public_contract(str(data_v1_dir))
    assert len(val_errors) == 0

    # 7. Verify JuryPress feed inspection CLI output helper
    inspect_res = inspect_jurypress_feed(str(data_v1_dir))
    assert len(inspect_res["ready"]) == 1
    assert inspect_res["ready"][0]["public_id"] == "opp_test123"
