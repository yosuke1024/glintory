import json
import logging
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import (
    Confidence,
    EvidenceRelationType,
    OpportunityStatus,
    SignalType,
)
from glintory.domain.models import (
    Base,
    Opportunity,
    OpportunityEnrichment,
    OpportunityEnrichmentLocalization,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)
from glintory.domain.validation_models import EnglishBrief, JapaneseBrief
from glintory.infrastructure.local_llm_client import (
    OpportunityEnrichmentProvider,
    OpportunityEnrichmentRequest,
    OpportunityEnrichmentResponse,
)
from glintory.infrastructure.opportunity_enrichment_repository import (
    OpportunityEnrichmentRepository,
)
from glintory.services.opportunity_enrichment_service import (
    PROMPT_VERSION,
    OpportunityEnrichmentService,
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
        from glintory.infrastructure.local_llm_client import LocalLlmRuntimeDescriptor

        self.runtime_descriptor = LocalLlmRuntimeDescriptor(
            version="b5092",
            commit="d3bd7193ba66c15963fd1c59448f22019a8caf6e",
            binary_sha256="f7396752344cc252f57339ad62912a79559b3dd8c80b0c2d49cce0a6fb6ca41e",
        )

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
    session_class = sessionmaker(bind=engine)
    yield session_class
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
        title_ja="テスト案件タイトル",
        summary_ja="テスト案件概要",
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        status=OpportunityStatus.INBOX,
        current_scoring_version="v2",
        gate_status="passed",
        last_scored_at=datetime.now(UTC),
    )
    session.add(opp)
    session.flush()

    opp_sig = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=signal.id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
        is_excluded=False,
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
        target_user="Developers",
        problem="AI Problem",
        current_workaround="Now is the time",
        existing_solution_gap="Evidence summary",
        mvp_direction="Build it",
        why_selected="Why selected",
        risks="No risk",
    )

    japanese_brief = JapaneseBrief(
        title="AI タイトル",
        summary="AI サマリー",
        target_user="開発者",
        problem="AI 課題定義",
        current_workaround="今がその時",
        existing_solution_gap="証拠の要約",
        mvp_direction="構築せよ",
        why_selected="選定理由",
        risks="リスクなし",
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

    # Assert database audit fields in unit test
    session = db_session_factory()
    enrich = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.opportunity_id == opp_id)
        .first()
    )
    assert enrich is not None
    assert enrich.runtime_version == "b5092"
    assert enrich.runtime_commit == "d3bd7193ba66c15963fd1c59448f22019a8caf6e"
    assert (
        enrich.runtime_binary_sha256
        == "f7396752344cc252f57339ad62912a79559b3dd8c80b0c2d49cce0a6fb6ca41e"
    )
    assert enrich.model_revision == settings.local_llm_model_revision
    assert enrich.model_sha256 == settings.local_llm_model_sha256
    assert enrich.prompt_version == PROMPT_VERSION
    session.close()

    # Second run without force (should skip)
    res2 = service.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res2.skipped_count == 1
    assert len(provider.calls) == 1

    # Third run with force
    res3 = service.run_enrichment(affected_opportunity_ids=[opp_id], force=True)
    assert res3.succeeded_count == 1
    assert len(provider.calls) == 2


