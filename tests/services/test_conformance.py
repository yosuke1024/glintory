import os
import pathlib
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import EvidenceRelationType, OpportunityStatus, SignalType
from glintory.domain.models import Opportunity, OpportunitySignal, Signal, Source
from glintory.domain.review import (
    EvidenceAddRequest,
    EvidenceUpdateRequest,
    InvalidStatusTransitionError,
    ReviewReasonRequiredError,
    StatusTransitionRequest,
)
from glintory.domain.search import SignalSearchFilters
from glintory.infrastructure.database import reset_db_connections
from glintory.infrastructure.signal_search import SignalSearchRepository
from glintory.main import app
from glintory.services.opportunity_review import OpportunityReviewService

SRC_ID = "00000000-0000-0000-0000-000000000001"
OPP_ID = "00000000-0000-0000-0000-000000000002"
SIG_ID = "00000000-0000-0000-0000-000000000003"
SIG_ID2 = "00000000-0000-0000-0000-000000000004"


@pytest.fixture(name="test_db")
def fixture_test_db(tmp_path):
    """Sets up a temporary SQLite database with all migrations applied."""
    db_file = tmp_path / "test_conformance.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    # Apply Alembic Migrations
    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))

    engine = create_engine(db_url)
    with engine.connect() as conn:
        alembic_cfg.attributes["connection"] = conn
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    yield session

    session.close()
    engine.dispose()
    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_schema_conformance(test_db) -> None:
    # 1. Check table existence
    inspector = inspect(test_db.bind)
    tables = inspector.get_table_names()

    assert "opportunity_signals" in tables
    assert "decisions" in tables
    assert "notes" in tables
    assert "opportunity_decisions" not in tables
    assert "opportunity_enrichment_localizations" in tables

    # 2. Check association_source check constraint
    src = Source(id=SRC_ID, name="Test Source", source_type="github")
    test_db.add(src)
    test_db.commit()

    opp = Opportunity(id=OPP_ID, title="Test Opp", status=OpportunityStatus.INBOX)
    sig = Signal(
        id=SIG_ID,
        source_id=SRC_ID,
        canonical_url="http://example.com/1",
        title="Sig 1",
        signal_type=SignalType.PAIN,
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    test_db.add_all([opp, sig])
    test_db.commit()

    # Valid values 'clustering' and 'manual'
    link1 = OpportunitySignal(
        opportunity_id=OPP_ID,
        signal_id=SIG_ID,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
        association_source="clustering",
    )
    test_db.add(link1)
    test_db.commit()

    # Invalid value should trigger integrity error due to check constraint
    link1.association_source = "invalid_source"
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()

    # Test opportunity_enrichment_localizations check constraint for locale
    from glintory.domain.models import (
        OpportunityEnrichment,
        OpportunityEnrichmentLocalization,
    )

    enrich = OpportunityEnrichment(
        opportunity_id=OPP_ID,
        status="succeeded",
        model_provider="qwen",
        model_id="model",
        model_revision="rev",
        model_sha256="sha",
        runtime="runtime",
        runtime_version="ver",
        prompt_version="v1",
        input_hash="hash1",
        started_at=datetime.now(UTC),
    )
    test_db.add(enrich)
    test_db.commit()

    # Valid locale 'en'
    loc = OpportunityEnrichmentLocalization(
        enrichment_id=enrich.id,
        locale="en",
    )
    test_db.add(loc)
    test_db.commit()

    # Invalid locale should raise IntegrityError
    loc_invalid = OpportunityEnrichmentLocalization(
        enrichment_id=enrich.id,
        locale="fr",
    )
    test_db.add(loc_invalid)
    with pytest.raises(IntegrityError):
        test_db.commit()
    test_db.rollback()


def test_status_transition_matrix_and_reasons(test_db) -> None:
    # Setup
    src = Source(id=SRC_ID, name="Test Source", source_type="github")
    test_db.add(src)
    opp = Opportunity(id=OPP_ID, title="Test Opp", status=OpportunityStatus.INBOX)
    test_db.add_all([opp, src])
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)

    # Valid: inbox -> watch (no reason required)
    service.transition_status(
        StatusTransitionRequest(
            opportunity_id=OPP_ID,
            expected_status=OpportunityStatus.INBOX,
            target_status=OpportunityStatus.WATCH,
            reason=None,
        )
    )
    assert test_db.get(Opportunity, OPP_ID).status == OpportunityStatus.WATCH

    # Invalid: watch -> watch (same status)
    with pytest.raises(InvalidStatusTransitionError):
        service.transition_status(
            StatusTransitionRequest(
                opportunity_id=OPP_ID,
                expected_status=OpportunityStatus.WATCH,
                target_status=OpportunityStatus.WATCH,
                reason=None,
            )
        )

    # Invalid transition: build -> inbox (matrix rejects this)
    test_db.get(Opportunity, OPP_ID).status = OpportunityStatus.BUILD
    test_db.commit()
    with pytest.raises(InvalidStatusTransitionError):
        service.transition_status(
            StatusTransitionRequest(
                opportunity_id=OPP_ID,
                expected_status=OpportunityStatus.BUILD,
                target_status=OpportunityStatus.INBOX,
                reason=None,
            )
        )

    # Reason required check: target = rejected (requires reason >= 3 chars)
    with pytest.raises(ReviewReasonRequiredError):
        service.transition_status(
            StatusTransitionRequest(
                opportunity_id=OPP_ID,
                expected_status=OpportunityStatus.BUILD,
                target_status=OpportunityStatus.REJECTED,
                reason="  ",  # empty/whitespace
            )
        )

    # Valid with reason >= 3 chars
    service.transition_status(
        StatusTransitionRequest(
            opportunity_id=OPP_ID,
            expected_status=OpportunityStatus.BUILD,
            target_status=OpportunityStatus.REJECTED,
            reason="Not relevant anymore",
        )
    )
    assert test_db.get(Opportunity, OPP_ID).status == OpportunityStatus.REJECTED


