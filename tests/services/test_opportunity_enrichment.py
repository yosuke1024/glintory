import json
import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from typing import Sequence
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import Confidence, OpportunityStatus, SignalType, EvidenceRelationType
from glintory.domain.models import (
    Base,
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
    OpportunityEnrichment,
    OpportunityEnrichmentLocalization,
)
from glintory.domain.validation_models import EnglishBrief, JapaneseBrief
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


@pytest.fixture(autouse=True)
def mock_llama_server_context():
    with patch("glintory.infrastructure.local_llm_client.LlamaServerContext") as mock:
        mock.return_value.__enter__.return_value = MagicMock()
        yield mock


class FakeEnrichmentProvider(OpportunityEnrichmentProvider):
    def __init__(self, response: OpportunityEnrichmentResponse) -> None:
        self.response = response
        self.calls: list[OpportunityEnrichmentRequest] = []

    def enrich_many(
        self,
        requests: Sequence[OpportunityEnrichmentRequest],
    ) -> Sequence[OpportunityEnrichmentResponse]:
        for req in requests:
            self.calls.append(req)
        return [self.response] * len(requests)


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


def test_enrichment_skips_same_input_hash(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    english_brief = EnglishBrief(
        title="AI Title",
        summary="AI Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now is the time",
        evidence_synthesis="Evidence summary",
        build_direction="Build it",
        risks=["No risk"],
        tags=["ai"],
    )

    japanese_brief = JapaneseBrief(
        title="AI タイトル",
        summary="AI サマリー",
        problem_statement="AI 課題定義",
        target_users=["開発者"],
        why_now="今がその時",
        evidence_synthesis="証拠の要約",
        build_direction="構築せよ",
        risks=["リスクなし"],
        tags=["ai"],
    )

    resp = OpportunityEnrichmentResponse(
        status="succeeded",
        english=english_brief,
        japanese=japanese_brief,
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
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    english_brief = EnglishBrief(
        title="AI Title",
        summary="AI Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now is the time",
        evidence_synthesis="Evidence summary",
        build_direction="Build it",
        risks=["No risk"],
        tags=["ai"],
    )

    japanese_brief = JapaneseBrief(
        title="AI タイトル",
        summary="AI サマリー",
        problem_statement="AI 課題定義",
        target_users=["開発者"],
        why_now="今がその時",
        evidence_synthesis="証拠の要約",
        build_direction="構築せよ",
        risks=["リスクなし"],
        tags=["ai"],
    )

    resp = OpportunityEnrichmentResponse(
        status="succeeded",
        english=english_brief,
        japanese=japanese_brief,
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
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    english_brief = EnglishBrief(
        title="AI Title",
        summary="AI Summary",
        problem_statement="AI Problem",
        target_users=["Developers"],
        why_now="Now is the time",
        evidence_synthesis="Evidence summary",
        build_direction="Build it",
        risks=["No risk"],
        tags=["ai"],
    )

    japanese_brief = JapaneseBrief(
        title="AI タイトル",
        summary="AI サマリー",
        problem_statement="AI 課題定義",
        target_users=["開発者"],
        why_now="今がその時",
        evidence_synthesis="証拠の要約",
        build_direction="構築せよ",
        risks=["リスクなし"],
        tags=["ai"],
    )

    resp = OpportunityEnrichmentResponse(
        status="succeeded",
        english=english_brief,
        japanese=japanese_brief,
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
    opp_id, _, _ = mock_opportunity_data
    settings.local_llm_enabled = False

    provider = FakeEnrichmentProvider(OpportunityEnrichmentResponse(status="succeeded"))
    service = OpportunityEnrichmentService(db_session_factory, provider)

    res = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.selected_count == 0
    assert len(provider.calls) == 0


def test_validation_rejects_html_and_invalid_refs(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    from glintory.infrastructure.local_llm_client import LocalLlmProvider
    provider = LocalLlmProvider()

    req = OpportunityEnrichmentRequest(
        opportunity_id=opp_id,
        title="Test Opportunity Title",
        summary="Test solution",
        evidence_count=1,
        confidence="medium",
        evidence=[
            {
                "id": signal_id,
                "source_name": "src",
                "signal_type": "pain",
                "title": "t",
                "excerpt": "e",
                "published_at": None,
                "canonical_url": "url",
                "relevance_score": 1.0,
            }
        ],
    )

    # 1. Mock version command to verify_infrastructure succeeds
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.stdout = "version: 5092 (d3bd7193)"
        mock_run.return_value = mock_res

        # Mock check verification paths
        with patch("os.path.exists", return_value=True), patch(
            "glintory.infrastructure.local_llm_client.verify_sha256", return_value=True
        ):

            # 2. Mock httpx.Client.post for HTML injection
            with patch("httpx.Client.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "english": {
                                            "title": "<script>alert(1)</script>",  # HTML injection
                                            "summary": "AI Summary",
                                            "problem_statement": "AI Problem",
                                            "target_users": ["Developers"],
                                            "why_now": "Now is the time",
                                            "evidence_synthesis": "Evidence",
                                            "build_direction": "Build",
                                            "risks": [],
                                            "tags": [],
                                        },
                                        "japanese": {
                                            "title": "AI タイトル",
                                            "summary": "AI サマリー",
                                            "problem_statement": "AI 課題定義",
                                            "target_users": ["開発者"],
                                            "why_now": "今がその時",
                                            "evidence_synthesis": "証拠の要約",
                                            "build_direction": "構築せよ",
                                            "risks": [],
                                            "tags": [],
                                        },
                                        "evidence_refs": [signal_id],
                                        "confidence": "medium",
                                    }
                                )
                            }
                        }
                    ]
                }
                mock_post.return_value = mock_resp

                res = provider.enrich_many([req])
                assert res[0].status == "invalid_output"
                assert res[0].error_code == "LLM_SCHEMA_VALIDATION_FAILED"

            # 3. Mock httpx.Client.post for bad evidence ref
            with patch("httpx.Client.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "english": {
                                            "title": "AI Title",
                                            "summary": "AI Summary",
                                            "problem_statement": "AI Problem",
                                            "target_users": ["Developers"],
                                            "why_now": "Now is the time",
                                            "evidence_synthesis": "Evidence",
                                            "build_direction": "Build",
                                            "risks": [],
                                            "tags": [],
                                        },
                                        "japanese": {
                                            "title": "AI タイトル",
                                            "summary": "AI サマリー",
                                            "problem_statement": "AI 課題定義",
                                            "target_users": ["開発者"],
                                            "why_now": "今がその時",
                                            "evidence_synthesis": "証拠の要約",
                                            "build_direction": "構築せよ",
                                            "risks": [],
                                            "tags": [],
                                        },
                                        "evidence_refs": ["bad-ref"],  # Bad ref
                                        "confidence": "medium",
                                    }
                                )
                            }
                        }
                    ]
                }
                mock_post.return_value = mock_resp

                res = provider.enrich_many([req])
                assert res[0].status == "invalid_output"
                assert res[0].error_code == "LLM_SCHEMA_VALIDATION_FAILED"