def test_enrichment_stale_when_evidence_changes(
    db_session_factory, mock_opportunity_data
):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    english_brief = EnglishBrief(
        title="AI Title",
        summary="AI Summary",
        target_user="Developers",
        problem="AI Problem",
        current_workaround="Now is the time",
        existing_solution_gap="Evidence summary",
        mvp_direction="Build it",
        why_selected="Why selected",
        risks="No risk",
    )

    japanese_brief = JapaneseBrief(
        title="AI タイトル",
        summary="AI サマリー",
        target_user="開発者",
        problem="AI 課題定義",
        current_workaround="今がその時",
        existing_solution_gap="証拠の要約",
        mvp_direction="構築せよ",
        why_selected="選定理由",
        risks="リスクなし",
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
        target_user="Developers",
        problem="AI Problem",
        current_workaround="Now is the time",
        existing_solution_gap="Evidence summary",
        mvp_direction="Build it",
        why_selected="Why selected",
        risks="No risk",
    )

    japanese_brief = JapaneseBrief(
        title="AI タイトル",
        summary="AI サマリー",
        target_user="開発者",
        problem="AI 課題定義",
        current_workaround="今がその時",
        existing_solution_gap="証拠の要約",
        mvp_direction="構築せよ",
        why_selected="選定理由",
        risks="リスクなし",
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


def test_validation_rejects_html_and_invalid_refs(
    db_session_factory, mock_opportunity_data
):
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
        with (
            patch("os.path.exists", return_value=True),
            patch(
                "glintory.infrastructure.local_llm_client.verify_sha256",
                return_value=True,
            ),
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


def test_static_site_fallback_and_rendering(
    db_session_factory, mock_opportunity_data, tmp_path
):
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
    with open(opp_detail_file) as f:
        content = f.read()
    assert "テスト案件タイトル" in content
    assert "AI-generated brief based on the evidence below." not in content

    # Add enrichment data and its localization
    from glintory.domain.models import OpportunitySignal, Signal, Source
    from glintory.services.static_publishing import calculate_current_hash

    ev_signals = (
        session.query(
            Signal,
            OpportunitySignal.relevance_score,
            OpportunitySignal.evidence_summary_en,
            OpportunitySignal.evidence_summary_ja,
            Source.name,
            Source.source_type,
        )
        .join(OpportunitySignal, Signal.id == OpportunitySignal.signal_id)
        .join(Source, Signal.source_id == Source.id)
        .filter(OpportunitySignal.opportunity_id == opp_id)
        .filter(OpportunitySignal.is_excluded.is_(False))
        .order_by(OpportunitySignal.relevance_score.desc())
        .all()
    )
    current_hash = calculate_current_hash(opp_id, "snapshot_hash_123", ev_signals)

    # Sync localization fields directly to Opportunity
    from glintory.domain.models import Opportunity

    opp = session.get(Opportunity, opp_id)
    opp.translation_status = "completed"
    opp.title_en = "AI Eng Title"
    opp.summary_en = "AI Eng Summary"
    opp.problem_en = "AI Eng Problem"
    opp.target_user_en = "Developers"
    opp.current_workaround_en = "Now"
    opp.existing_solution_gap_en = "Synthesis"
    opp.mvp_direction_en = "Direction"
    opp.risks_en = "Risk"

    opp.title_ja = "AI 日タイト"
    opp.summary_ja = "AI 日サマ"
    opp.problem_ja = "AI 日課題"
    opp.target_user_ja = "開発者"
    opp.current_workaround_ja = "今"
    opp.existing_solution_gap_ja = "総合"
    opp.mvp_direction_ja = "推奨"
    opp.risks_ja = "リスク"

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
        input_hash=current_hash,
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
    build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    session.close()

    # Verify Japanese Page (Default)
    with open(opp_detail_file) as f:
        content_ja = f.read()
    assert "AI 日タイト" in content_ja
    assert "AI 日サマ" in content_ja
    assert "✨ 以下の証拠データに基づくAI生成の日本語参考訳です。" in content_ja
    assert "English" in content_ja

    # Verify English Page
    opp_detail_file_en = os.path.join(
        output_dir, "opportunities", opp_id, "en", "index.html"
    )
    assert os.path.exists(opp_detail_file_en)
    with open(opp_detail_file_en) as f:
        content_en = f.read()
    assert "AI Eng Title" in content_en
    assert "AI Eng Summary" in content_en
    assert "AI-generated brief based on the evidence below." in content_en
    assert "日本語" in content_en

    # Test Fallback rendering (Japanese localization is missing)
    session = db_session_factory()
    # Delete Japanese localization
    session.query(OpportunityEnrichmentLocalization).filter(
        OpportunityEnrichmentLocalization.locale == "ja"
    ).delete()
    opp = session.get(Opportunity, opp_id)
    opp.title_ja = None
    opp.summary_ja = None
    opp.problem_ja = None
    opp.target_user_ja = None
    opp.current_workaround_ja = None
    opp.existing_solution_gap_ja = None
    opp.mvp_direction_ja = None
    opp.risks_ja = None
    session.commit()

    build_static_site(
        session=session,
        output_dir=output_dir,
        site_url="https://example.com/glintory",
    )
    session.close()

    with open(opp_detail_file) as f:
        content_ja_fallback = f.read()
    assert "日本語要約はまだ生成されていません。" in content_ja_fallback


def test_zero_evidence_skips_with_warning(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    # Delete all evidence relation records to simulate zero evidence
    session = db_session_factory()
    session.query(OpportunitySignal).filter(
        OpportunitySignal.opportunity_id == opp_id
    ).delete()
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
    settings.local_llm_max_input_chars = (
        50  # extremely small limit to trigger early budget overflow
    )

    try:
        resp = OpportunityEnrichmentResponse(status="succeeded")
        provider = FakeEnrichmentProvider(resp)
        service = OpportunityEnrichmentService(db_session_factory, provider)

        res = service.run_enrichment(affected_opportunity_ids=[opp_id])
        assert res.failed_count == 1
        assert "LLM_INPUT_BUDGET_EXCEEDED" in res.warning_codes

        # Verify DB shows failed status with budget error code
        session = db_session_factory()
        enrich = (
            session.query(OpportunityEnrichment)
            .filter(OpportunityEnrichment.opportunity_id == opp_id)
            .first()
        )
        assert enrich is not None
        assert enrich.status == "failed"
        assert enrich.error_code == "LLM_INPUT_BUDGET_EXCEEDED"
        session.close()
    finally:
        settings.local_llm_max_input_chars = old_limit


def test_provider_response_count_mismatch(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = True

    # Case 1: Excess responses
    class ExcessProvider(OpportunityEnrichmentProvider):
        def enrich_many(self, reqs):
            return [OpportunityEnrichmentResponse(status="succeeded")] * (len(reqs) + 1)

    service = OpportunityEnrichmentService(db_session_factory, ExcessProvider())
    with pytest.raises(ValueError, match="LLM_PROVIDER_CONTRACT_FAILED"):
        service.run_enrichment(affected_opportunity_ids=[opp_id])

    # Verify enrichment record is marked failed in DB
    session = db_session_factory()
    enrich = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.opportunity_id == opp_id)
        .first()
    )
    assert enrich is not None
    assert enrich.status == "failed"
    assert enrich.error_code == "LLM_PROVIDER_CONTRACT_FAILED"

    running_count = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.status == "running")
        .count()
    )
    assert running_count == 0
    session.close()


