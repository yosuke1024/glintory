import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import Confidence, OpportunityStatus, SignalType, EvidenceRelationType
from glintory.domain.models import Base, Opportunity, OpportunitySignal, ScoreSnapshot, Signal, Source, OpportunityEnrichment
from glintory.infrastructure.local_llm_client import (
    OpportunityEnrichmentProvider,
    OpportunityEnrichmentRequest,
    OpportunityEnrichmentResponse,
)
from glintory.infrastructure.opportunity_enrichment_repository import OpportunityEnrichmentRepository
from glintory.services.opportunity_enrichment_service import (
    OpportunityEnrichmentService,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)
from glintory.services.static_publishing import build_static_site


class FakeEnrichmentProvider(OpportunityEnrichmentProvider):
    def __init__(self, response: OpportunityEnrichmentResponse) -> None:
        self.response = response
        self.calls: list[OpportunityEnrichmentRequest] = []

    def enrich(self, request: OpportunityEnrichmentRequest) -> OpportunityEnrichmentResponse:
        self.calls.append(request)
        return self.response


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session
    engine.dispose()


@pytest.fixture
def mock_opportunity_data(db_session_factory):
    session = db_session_factory()
    # Setup test source, signal, opportunity
    source = Source(
        name="Test Github Source",
        source_type="github",
        enabled=True,
    )
    session.add(source)
    session.flush()

    signal = Signal(
        source_id=source.id,
        canonical_url="https://github.com/foo/bar",
        title="Test Signal Title",
        excerpt="This is a test signal excerpt containing some feedback.",
        signal_type=SignalType.PAIN,
        content_hash="hash123",
        freshness_score=1.0,
        source_quality_score=1.0,
    )
    session.add(signal)
    session.flush()

    opp = Opportunity(
        title="Test Opportunity Title",
        proposed_solution="Test summary proposed solution.",
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        status=OpportunityStatus.INBOX,
    )
    session.add(opp)
    session.flush()

    opp_sig = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=signal.id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
    )
    session.add(opp_sig)

    snapshot = ScoreSnapshot(
        opportunity_id=opp.id,
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        scoring_version=settings.scoring_version,
        input_hash="snapshot_hash_123",
    )
    session.add(snapshot)
    session.commit()

    opp_id = opp.id
    signal_id = signal.id
    source_id = source.id
    session.close()

    return opp_id, signal_id, source_id


@pytest.fixture(autouse=True)
def mock_llama_server_context():
    with patch("glintory.services.opportunity_enrichment_service.LlamaServerContext") as mock:
        mock.return_value.__enter__.return_value = MagicMock()
        yield mock


