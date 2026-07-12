import json
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
    Base,
    Opportunity,
    OpportunitySignal,
    Signal,
    Source,
)
from glintory.services.public_contract_generator import generate_public_contract
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


def test_case_k_static_publishing(memory_db, tmp_path):
    session = memory_db
    now = datetime.now(UTC)

    # 1. Setup mock sources and signals
    src = Source(id="src-1", name="HN", source_type="hackernews", enabled=True)
    session.add(src)

    # We need 3 Research Candidates (gate_status='rejected', status=OpportunityStatus.RESEARCH)
    # 2 Rejected Opportunities (status=OpportunityStatus.REJECTED, public_lifecycle='unregistered')
    
    # 3 Research Candidates
    for i in range(1, 4):
        opp = Opportunity(
            id=f"opp-research-{i}",
            public_id=f"opp_0000000000000000000000000000000{i}",
            public_revision=1,
            title=f"Research Opportunity {i}",
            title_ja=f"リサーチ案件 {i}",
            summary_ja=f"リサーチ要約 {i}",
            problem_ja="課題",
            target_user_ja="対象",
            current_workaround_ja="回避策",
            existing_solution_gap_ja="ギャップ",
            mvp_direction_ja="MVP",
            why_selected_ja="理由",
            risks_ja="リスク",
            title_en=f"Research Title {i} EN",
            summary_en=f"Research Summary {i} EN",
            problem_en="Problem EN",
            target_user_en="Target EN",
            current_workaround_en="Workaround EN",
            existing_solution_gap_en="Gap EN",
            mvp_direction_en="MVP EN",
            why_selected_en="Why EN",
            risks_en="Risks EN",
            total_score=50,
            confidence=Confidence.HIGH,
            independent_evidence_count=1,
            demand_evidence_count=1,
            source_type_count=1,
            source_domain_count=1,
            status=OpportunityStatus.RESEARCH,
            current_scoring_version="v2",
            gate_status="rejected",
            gate_reason="Research Candidate: gate_v3 rejection but valuable.",
            enrichment_status="completed",
            translation_status="completed",
            enriched_at=now,
            evidence_updated_at=now,
            public_lifecycle="active",  # active so it is published
        )
        session.add(opp)

        sig = Signal(
            id=f"sig-research-{i}",
            source_id="src-1",
            title=f"Evidence Title {i}",
            excerpt="Excerpt text",
            canonical_url=f"https://example.com/research/{i}",
            signal_role=SignalRole.DEMAND,
            signal_type=SignalType.PAIN,
            collected_at=now,
            content_hash=f"hash-{i}",
            freshness_score=1.0,
            source_quality_score=1.0,
        )
        session.add(sig)

        opp_sig = OpportunitySignal(
            opportunity_id=opp.id,
            signal_id=sig.id,
            relation_type=EvidenceRelationType.SUPPORTING,
            relevance_score=0.9,
            association_source="manual",
            is_excluded=False,
            updated_at=now,
        )
        session.add(opp_sig)

    # 2 Rejected Opportunities
    for i in range(1, 3):
        opp = Opportunity(
            id=f"opp-rejected-{i}",
            public_id=f"opp_f000000000000000000000000000000{i}",
            public_revision=1,
            title=f"Rejected Opportunity {i}",
            title_ja=f"却下された案件 {i}",
            summary_ja=f"要約 {i}",
            total_score=20,
            confidence=Confidence.LOW,
            independent_evidence_count=0,
            demand_evidence_count=0,
            source_type_count=1,
            source_domain_count=1,
            status=OpportunityStatus.REJECTED,
            current_scoring_version="v2",
            gate_status="rejected",
            gate_reason="Rejected: No evidence.",
            public_lifecycle="unregistered",  # unregistered so it is NOT published
        )
        session.add(opp)

    session.commit()

    # Create directories for static publishing
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    # 2. Build static site
    # This also generates HTML templates
    build_static_site(
        session=session,
        output_dir=str(dist_dir),
        base_path="/glintory",
        site_url="https://example.com/glintory",
    )

    # 3. Generate Public Data Contract
    _ = generate_public_contract(
        session=session,
        temp_build_dir=str(dist_dir),
        base_path="/glintory",
        site_url="https://example.com/glintory",
        gen_time=now,
    )

    # 4. Assertions for E2E validation

    # A. Check manifest.json contents
    manifest_path = dist_dir / "data" / "v1" / "manifest.json"
    assert manifest_path.exists()
    with open(manifest_path) as f:
        manifest = json.load(f)

    assert manifest["schema_version"] == "1.1.0"
    assert manifest["counts"]["published_opportunities"] == 0
    assert manifest["counts"]["research_candidates"] == 3
    assert manifest["counts"]["jurypress_ready"] == 0

    # B. Check opportunities.json (list) contents
    list_path = dist_dir / "data" / "v1" / "opportunities.json"
    assert list_path.exists()
    with open(list_path) as f:
        opp_list = json.load(f)

    assert opp_list["schema_version"] == "1.1.0"
    assert opp_list["count"] == 3
    for item in opp_list["items"]:
        assert item["stage"] == "research"
        assert item["public_lifecycle"] == "active"
        # Make sure rejected ones are NOT in the list
        assert not item["public_id"].startswith("opp_f000")

    # C. Check detail JSONs and Evidence URLs
    for i in range(1, 4):
        pub_id = f"opp_0000000000000000000000000000000{i}"
        detail_path = dist_dir / "data" / "v1" / "opportunities" / f"{pub_id}.json"
        assert detail_path.exists()
        with open(detail_path) as f:
            detail = json.load(f)
        assert detail["stage"] == "research"
        assert detail["gate"]["version"] == "v3"
        # Check evidence URL in contract
        assert len(detail["evidence"]) == 1
        assert detail["evidence"][0]["url"] == f"https://example.com/research/{i}"

    # Verify rejected ones do not have detail JSONs generated
    for i in range(1, 3):
        pub_id = f"opp_f000000000000000000000000000000{i}"
        detail_path = dist_dir / "data" / "v1" / "opportunities" / f"{pub_id}.json"
        assert not detail_path.exists()

    # D. Check JuryPress Feed is empty (ready count 0)
    feed_path = dist_dir / "data" / "v1" / "feeds" / "jurypress.json"
    assert feed_path.exists()
    with open(feed_path) as f:
        feed = json.load(f)
    assert feed["count"] == 0
    assert len(feed["items"]) == 0

    # E. Check HTML generation and UI Dashboard / tabs
    opp_index_html_path = dist_dir / "opportunities" / "index.html"
    assert opp_index_html_path.exists()
    opp_index_content = opp_index_html_path.read_text(encoding="utf-8")

    # Opportunities page should show Research Candidate count (3)
    assert "Research Candidates" in opp_index_content
    # Opportunities page tab should contain the research count or status
    assert "research" in opp_index_content

    # Home Dashboard should show Top Opportunities (Research Candidates are active and shown)
    index_html_path = dist_dir / "index.html"
    assert index_html_path.exists()
    index_content = index_html_path.read_text(encoding="utf-8")
    assert "リサーチ案件 1" in index_content

    # Check detail HTML files exist
    for i in range(1, 4):
        pub_id = f"opp_0000000000000000000000000000000{i}"
        detail_html = dist_dir / "opportunities" / pub_id / "index.html"
        assert detail_html.exists()
        detail_content = detail_html.read_text(encoding="utf-8")
        assert f"リサーチ案件 {i}" in detail_content
        assert f"https://example.com/research/{i}" in detail_content
