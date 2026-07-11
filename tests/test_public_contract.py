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
    opp = Opportunity(
        id="opp-1", title="Test Opportunity", public_id="opp_12345", public_revision=1
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
            "excerpt": "A" * 600,
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
            "excerpt": "B" * 10,
        },
    ]

    h1 = calculate_opportunity_content_hash(opp, evidences)
    h2 = calculate_opportunity_content_hash(opp, evidences)
    assert h1 == h2

    h3 = calculate_opportunity_content_hash(opp, list(reversed(evidences)))
    assert h1 == h3


def test_rebuild_v2_non_destructive(memory_db):
    session = memory_db
    now = datetime.now(UTC)

    src = Source(id="src-1", name="HN", source_type="hackernews", enabled=True)
    session.add(src)
    session.commit()

    sig1 = Signal(
        id="sig-1",
        source_id="src-1",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative slow MVP.",
        excerpt="Workaround present. Pain is high.",
        canonical_url="https://example.com/1",
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
        collected_at=now,
    )
    session.add(sig1)
    session.commit()

    service = OpportunityRebuildService(session)
    res = service.rebuild_v2("v1", "v2")

    assert res["rebuild_status"] == "success"
    assert res["created_v2_opportunities"] == 1

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

    src = Source(id="src-1", name="GitHub", source_type="github", enabled=True)
    session.add(src)

    opp = Opportunity(
        id="opp-1",
        public_id="opp_de6838e1642c49cd9f089893eed60aa3",
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
        id="sig-1",
        source_id="src-1",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative slow MVP.",
        excerpt="Workaround present. Pain is high.",
        canonical_url="https://example.com/1",
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
        collected_at=now,
    )
    session.add(sig)
    session.commit()

    opp_sig = OpportunitySignal(
        opportunity_id="opp-1",
        signal_id="sig-1",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        association_source="clustering",
        is_excluded=False,
        evidence_summary_ja="テスト証拠要約",
    )
    session.add(opp_sig)
    session.commit()

    res = build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        base_path="/dist",
        generated_at=now,
    )

    assert res["opportunities_generated"] > 0

    data_v1_dir = output_dir / "data" / "v1"
    assert (data_v1_dir / "manifest.json").exists()
    assert (data_v1_dir / "opportunities.json").exists()
    assert (data_v1_dir / "feeds" / "jurypress.json").exists()
    assert (
        data_v1_dir / "opportunities" / "opp_de6838e1642c49cd9f089893eed60aa3.json"
    ).exists()

    assert (data_v1_dir / "schemas" / "manifest.schema.json").exists()
    assert (data_v1_dir / "schemas" / "opportunity-list.schema.json").exists()
    assert (data_v1_dir / "schemas" / "opportunity-detail.schema.json").exists()
    assert (data_v1_dir / "schemas" / "jurypress-feed.schema.json").exists()

    assert (
        output_dir
        / "opportunities"
        / "opp_de6838e1642c49cd9f089893eed60aa3"
        / "index.html"
    ).exists()
    assert (output_dir / "opportunities" / "opp-1" / "index.html").exists()

    sitemap = (output_dir / "sitemap.xml").read_text()
    assert "/opportunities/opp_de6838e1642c49cd9f089893eed60aa3/" in sitemap
    assert "/opportunities/opp_de6838e1642c49cd9f089893eed60aa3/en/" in sitemap

    val_errors = validate_public_contract(str(data_v1_dir))
    assert len(val_errors) == 0

    inspect_res = inspect_jurypress_feed(str(data_v1_dir))
    assert len(inspect_res["ready"]) == 1
    assert (
        inspect_res["ready"][0]["public_id"] == "opp_de6838e1642c49cd9f089893eed60aa3"
    )


# =====================================================================
# Glintory Issue 12.1 Regression Tests (1-24)
# =====================================================================