def test_provider_response_deficient_count_mismatch(
    db_session_factory, mock_opportunity_data
):
    opp_id, signal_id, source_id = mock_opportunity_data
    settings.local_llm_enabled = True

    # Create a second opportunity
    session = db_session_factory()
    opp2 = Opportunity(
        title="Test Opportunity Title 2",
        proposed_solution="Test summary proposed solution 2.",
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        status=OpportunityStatus.INBOX,
    )
    session.add(opp2)
    session.flush()
    opp2_id = opp2.id

    opp_sig2 = OpportunitySignal(
        opportunity_id=opp2.id,
        signal_id=signal_id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
    )
    session.add(opp_sig2)

    snapshot2 = ScoreSnapshot(
        opportunity_id=opp2.id,
        evidence_score=10,
        feasibility_score=10,
        penalty_score=0,
        total_score=20,
        confidence=Confidence.MEDIUM,
        scoring_version=settings.scoring_version,
        input_hash="snapshot_hash_456",
    )
    session.add(snapshot2)
    session.commit()
    session.close()

    # Requests: 2, Responses: 1
    class DeficientProvider(OpportunityEnrichmentProvider):
        def enrich_many(self, _reqs):
            return [
                OpportunityEnrichmentResponse(
                    status="succeeded",
                    english=EnglishBrief(
                        title="Deficient Title 1",
                        summary="Summary 1",
                        target_user="User 1",
                        problem="Problem 1",
                        current_workaround="Why 1",
                        existing_solution_gap="Synthesis 1",
                        mvp_direction="Direction 1",
                        why_selected="Why selected",
                        risks="Risk 1",
                    ),
                    japanese=JapaneseBrief(
                        title="日本語タイトル 1",
                        summary="概要 1",
                        target_user="ユーザー 1",
                        problem="課題 1",
                        current_workaround="背景 1",
                        existing_solution_gap="証拠 1",
                        mvp_direction="方向性 1",
                        why_selected="選定理由",
                        risks="リスク 1",
                    ),
                    evidence_refs=["smoke_sig"],
                    llm_confidence="high",
                    duration_ms=100,
                )
            ]

    service = OpportunityEnrichmentService(db_session_factory, DeficientProvider())
    res = service.run_enrichment(affected_opportunity_ids=[opp_id, opp2_id])

    assert res.succeeded_count == 1
    assert res.failed_count == 1

    session = db_session_factory()
    e1 = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.opportunity_id == opp_id)
        .first()
    )
    e2 = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.opportunity_id == opp2_id)
        .first()
    )
    assert e1 is not None
    assert e2 is not None

    statuses = {e1.status, e2.status}
    assert statuses == {"succeeded", "failed"}

    succeeded_enrich = e1 if e1.status == "succeeded" else e2
    failed_enrich = e1 if e1.status == "failed" else e2

    assert succeeded_enrich.generated_title == "Deficient Title 1"
    assert failed_enrich.error_code == "LLM_INFERENCE_FAILED"

    running_count = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.status == "running")
        .count()
    )
    assert running_count == 0
    session.close()


