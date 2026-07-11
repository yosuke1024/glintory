import os
import json
import hashlib
import shutil
import pytest
from datetime import UTC, datetime
from typing import Literal, cast
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
    OpportunityPublicAlias,
    AnalysisRun,
    ScoringRun,
)
from glintory.domain.public_contract import (
    PublicOpportunityDetailV1,
    PublicEvidenceV1,
)
from glintory.services.content_hashing import (
    calculate_opportunity_content_hash,
    calculate_opportunity_detail_canonical_hash,
)
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
        id="opp-1",
        title="Test Opportunity",
        public_id="opp_11111111111111111111111111111111",
        public_revision=1,
        title_ja="テストタイトル",
        summary_ja="テスト要約",
        confidence=Confidence.HIGH,
        gate_status="passed",
        status=OpportunityStatus.INBOX,
        current_scoring_version="v2",
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
            "role": "supply",
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


# =====================================================================
# Glintory Issue 12.2 Regression Tests (1-24)
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
        id="sig-1", source_id="src-1", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative.",
        excerpt="Workaround present. Pain is high.", canonical_url="https://example.com/1",
        content_hash="h1", freshness_score=1.0, source_quality_score=1.0, collected_at=now
    )
    sig2 = Signal(
        id="sig-2", source_id="src-1", signal_type=SignalType.PAIN, signal_role=SignalRole.DEMAND,
        title="Need user client target developer problem issue workaround alternative 2.",
        excerpt="Workaround present. Pain is high 2.", canonical_url="https://example.com/2",
        content_hash="h2", freshness_score=1.0, source_quality_score=1.0, collected_at=now
    )
    session.add_all([sig, sig2])
    session.commit()

    opp_sig1 = OpportunitySignal(
        opportunity_id="opp-1", signal_id="sig-1", relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0, association_source="clustering", is_excluded=False,
        evidence_summary_ja="テスト証拠要約"
    )
    opp_sig2 = OpportunitySignal(
        opportunity_id="opp-1", signal_id="sig-2", relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0, association_source="clustering", is_excluded=False,
        evidence_summary_ja="テスト証拠要約2"
    )
    session.add_all([opp_sig1, opp_sig2])
    session.commit()

    return session, opp