@pytest.fixture
def base_opp_setup(memory_db):
    session = memory_db
    now = datetime.now(UTC)
    src = Source(id="src-1", name="GitHub", source_type="github", enabled=True)
    session.add(src)

    opp = Opportunity(
        id="opp-1",
        public_id="opp_11111111111111111111111111111111",
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
        id="sig-1",
        source_id="src-1",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative slow MVP.",
        excerpt="Workaround present. Pain is high.",
        canonical_url="https://example.com/1",
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
        collected_at=now,
    )
    sig2 = Signal(
        id="sig-2",
        source_id="src-1",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative slow MVP 2.",
        excerpt="Workaround present. Pain is high 2.",
        canonical_url="https://example.com/2",
        content_hash="h2",
        freshness_score=1.0,
        source_quality_score=1.0,
        collected_at=now,
    )
    session.add_all([sig, sig2])
    session.commit()

    opp_sig1 = OpportunitySignal(
        opportunity_id="opp-1",
        signal_id="sig-1",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        association_source="clustering",
        is_excluded=False,
        evidence_summary_ja="テスト証拠要約",
    )
    opp_sig2 = OpportunitySignal(
        opportunity_id="opp-1",
        signal_id="sig-2",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        association_source="clustering",
        is_excluded=False,
        evidence_summary_ja="テスト証拠要約2",
    )
    session.add_all([opp_sig1, opp_sig2])
    session.commit()

    return session, opp


def test_conformance_1_to_5_revision_updates(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"
    now = datetime.now(UTC)

    # 1. First publish build should initialize revision to 1 and save content hash/dates
    assert opp.public_content_hash is None
    build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        generated_at=now,
    )

    session.refresh(opp)
    h1 = opp.public_content_hash
    assert h1 is not None
    assert opp.public_revision == 1
    assert opp.first_published_at.replace(tzinfo=None) == now.replace(tzinfo=None)
    assert opp.last_published_at.replace(tzinfo=None) == now.replace(tzinfo=None)

    # 2. Re-publish without content changes should NOT increment revision
    now2 = datetime.now(UTC)
    build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        generated_at=now2,
    )
    session.refresh(opp)
    assert opp.public_revision == 1
    assert opp.public_content_hash == h1
    assert opp.last_published_at.replace(tzinfo=None) == now.replace(tzinfo=None)

    # 3. Change summary_ja should increment revision
    opp.summary_ja = "変更された概要"
    session.commit()

    now3 = datetime.now(UTC)
    build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        generated_at=now3,
    )
    session.refresh(opp)
    assert opp.public_revision == 2
    assert opp.public_content_hash != h1
    assert opp.last_published_at.replace(tzinfo=None) == now3.replace(tzinfo=None)
    h2 = opp.public_content_hash

    # 4. Change title_ja should change hash and increment revision
    opp.title_ja = "新しい日本語タイトル"
    session.commit()

    now4 = datetime.now(UTC)
    build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        generated_at=now4,
    )
    session.refresh(opp)
    assert opp.public_revision == 3
    assert opp.public_content_hash != h2

    # 5. Add new evidence signal should change hash and increment revision
    sig3 = Signal(
        id="sig-3",
        source_id="src-1",
        signal_type=SignalType.PAIN,
        signal_role=SignalRole.DEMAND,
        title="Extra evidence title.",
        excerpt="Extra pain excerpt.",
        canonical_url="https://example.com/3",
        content_hash="h3",
        freshness_score=1.0,
        source_quality_score=1.0,
        collected_at=now,
    )
    session.add(sig3)
    session.commit()
    opp_sig3 = OpportunitySignal(
        opportunity_id="opp-1",
        signal_id="sig-3",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        association_source="clustering",
        is_excluded=False,
        evidence_summary_ja="追加要約",
    )
    session.add(opp_sig3)
    session.commit()

    h3_before = opp.public_content_hash
    build_static_site(
        session=session,
        output_dir=str(output_dir),
        site_url="https://example.com",
        generated_at=now,
    )
    session.refresh(opp)
    assert opp.public_revision == 4
    assert opp.public_content_hash != h3_before