def test_enrichment_skips_same_input_hash(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = True

    resp = OpportunityEnrichmentResponse(
        status="succeeded",
        generated_title="AI Title",
        generated_summary="AI Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now is the time",
        evidence_synthesis="Evidence summary",
        build_direction="Build it",
        risks=["No risk"],
        tags=["ai"],
        evidence_refs=[signal_id],
        llm_confidence="medium",
        duration_ms=100,
    )
    provider = FakeEnrichmentProvider(resp)
    service = OpportunityEnrichmentService(db_session_factory, provider)

    # First run
    res = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.succeeded_count == 1
    assert len(provider.calls) == 1

    # Second run without force (should skip)
    res2 = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res2.skipped_count == 1
    assert len(provider.calls) == 1

    # Third run with force
    res3 = service.run_enrichment(affected_opportunity_ids=[opp_id], force=True)
    assert res3.succeeded_count == 1
    assert len(provider.calls) == 2


def test_enrichment_stale_when_evidence_changes(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = True

    resp = OpportunityEnrichmentResponse(
        status="succeeded",
        generated_title="AI Title",
        generated_summary="AI Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now is the time",
        evidence_synthesis="Evidence summary",
        build_direction="Build it",
        risks=["No risk"],
        tags=["ai"],
        evidence_refs=[signal_id],
        llm_confidence="medium",
        duration_ms=100,
    )
    provider = FakeEnrichmentProvider(resp)
    service = OpportunityEnrichmentService(db_session_factory, provider)

    res = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.succeeded_count == 1

    # Change evidence content hash in a new session
    session = db_session_factory()
    sig = session.get(Signal, signal_id)
    sig.content_hash = "new_hash_456"
    session.commit()
    session.close()

    res2 = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res2.succeeded_count == 1
    assert len(provider.calls) == 2


def test_enrichment_stale_when_score_changes(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = True

    resp = OpportunityEnrichmentResponse(
        status="succeeded",
        generated_title="AI Title",
        generated_summary="AI Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now is the time",
        evidence_synthesis="Evidence summary",
        build_direction="Build it",
        risks=["No risk"],
        tags=["ai"],
        evidence_refs=[signal_id],
        llm_confidence="medium",
        duration_ms=100,
    )
    provider = FakeEnrichmentProvider(resp)
    service = OpportunityEnrichmentService(db_session_factory, provider)

    res = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.succeeded_count == 1

    # Add a new snapshot (simulating score change) in a new session
    session = db_session_factory()
    new_snapshot = ScoreSnapshot(
        opportunity_id=opp_id,
        evidence_score=15,
        feasibility_score=10,
        penalty_score=0,
        total_score=25,
        confidence=Confidence.MEDIUM,
        scoring_version=settings.scoring_version,
        input_hash="snapshot_hash_456",
    )
    session.add(new_snapshot)
    session.commit()
    session.close()

    res2 = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res2.succeeded_count == 1
    assert len(provider.calls) == 2


def test_enrichment_skips_when_disabled(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = False

    provider = FakeEnrichmentProvider(OpportunityEnrichmentResponse(status="succeeded"))
    service = OpportunityEnrichmentService(db_session_factory, provider)

    res = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.selected_count == 0
    assert len(provider.calls) == 0


def test_validation_rejects_html_and_invalid_refs(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = True

    from glintory.infrastructure.local_llm_client import LocalLlmProvider
    provider = LocalLlmProvider()

    req = OpportunityEnrichmentRequest(
        opportunity_id=opp_id,
        title="Test Opportunity Title",
        summary="Test solution",
        evidence_count=1,
        confidence="medium",
        evidence=[{
            "id": signal_id,
            "source_name": "src",
            "signal_type": "pain",
            "title": "t",
            "excerpt": "e",
            "published_at": None,
            "canonical_url": "url",
            "relevance_score": 1.0
        }],
    )

    # Mock httpx.Client.post
    with patch("httpx.Client.post") as mock_post:
        # Mock HTML injection response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"title": "<script>alert(1)</script>", "summary": "AI Summary", "problem_statement": "AI Problem", "target_users": ["Developers"], "why_now": "Now is the time", "evidence_synthesis": "Evidence", "build_direction": "Build", "risks": [], "tags": [], "evidence_refs": ["' + signal_id + '"], "confidence": "medium"}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        res = provider.enrich(req)
        assert res.status == "invalid_output"
        assert res.error_code == "LLM_SCHEMA_VALIDATION_FAILED"

    with patch("httpx.Client.post") as mock_post:
        # Mock bad evidence ref response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"title": "AI Title", "summary": "AI Summary", "problem_statement": "AI Problem", "target_users": ["Developers"], "why_now": "Now is the time", "evidence_synthesis": "Evidence", "build_direction": "Build", "risks": [], "tags": [], "evidence_refs": ["bad-ref"], "confidence": "medium"}'
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        res = provider.enrich(req)
        assert res.status == "invalid_output"
        assert res.error_code == "LLM_SCHEMA_VALIDATION_FAILED"


def test_static_site_fallback_and_rendering(db_session_factory, mock_opportunity_data, tmp_path):
    opp_id, signal_id, source_id = mock_opportunity_data
    output_dir = str(tmp_path / "static")

    session = db_session_factory()
    # Render without enrichment (fallback)
    res_fallback = build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    assert res_fallback["total_files"] > 0
    
    # Read details html and verify it has fallback title
    opp_detail_file = os.path.join(output_dir, "opportunities", opp_id, "index.html")
    with open(opp_detail_file, "r") as f:
        content = f.read()
    assert "Test Opportunity Title" in content
    assert "AI-generated brief based on the evidence below." not in content

    # Add enrichment data
    enrichment = OpportunityEnrichment(
        opportunity_id=opp_id,
        status="succeeded",
        model_provider="qwen",
        model_id="Qwen3-1.7B-Q8_0.gguf",
        model_revision="pinned",
        model_sha256="sha256",
        runtime="llama.cpp",
        runtime_version="v1.0",
        prompt_version=PROMPT_VERSION,
        input_hash="hash",
        generated_title="AI Generated Title",
        generated_summary="AI Generated Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now",
        evidence_synthesis="Synthesis",
        build_direction="Direction",
        risks=["Risk"],
        tags=["tag"],
        evidence_refs=[signal_id],
        llm_confidence="high",
        started_at=datetime.now(UTC),
    )
    session.add(enrichment)
    session.commit()

    # Re-render with enrichment
    res_enriched = build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    session.close()
    
    with open(opp_detail_file, "r") as f:
        content = f.read()
    assert "AI Generated Title" in content
    assert "AI Generated Summary" in content
    assert "AI-generated brief based on the evidence below." in content