def test_v1_to_v2_migration_recomputation(db_session_factory, mock_opportunity_data):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    session = db_session_factory()
    service = OpportunityEnrichmentService(db_session_factory, cast(Any, None))

    repo = OpportunityEnrichmentRepository(session)
    opps = service._select_opportunities(
        session, repo, affected_opportunity_ids=[opp_id]
    )
    assert len(opps) == 1
    opp, score_hash, evidences = opps[0]
    input_hash = service.calculate_input_hash(
        opportunity_id=opp_id, score_input_hash=score_hash, evidences=evidences
    )

    from datetime import UTC, datetime

    v1_enrich = OpportunityEnrichment(
        opportunity_id=opp_id,
        status="succeeded",
        model_provider="qwen",
        model_id=settings.local_llm_model_file,
        model_revision=settings.local_llm_model_revision,
        model_sha256=settings.local_llm_model_sha256,
        runtime="llama.cpp",
        runtime_version="b5092",
        prompt_version="v1",
        input_hash=input_hash,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(v1_enrich)
    session.commit()
    session.close()

    class DummyProvider(OpportunityEnrichmentProvider):
        def enrich_many(self, _reqs):
            return [
                OpportunityEnrichmentResponse(
                    status="succeeded",
                    english=EnglishBrief(
                        title="V2 Title",
                        summary="Summary",
                        target_user="User",
                        problem="Problem",
                        current_workaround="Why",
                        existing_solution_gap="Synthesis",
                        mvp_direction="Direction",
                        why_selected="Why selected",
                        risks="Risk",
                    ),
                    japanese=JapaneseBrief(
                        title="V2 日本語タイトル",
                        summary="概要",
                        target_user="ユーザー",
                        problem="課題",
                        current_workaround="背景",
                        existing_solution_gap="証拠",
                        mvp_direction="方向性",
                        why_selected="選定理由",
                        risks="リスク",
                    ),
                    evidence_refs=["smoke_sig"],
                    llm_confidence="high",
                    duration_ms=100,
                )
            ]

    service2 = OpportunityEnrichmentService(db_session_factory, DummyProvider())
    res = service2.run_enrichment(affected_opportunity_ids=[opp_id])
    assert res.succeeded_count == 1
    assert res.skipped_count == 0

    session = db_session_factory()
    v2_enrich = (
        session.query(OpportunityEnrichment)
        .filter(
            OpportunityEnrichment.opportunity_id == opp_id,
            OpportunityEnrichment.prompt_version == "v2",
        )
        .first()
    )
    assert v2_enrich is not None
    assert v2_enrich.status == "succeeded"
    assert v2_enrich.generated_title == "V2 Title"
    session.close()


def test_leak_prevention_on_failures(db_session_factory, mock_opportunity_data, caplog):
    import sys
    from io import StringIO

    opp_id, _, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    secret_token = "TOKEN_SECRET_12345"
    secret_db = "sqlite:///private_leaked.db"
    secret_body = "HTTP response body containing sensitive info"
    secret_path = "/Users/example/private_folder/private.gguf"

    class LeakingProvider(OpportunityEnrichmentProvider):
        def verify_infrastructure(self):
            raise ValueError(
                f"Failed with {secret_token} and {secret_db} and {secret_body} and {secret_path}"
            )

        def enrich_many(self, _reqs):
            return []

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = StringIO()
    sys.stderr = StringIO()

    import argparse

    from glintory.cli import run_enrich_command

    class MockRuntime:
        def __init__(self, session_factory):
            self.session_factory = session_factory

    args = argparse.Namespace(
        opportunity=opp_id,
        max_opportunities=1,
        force=True,
        json=True,
        require_all_success=True,
    )
    runtime = MockRuntime(db_session_factory)

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        from unittest.mock import patch

        with patch(
            "glintory.infrastructure.local_llm_client.LocalLlmProvider", LeakingProvider
        ):
            exit_code = pytest.importorskip("asyncio").run(
                run_enrich_command(args, runtime)
            )

    stdout_val = sys.stdout.getvalue()
    stderr_val = sys.stderr.getvalue()
    sys.stdout = old_stdout
    sys.stderr = old_stderr

    assert exit_code == 1

    for secret in (secret_token, secret_db, secret_body, secret_path):
        assert secret not in stdout_val, f"Secret leaked in stdout: {secret}"
        assert secret not in stderr_val, f"Secret leaked in stderr: {secret}"
        for record in caplog.records:
            assert secret not in record.message, (
                f"Secret leaked in logger: {record.message}"
            )


def test_runtime_start_failure_leaves_no_running_records(
    db_session_factory, mock_opportunity_data
):
    opp_id, signal_id, _ = mock_opportunity_data
    settings.local_llm_enabled = True

    # Force verify_infrastructure to raise start failed exception
    class FailingProvider(OpportunityEnrichmentProvider):
        def verify_infrastructure(self):
            raise RuntimeError("LLM_RUNTIME_START_FAILED")

        def enrich_many(self, _reqs):
            return []

    service = OpportunityEnrichmentService(db_session_factory, FailingProvider())
    with pytest.raises(RuntimeError, match="LLM_RUNTIME_START_FAILED"):
        service.run_enrichment(affected_opportunity_ids=[opp_id])

    # Verify no enrichment record in DB is left in running state
    session = db_session_factory()
    enrich = (
        session.query(OpportunityEnrichment)
        .filter(OpportunityEnrichment.opportunity_id == opp_id)
        .first()
    )
    assert enrich is None or enrich.status != "running"
    session.close()