def test_evidence_mutation_staleness(test_db) -> None:
    src = Source(id=SRC_ID, name="Test Source", source_type="github")
    opp = Opportunity(
        id=OPP_ID,
        title="Test Opp",
        status=OpportunityStatus.INBOX,
        last_scored_at=datetime(2026, 7, 1, 12, 0, 0),
        current_scoring_version="v1",
        evidence_updated_at=datetime(2026, 7, 1, 10, 0, 0),
    )
    sig = Signal(
        id=SIG_ID,
        source_id=SRC_ID,
        canonical_url="http://example.com/1",
        title="Sig 1",
        signal_type=SignalType.PAIN,
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    test_db.add_all([src, opp, sig])
    test_db.commit()

    service = OpportunityReviewService(
        lambda: test_db, clock=lambda: datetime(2026, 7, 2, 12, 0, 0)
    )

    # 1. Add manual evidence -> score becomes stale (evidence_updated_at updated)
    res = service.add_evidence(
        EvidenceAddRequest(
            opportunity_id=OPP_ID,
            signal_id=SIG_ID,
            relation_type=EvidenceRelationType.SUPPORTING,
            relevance_score=0.8,
            review_note="Manual add",
        )
    )
    assert res.score_is_stale is True
    test_db.expire_all()
    opp_ref = test_db.get(Opportunity, OPP_ID)
    assert opp_ref.evidence_updated_at == datetime(2026, 7, 2, 12, 0, 0)

    # 2. Perfect identical update -> stale = False, evidence_updated_at unchanged
    opp_ref.last_scored_at = datetime(2026, 7, 3, 12, 0, 0)  # clear stale
    test_db.commit()

    res = service.update_evidence(
        EvidenceUpdateRequest(
            opportunity_id=OPP_ID,
            signal_id=SIG_ID,
            relation_type=EvidenceRelationType.SUPPORTING,
            relevance_score=0.8,
            review_note="Manual add",
        )
    )
    assert res.score_is_stale is False
    test_db.expire_all()
    opp_ref = test_db.get(Opportunity, OPP_ID)
    assert opp_ref.evidence_updated_at == datetime(2026, 7, 2, 12, 0, 0)  # unchanged

    # 3. Review note only update -> stale = False, Opportunity.evidence_updated_at unchanged
    res = service.update_evidence(
        EvidenceUpdateRequest(
            opportunity_id=OPP_ID,
            signal_id=SIG_ID,
            relation_type=EvidenceRelationType.SUPPORTING,
            relevance_score=0.8,
            review_note="Updated review note only",
        )
    )
    assert res.score_is_stale is False
    test_db.expire_all()
    opp_ref = test_db.get(Opportunity, OPP_ID)
    assert opp_ref.evidence_updated_at == datetime(2026, 7, 2, 12, 0, 0)  # unchanged

    # 4. Relation / Relevance update -> stale = True
    res = service.update_evidence(
        EvidenceUpdateRequest(
            opportunity_id=OPP_ID,
            signal_id=SIG_ID,
            relation_type=EvidenceRelationType.RELATED,  # changed
            relevance_score=0.8,
            review_note="Updated review note only",
        )
    )
    assert res.score_is_stale is True


def test_fts5_sql_safety_and_bm25_correctness(test_db) -> None:
    repo = SignalSearchRepository(test_db)

    # Insert test data to evaluate BM25
    src = Source(id=SRC_ID, name="Test Source", source_type="github")
    test_db.add(src)
    test_db.commit()

    sig1 = Signal(
        id=SIG_ID,
        source_id=SRC_ID,
        canonical_url="http://example.com/1",
        title="sqlite backup agent",  # Title match
        excerpt="A backup runner.",
        signal_type=SignalType.PROJECT,
        content_hash="h1",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    sig2 = Signal(
        id=SIG_ID2,
        source_id=SRC_ID,
        canonical_url="http://example.com/2",
        title="backup agent",
        excerpt="An agent using sqlite databases.",  # Excerpt match
        signal_type=SignalType.PROJECT,
        content_hash="h2",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    test_db.add_all([sig1, sig2])
    test_db.commit()

    # 1. SQL Injection / Quote input safety
    safe_res = repo.search(
        SignalSearchFilters(query="sqlite' OR 1=1;--"),
        match_expression='"sqlite" OR "1=1"',
    )
    # FTS Match should execute safely without SQL Syntax errors or SQL injection
    assert safe_res is not None

    # 2. BM25 ranking evaluation (Title match sig1 should rank higher than Excerpt match sig2)
    bm25_res = repo.search(
        SignalSearchFilters(query="sqlite"),
        match_expression='"sqlite"',
    )
    assert bm25_res.total_count == 2
    assert bm25_res.items[0].id == SIG_ID  # Title match ranks higher
    assert bm25_res.items[1].id == SIG_ID2  # Excerpt match ranks lower


def test_api_privacy_protection() -> None:
    client = TestClient(app)

    response = client.get("/api/v1/opportunities")
    assert response.status_code == 200
    data = response.json()
    if data["items"]:
        item = data["items"][0]
        assert "notes" not in item
        assert "decisions" not in item
        assert "explanation" not in item
        assert "csrf_token" not in item

    # POST to API endpoint should return 405 Method Not Allowed or 404
    post_resp = client.post("/api/v1/opportunities")
    assert post_resp.status_code in (404, 405)


def test_csrf_route_protection(test_db) -> None:
    opp = Opportunity(id=OPP_ID, title="Test Opp", status=OpportunityStatus.INBOX)
    test_db.add(opp)
    test_db.commit()

    client = TestClient(app)

    response = client.post(
        f"/opportunities/{OPP_ID}/status",
        data={"expected_status": "inbox", "target_status": "watch"},
    )
    assert response.status_code == 403
    assert (
        "CSRF cookie missing" in response.text or "CSRF token missing" in response.text
    )