def test_static_site_fallback_and_rendering(db_session_factory, mock_opportunity_data, tmp_path):
    opp_id, signal_id, _ = mock_opportunity_data
    output_dir = str(tmp_path / "static")

    session = db_session_factory()
    res_fallback = build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    assert res_fallback["total_files"] > 0

    opp_detail_file = os.path.join(output_dir, "opportunities", opp_id, "index.html")
    with open(opp_detail_file, "r") as f:
        content = f.read()
    assert "Test Opportunity Title" in content
    assert "AI-generated brief based on the evidence below." not in content

    # Add enrichment data and its localization
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
    session.flush()

    en_loc = OpportunityEnrichmentLocalization(
        enrichment_id=enrichment.id,
        locale="en",
        generated_title="AI Eng Title",
        generated_summary="AI Eng Summary",
        problem_statement="AI Eng Problem",
        target_users=["Developers"],
        why_now="Now",
        evidence_synthesis="Synthesis",
        build_direction="Direction",
        risks=["Risk"],
        tags=["tag"],
    )
    session.add(en_loc)

    ja_loc = OpportunityEnrichmentLocalization(
        enrichment_id=enrichment.id,
        locale="ja",
        generated_title="AI 日タイト",
        generated_summary="AI 日サマ",
        problem_statement="AI 日課題",
        target_users=["開発者"],
        why_now="今",
        evidence_synthesis="総合",
        build_direction="推奨",
        risks=["リスク"],
        tags=["タグ"],
    )
    session.add(ja_loc)
    session.commit()

    # Re-render with enrichment
    res_enriched = build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    session.close()

    # Verify English Page (Default)
    with open(opp_detail_file, "r") as f:
        content_en = f.read()
    assert "AI Eng Title" in content_en
    assert "AI Eng Summary" in content_en
    assert "AI-generated brief based on the evidence below." in content_en
    assert "View in Japanese (日本語)" in content_en

    # Verify Japanese Page
    opp_detail_file_ja = os.path.join(output_dir, "opportunities", opp_id, "ja", "index.html")
    assert os.path.exists(opp_detail_file_ja)
    with open(opp_detail_file_ja, "r") as f:
        content_ja = f.read()
    assert "AI 日タイト" in content_ja
    assert "AI 日サマ" in content_ja
    assert "以下の証拠データに基づくAI生成の日本語参考訳です。" in content_ja
    assert "View in English (English)" in content_ja

    # Test Fallback rendering (Japanese localization is missing)
    session = db_session_factory()
    # Delete Japanese localization
    session.query(OpportunityEnrichmentLocalization).filter(
        OpportunityEnrichmentLocalization.locale == "ja"
    ).delete()
    session.commit()

    build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    session.close()

    with open(opp_detail_file_ja, "r") as f:
        content_ja_fallback = f.read()
    assert "AI Generated Title" in content_ja_fallback  # fallback to English
    assert "日本語訳はまだ生成されていません。英語版のAI要約を表示しています。" in content_ja_fallback