# 1 & 2. Supply & Context Evidence Integration Succeeds
def test_regression_1_and_2_roles(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # Setup signals with Supply and Context roles
    sig_supply = session.query(Signal).filter(Signal.id == "sig-1").first()
    sig_supply.signal_role = SignalRole.SUPPLY
    
    sig_context = session.query(Signal).filter(Signal.id == "sig-2").first()
    sig_context.signal_role = SignalRole.CONTEXT
    session.commit()

    # Build should succeed without ValidationError on PublicEvidenceV1.role
    res = build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    assert res["opportunities_generated"] == 1
    
    data_v1_dir = output_dir / "data" / "v1"
    detail = json.loads((data_v1_dir / "opportunities" / f"{opp.public_id}.json").read_text())
    assert detail["evidence"][0]["role"] in ("supply", "context")
    assert detail["evidence"][1]["role"] in ("supply", "context")


# 3. Reject Invalid Signal Role
def test_regression_3_invalid_role(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    detail_path = data_v1_dir / "opportunities" / f"{opp.public_id}.json"
    
    # Tamper with the evidence role to be an invalid literal
    detail_data = json.loads(detail_path.read_text())
    detail_data["evidence"][0]["role"] = "invalid_role"
    detail_path.write_text(json.dumps(detail_data, indent=2))

    errors = validate_public_contract(str(data_v1_dir))
    assert any("validation failed" in err.lower() or "schema" in err.lower() for err in errors)


# 4 & 5. Translation Status update and Manifest Hash consistency
def test_regression_4_and_5_feed_hash_updates(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # Initial build: ready
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    manifest1 = json.loads((data_v1_dir / "manifest.json").read_text())
    hash1 = manifest1["content_hash"]
    
    # Verify in feeds/jurypress.json
    feed1 = json.loads((data_v1_dir / "feeds" / "jurypress.json").read_text())
    assert len(feed1["items"]) == 1

    # Update translation status to failed
    opp.translation_status = "failed"
    session.commit()

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    manifest2 = json.loads((data_v1_dir / "manifest.json").read_text())
    hash2 = manifest2["content_hash"]
    
    feed2 = json.loads((data_v1_dir / "feeds" / "jurypress.json").read_text())
    
    # 4. translation status failed should remove it from ready feed
    assert len(feed2["items"]) == 0
    
    # 5. Manifest hash must change because the dataset state changed
    assert hash1 != hash2


# 6 & 7. Detect Title / Evidence Summary Tampering via Hash Recalculation
def test_regression_6_and_7_title_and_summary_tampering(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    detail_path = data_v1_dir / "opportunities" / f"{opp.public_id}.json"

    # 6. Tamper with title_ja in detail JSON without updating the content_hash field
    original_detail = detail_path.read_text()
    try:
        data = json.loads(original_detail)
        data["localization"]["ja"]["title"] = "改ざんされた日本語タイトル"
        detail_path.write_text(json.dumps(data, indent=2))
        
        errors = validate_public_contract(str(data_v1_dir))
        assert any("content hash integrity failure" in err.lower() for err in errors)
    finally:
        detail_path.write_text(original_detail)

    # 7. Tamper with evidence summary
    try:
        data = json.loads(original_detail)
        data["evidence"][0]["summary_ja"] = "改ざんされた要約"
        detail_path.write_text(json.dumps(data, indent=2))
        
        errors = validate_public_contract(str(data_v1_dir))
        assert any("content hash integrity failure" in err.lower() for err in errors)
    finally:
        detail_path.write_text(original_detail)


# 8 & 9. Localization Status and Total Score Readiness Validation
def test_regression_8_and_9_readiness_recalc_tampering(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    detail_path = data_v1_dir / "opportunities" / f"{opp.public_id}.json"
    original_detail = detail_path.read_text()

    # 8. Localization status failed but ready=True
    try:
        data = json.loads(original_detail)
        data["localization"]["ja"]["status"] = "failed"
        data["jurypress"]["ready"] = True
        
        # We also need to update content_hash so that hash check passes but readiness check fails
        detail_model = PublicOpportunityDetailV1.model_validate(data)
        data["content_hash"] = calculate_opportunity_detail_canonical_hash(detail_model)
        detail_path.write_text(json.dumps(data, indent=2))

        errors = validate_public_contract(str(data_v1_dir))
        assert any("readiness mismatch" in err.lower() for err in errors)
    finally:
        detail_path.write_text(original_detail)

    # 9. Total score below threshold but ready=True
    try:
        data = json.loads(original_detail)
        data["score"]["total"] = 10
        data["jurypress"]["ready"] = True
        detail_model = PublicOpportunityDetailV1.model_validate(data)
        data["content_hash"] = calculate_opportunity_detail_canonical_hash(detail_model)
        detail_path.write_text(json.dumps(data, indent=2))

        errors = validate_public_contract(str(data_v1_dir))
        assert any("readiness mismatch" in err.lower() for err in errors)
    finally:
        detail_path.write_text(original_detail)


# 10. Feed containing Non-Ready item rejection
def test_regression_10_non_ready_in_feed(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    detail_path = data_v1_dir / "opportunities" / f"{opp.public_id}.json"
    feed_path = data_v1_dir / "feeds" / "jurypress.json"
    original_detail = detail_path.read_text()
    original_feed = feed_path.read_text()

    try:
        # Force detail ready to False
        data_detail = json.loads(original_detail)
        data_detail["jurypress"]["ready"] = False
        data_detail["jurypress"]["reasons"] = ["SCORE_BELOW_THRESHOLD"]
        detail_model = PublicOpportunityDetailV1.model_validate(data_detail)
        data_detail["content_hash"] = calculate_opportunity_detail_canonical_hash(detail_model)
        detail_path.write_text(json.dumps(data_detail, indent=2))
        
        # Keep feed containing the item
        errors = validate_public_contract(str(data_v1_dir))
        assert any("JuryPress Feed items mismatch" in err or "readiness flag mismatch" in err.lower() for err in errors)
    finally:
        detail_path.write_text(original_detail)
        feed_path.write_text(original_feed)


# 11, 12 & 13. Retired Tombstone transitions, status, and Revision increment
def test_regression_11_12_13_retired_tombstone(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # Step 1: Initial publish active
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    session.refresh(opp)
    
    data_v1_dir = output_dir / "data" / "v1"
    detail1 = json.loads((data_v1_dir / "opportunities" / f"{opp.public_id}.json").read_text())
    assert detail1["public_lifecycle"] == "active"
    assert opp.public_revision == 1
    h1 = opp.public_content_hash

    # 11. Confidence LOW transitions it to retired detail JSON
    opp = session.get(Opportunity, opp.id)
    opp.confidence = Confidence.LOW
    session.add(opp)
    session.commit()

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    session.refresh(opp)
    
    detail2 = json.loads((data_v1_dir / "opportunities" / f"{opp.public_id}.json").read_text())
    
    # Lifecycle must be retired
    assert detail2["public_lifecycle"] == "retired"
    assert detail2["retired_reason"] == "CONFIDENCE_LOW"
    # Detail fields must be None in retired state
    assert detail2["localization"] is None
    assert detail2["score"] is None

    # 13. Retired transition must increment revision and change content hash
    assert opp.public_revision == 2
    assert opp.public_content_hash != h1
    h2 = opp.public_content_hash

    # 12. Gate status rejected keeps it retired
    opp = session.get(Opportunity, opp.id)
    opp.confidence = Confidence.HIGH
    opp.gate_status = "rejected"
    session.add(opp)
    session.commit()

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    session.refresh(opp)
    
    detail3 = json.loads((data_v1_dir / "opportunities" / f"{opp.public_id}.json").read_text())
    assert detail3["public_lifecycle"] == "retired"
    assert detail3["retired_reason"] == "GATE_REJECTED"
    assert opp.public_revision == 3
    assert opp.public_content_hash != h2


# 14. Merged Public ID redirection JSON
def test_regression_14_merged_detail_json(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # Add alias to db
    alias = OpportunityPublicAlias(
        old_public_id="opp_11111111111111111111111111111112",
        canonical_public_id=opp.public_id,
        created_at=datetime.now(UTC),
    )
    session.add(alias)
    session.commit()

    try:
        build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
        
        data_v1_dir = output_dir / "data" / "v1"
        merged_path = data_v1_dir / "opportunities" / "opp_11111111111111111111111111111112.json"
        assert merged_path.exists()
        
        merged_data = json.loads(merged_path.read_text())
        assert merged_data["public_lifecycle"] == "merged"
        assert merged_data["canonical_public_id"] == opp.public_id
        assert merged_data["canonical_detail_url"] == f"/data/v1/opportunities/{opp.public_id}.json"
    finally:
        session.delete(alias)
        session.commit()


# 15. Retired Detail Schema violations detection
def test_regression_15_retired_detail_schema_error(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # Step 1: Initial publish active
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")

    # Step 2: Trigger retired state
    opp = session.get(Opportunity, opp.id)
    opp.confidence = Confidence.LOW
    session.add(opp)
    session.commit()
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    detail_path = data_v1_dir / "opportunities" / f"{opp.public_id}.json"
    original_detail = detail_path.read_text()

    try:
        # Add forbidden extra field
        data = json.loads(original_detail)
        data["invalid_field"] = "value"
        detail_path.write_text(json.dumps(data, indent=2))
        
        errors = validate_public_contract(str(data_v1_dir))
        assert any("validation failed" in err.lower() or "schema" in err.lower() for err in errors)
    finally:
        detail_path.write_text(original_detail)


# 16. Detection of stray / unknown detailed Opportunity JSONs
def test_regression_16_untracked_stray_json(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    stray_path = data_v1_dir / "opportunities" / "opp_99999999999999999999999999999999.json"
    
    # Write a valid-looking JSON detail which is untracked
    stray_data = {
        "schema_version": "1.0.0",
        "public_id": "opp_99999999999999999999999999999999",
        "public_lifecycle": "active",
        "revision": 1,
        "content_hash": "dummy_hash",
        "localization": {
            "ja": {"status": "pending"},
            "en": {"status": "pending"}
        },
        "score": {
            "total": 60, "evidence": 20, "feasibility": 20, "penalty": 0, "confidence": "high", "version": "v2", "components": []
        },
        "gate": {
            "version": "v2", "status": "passed", "reason": ""
        },
        "evidence": [],
        "jurypress": {"ready": False}
    }
    
    try:
        stray_path.write_text(json.dumps(stray_data, indent=2))
        errors = validate_public_contract(str(data_v1_dir))
        assert any("stray detailed opportunity" in err.lower() or "stray file" in err.lower() for err in errors)
    finally:
        if stray_path.exists():
            stray_path.unlink()


# 17. Detection of active duplicate Public ID in lists
def test_regression_17_duplicate_public_id(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    opps_list_path = data_v1_dir / "opportunities.json"
    original_list = opps_list_path.read_text()

    try:
        data = json.loads(original_list)
        # Duplicate the item inside the list
        data["items"].append(data["items"][0])
        opps_list_path.write_text(json.dumps(data, indent=2))
        
        errors = validate_public_contract(str(data_v1_dir))
        # Recalculated dataset counts should mismatch the manifest or prompt duplicate errors
        assert len(errors) > 0
    finally:
        opps_list_path.write_text(original_list)


# 18. Detection of tampered detail_url
def test_regression_18_tampered_detail_url(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    opps_list_path = data_v1_dir / "opportunities.json"
    original_list = opps_list_path.read_text()

    try:
        data = json.loads(original_list)
        data["items"][0]["detail_url"] = "/invalid/detail.json"
        opps_list_path.write_text(json.dumps(data, indent=2))
        
        errors = validate_public_contract(str(data_v1_dir))
        assert any("invalid detail_url" in err.lower() for err in errors)
    finally:
        opps_list_path.write_text(original_list)


# 19. Detection of missing HTML file
def test_regression_19_missing_html_index(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    html_index_file = output_dir / "opportunities" / opp.public_id / "index.html"
    
    assert html_index_file.exists()
    
    # Temporarily rename/remove HTML file
    html_backup = html_index_file.parent / "backup_index.html"
    shutil.move(str(html_index_file), str(html_backup))
    
    try:
        errors = validate_public_contract(str(data_v1_dir))
        assert any("missing html file" in err.lower() for err in errors)
    finally:
        shutil.move(str(html_backup), str(html_index_file))


# 20 & 21. Value scan allows 'secret manager' but denies credential signatures
def test_regression_20_and_21_value_scanning_rules(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    # 20. Having 'secret manager' in summary should pass validation
    opp.summary_ja = "これは安全な secret manager の提案です。"
    session.commit()
    
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    data_v1_dir = output_dir / "data" / "v1"
    errors = validate_public_contract(str(data_v1_dir))
    assert len(errors) == 0

    # 21. Injecting actual credential formats like Mac path or sqlite URL should fail build_static_site
    opp.summary_ja = "Database url is sqlite:///Users/admin/data.sqlite3"
    session.commit()
    
    with pytest.raises(ValueError) as excinfo:
        build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    assert "Security violation" in str(excinfo.value)


# 22. Any public detail change alters hash
def test_regression_22_hash_changes_on_any_public_field_edit(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    session.refresh(opp)
    h1 = opp.public_content_hash

    # Modify scoring penalty
    opp.penalty_score = -5
    session.commit()
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    session.refresh(opp)
    h2 = opp.public_content_hash
    assert h1 != h2

    # Modify evidence relation relevance score
    opp_sig = session.query(OpportunitySignal).filter(OpportunitySignal.opportunity_id == opp.id).first()
    opp_sig.relevance_score = 0.5
    session.commit()
    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    session.refresh(opp)
    h3 = opp.public_content_hash
    assert h2 != h3


# 23. Byte-identical output from identical input
def test_regression_23_byte_identical_runs(base_opp_setup, tmp_path):
    session, _ = base_opp_setup
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"

    now = datetime.now(UTC)
    build_static_site(session=session, output_dir=str(dir1), site_url="https://example.com", generated_at=now)
    build_static_site(session=session, output_dir=str(dir2), site_url="https://example.com", generated_at=now)

    def read_all_bytes(filepath) -> bytes:
        with open(filepath, "rb") as f:
            return f.read()

    # Verify byte equality of main manifest & feeds
    assert read_all_bytes(dir1 / "data" / "v1" / "manifest.json") == read_all_bytes(dir2 / "data" / "v1" / "manifest.json")
    assert read_all_bytes(dir1 / "data" / "v1" / "opportunities.json") == read_all_bytes(dir2 / "data" / "v1" / "opportunities.json")
    assert read_all_bytes(dir1 / "data" / "v1" / "feeds" / "jurypress.json") == read_all_bytes(dir2 / "data" / "v1" / "feeds" / "jurypress.json")


# 24. Manifest hash tampering detection
def test_regression_24_tampered_manifest_hash(base_opp_setup, tmp_path):
    session, opp = base_opp_setup
    output_dir = tmp_path / "static"

    build_static_site(session=session, output_dir=str(output_dir), site_url="https://example.com")
    
    data_v1_dir = output_dir / "data" / "v1"
    manifest_path = data_v1_dir / "manifest.json"
    original_manifest = manifest_path.read_text()

    try:
        data = json.loads(original_manifest)
        data["content_hash"] = "tampered_hash_value"
        manifest_path.write_text(json.dumps(data, indent=2))
        
        errors = validate_public_contract(str(data_v1_dir))
        assert any("dataset content_hash mismatch" in err.lower() for err in errors)
    finally:
        manifest_path.write_text(original_manifest)