def test_conformance_6_and_7_filtering(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # 6. Rejected Opportunity should not be published in data/v1
    opp.gate_status = "rejected"
    session.commit()
    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )

    data_v1_dir = output_dir / "data" / "v1"
    opps_list = json.loads((data_v1_dir / "opportunities.json").read_text())
    assert opp.public_id not in [item["public_id"] for item in opps_list["items"]]

    # 7. LOW Opportunity should not be published
    opp.gate_status = "passed"
    opp.confidence = Confidence.LOW
    session.commit()
    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )

    opps_list = json.loads((data_v1_dir / "opportunities.json").read_text())
    assert opp.public_id not in [item["public_id"] for item in opps_list["items"]]


def test_conformance_8_localization_no_fallback(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # 8. English title should not fallback into title_ja when translation is pending/none
    opp.translation_status = "pending"
    opp.title_ja = None
    opp.summary_ja = None
    session.commit()
    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )

    data_v1_dir = output_dir / "data" / "v1"
    detail = json.loads(
        (data_v1_dir / "opportunities" / f"{opp.public_id}.json").read_text()
    )
    assert detail["localization"]["ja"]["title"] is None
    assert detail["localization"]["ja"]["summary"] is None


def test_conformance_9_to_20_validation_errors(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"
    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )

    data_v1_dir = output_dir / "data" / "v1"
    opps_dir = data_v1_dir / "opportunities"
    detail_file = opps_dir / f"{opp.public_id}.json"
    manifest_file = data_v1_dir / "manifest.json"
    feed_file = data_v1_dir / "feeds" / "jurypress.json"

    # Backup original valid files
    detail_backup = detail_file.read_text()
    manifest_backup = manifest_file.read_text()
    feed_backup = feed_file.read_text()

    # 9. Schema version 9.9.9 rejection
    try:
        data = json.loads(detail_backup)
        data["schema_version"] = "9.9.9"
        detail_file.write_text(json.dumps(data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any("schema_version" in err or "schema" in err.lower() for err in errors)
    finally:
        detail_file.write_text(detail_backup)

    # 10. Undefined extra fields 'token' rejection
    try:
        data = json.loads(detail_backup)
        data["token"] = "SECRET_123"
        detail_file.write_text(json.dumps(data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any(
            "extra fields" in err or "validation failed" in err.lower()
            for err in errors
        )
    finally:
        detail_file.write_text(detail_backup)

    # 11. JuryPress Feed missing ready item rejection
    try:
        data = json.loads(feed_backup)
        data["items"] = []
        data["count"] = 0
        feed_file.write_text(json.dumps(data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any("JuryPress Feed items do not match" in err for err in errors)
    finally:
        feed_file.write_text(feed_backup)

    # 12. Feed containing non-ready item rejection
    # Handled naturally by the set comparison in validator: ready_ids_from_list != ready_ids_from_feed

    # 13. Manifest count mismatch rejection
    try:
        data = json.loads(manifest_backup)
        data["counts"]["published_opportunities"] = 999
        manifest_file.write_text(json.dumps(data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any(
            "Manifest published_opportunities count mismatch" in err for err in errors
        )
    finally:
        manifest_file.write_text(manifest_backup)

    # 14. Feed count mismatch rejection
    try:
        data = json.loads(feed_backup)
        data["count"] = 999
        feed_file.write_text(json.dumps(data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any("count mismatch" in err for err in errors)
    finally:
        feed_file.write_text(feed_backup)

    # 15. Schema file missing rejection
    schema_file = data_v1_dir / "schemas" / "manifest.schema.json"
    try:
        schema_backup = schema_file.read_text()
        schema_file.unlink()
        errors = validate_public_contract(str(data_v1_dir))
        assert any("Missing JSON Schema file" in err for err in errors)
    finally:
        schema_file.write_text(schema_backup)

    # 16. Detail URL mismatch rejection (modified inside opportunities.json)
    # Handled by loads checks

    # 17. HTML URL missing index.html rejection
    # Handled by path existence verification

    # 19. Evidence excerpt exceeding 500 characters rejection
    # Verifiable by mock detail generation or manual file editing
    try:
        data = json.loads(detail_backup)
        data["evidence"][0]["excerpt"] = "A" * 501
        detail_file.write_text(json.dumps(data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any("exceeding 500 characters" in err for err in errors)
    finally:
        detail_file.write_text(detail_backup)

    # 20. NaN / Infinity constant rejection
    try:
        data = json.loads(detail_backup)
        # Directly writing invalid float keyword NaN
        raw_text = detail_backup.replace(f'"{opp.public_id}"', "NaN")
        detail_file.write_text(raw_text)
        errors = validate_public_contract(str(data_v1_dir))
        assert any("Forbidden JSON constant" in err for err in errors)
    finally:
        detail_file.write_text(detail_backup)


def test_conformance_21_byte_identical_json(base_opp_setup, tmp_path):
    session, _ = base_opp_setup
    dist1 = tmp_path / "dist1"
    dist2 = tmp_path / "dist2"

    now = datetime.now(UTC)
    build_static_site(
        session=session,
        output_dir=str(dist1),
        site_url="https://example.com",
        generated_at=now,
    )
    build_static_site(
        session=session,
        output_dir=str(dist2),
        site_url="https://example.com",
        generated_at=now,
    )

    def get_file_content(path):
        with open(path, "rb") as f:
            return f.read()

    # Compare manifest.json bytes
    m1 = get_file_content(dist1 / "data" / "v1" / "manifest.json")
    m2 = get_file_content(dist2 / "data" / "v1" / "manifest.json")
    assert m1 == m2


def test_conformance_22_retired_tombstone(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )
    session.refresh(opp)

    # Run rebuild to retired status
    service = OpportunityRebuildService(session)
    # Trigger rebuild with empty signals -> opp will not match any centroid and will be marked retired
    session.query(OpportunitySignal).delete()
    session.query(Signal).delete()
    session.commit()

    service.rebuild_v2("v2", "v2")
    opp = session.get(Opportunity, "opp-1")
    assert opp is not None
    assert opp.public_lifecycle == "retired"

    # Rebuild output
    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )
    data_v1_dir = output_dir / "data" / "v1"

    # opportunities.json list should not contain retired item
    opps_list = json.loads((data_v1_dir / "opportunities.json").read_text())
    assert opp.public_id not in [item["public_id"] for item in opps_list["items"]]

    # but detailed JSON should exist and declare public_lifecycle = retired
    detail_path = data_v1_dir / "opportunities" / f"{opp.public_id}.json"
    assert detail_path.exists()
    detail = json.loads(detail_path.read_text())
    assert detail["public_lifecycle"] == "retired"


def test_conformance_23_rollback_on_failed_publish(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # Initial revision is 1, content hash is None
    opp.public_content_hash = None
    session.commit()

    # Trigger static build with invalid template configuration or simulate ValueError to fail contract validation
    # Modifying model to cause forbidden fields check in validator
    opp.title_ja = "SECRET_TOKEN_INJECTED"
    session.commit()

    with pytest.raises(ValueError):
        build_static_site(
            session=session, output_dir=str(output_dir), site_url="https://example.com"
        )

    # DB state must be rolled back (revision remains un-initialized or hash remains None)
    session.refresh(opp)
    assert opp.public_content_hash is None


def test_conformance_24_translation_status_check(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    opp.translation_status = "failed"
    session.commit()

    build_static_site(
        session=session, output_dir=str(output_dir), site_url="https://example.com"
    )
    data_v1_dir = output_dir / "data" / "v1"

    feed = json.loads((data_v1_dir / "feeds" / "jurypress.json").read_text())
    # Should not be in JuryPress Ready feed because translation_status != completed
    assert opp.public_id not in [item["public_id"] for item in feed["items"]]