def test_zero_evidence_skips_with_warning(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    # Delete all evidence relation records to simulate zero evidence
    session = db_session_factory()
    session.query(OpportunitySignal).filter(OpportunitySignal.opportunity_id == opp_id).delete()
    session.commit()
    session.close()

    resp = OpportunityEnrichmentResponse(status="succeeded")
    provider = FakeEnrichmentProvider(resp)
    service = OpportunityEnrichmentService(db_session_factory, provider)

    res = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.skipped_count == 1
    assert "LLM_NO_EVIDENCE" in res.warning_codes
    assert len(provider.calls) == 0


def test_input_budget_exceeded(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True
    
    # Store old limit
    old_limit = settings.local_llm_max_input_chars
    settings.local_llm_max_input_chars = 50  # extremely small limit to trigger early budget overflow

    try:
        resp = OpportunityEnrichmentResponse(status="succeeded")
        provider = FakeEnrichmentProvider(resp)
        service = OpportunityEnrichmentService(db_session_factory, provider)

        res = service.run_enrichment(affected_opportunity_ids=[opp_id])
        assert res.failed_count == 1
        assert "LLM_INPUT_BUDGET_EXCEEDED" in res.warning_codes
        
        # Verify DB shows failed status with budget error code
        session = db_session_factory()
        enrich = session.query(OpportunityEnrichment).filter(OpportunityEnrichment.opportunity_id == opp_id).first()
        assert enrich is not None
        assert enrich.status == "failed"
        assert enrich.error_code == "LLM_INPUT_BUDGET_EXCEEDED"
        session.close()
    finally:
        settings.local_llm_max_input_chars = old_limit


def test_provider_response_count_mismatch(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    # Case 1: Excess responses
    class ExcessProvider(OpportunityEnrichmentProvider):
        def enrich_many(self, reqs):
            return [OpportunityEnrichmentResponse(status="succeeded")] * (len(reqs) + 1)

    service = OpportunityEnrichmentService(db_session_factory, ExcessProvider())
    with pytest.raises(ValueError, match="Provider Contract Error"):
        service.run_enrichment(affected_opportunity_ids=[opp_id])

    # Verify enrichment record is marked failed in DB
    session = db_session_factory()
    enrich = session.query(OpportunityEnrichment).filter(OpportunityEnrichment.opportunity_id == opp_id).first()
    assert enrich is not None
    assert enrich.status == "failed"
    assert enrich.error_code == "LLM_INFERENCE_FAILED"
    session.close()


def test_runtime_start_failure_leaves_no_running_records(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    # Force verify_infrastructure to raise start failed exception
    class FailingProvider(OpportunityEnrichmentProvider):
        def verify_infrastructure(self):
            raise RuntimeError("LLM_RUNTIME_START_FAILED")
        def enrich_many(self, reqs):
            return []

    service = OpportunityEnrichmentService(db_session_factory, FailingProvider())
    with pytest.raises(RuntimeError, match="LLM_RUNTIME_START_FAILED"):
        service.run_enrichment(affected_opportunity_ids=[opp_id])

    # Verify no enrichment record in DB is left in running state
    session = db_session_factory()
    enrich = session.query(OpportunityEnrichment).filter(OpportunityEnrichment.opportunity_id == opp_id).first()
    assert enrich is None or enrich.status != "running"
    session.close()
