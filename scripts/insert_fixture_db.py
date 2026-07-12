import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure local glintory package path is resolved
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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
    ScoreSnapshot,
    Signal,
    Source,
)


def main():
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./glintory_fixture.db")
    print(f"Initializing fixture DB at: {db_url}")

    engine = create_engine(db_url)

    session_local = sessionmaker(bind=engine)
    session = session_local()

    try:
        # Create Source
        src = Source(
            id="src-fixture-1", name="FixtureSource", source_type="rss", enabled=True
        )
        session.merge(src)

        # Create Opportunity
        opp = Opportunity(
            id="opp-fixture-1",
            public_id="opp_f1111111111111111111111111111111",
            public_revision=1,
            public_lifecycle="active",
            gate_status="passed",
            confidence=Confidence.HIGH,
            status=OpportunityStatus.INBOX,
            current_scoring_version="v2",
            total_score=85,
            evidence_score=40,
            feasibility_score=50,
            penalty_score=-5,
            independent_evidence_count=2,
            demand_evidence_count=1,
            source_type_count=1,
            source_domain_count=1,
            enrichment_status="succeeded",
            translation_status="completed",
            enriched_at=datetime.now(UTC),
            evidence_updated_at=datetime.now(UTC) - timedelta(minutes=5),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            title="Fixture Opportunity",
            title_ja="検証用案件",
            summary_ja="これは検証用テスト案件です。自動化機能の欠如を補います。",
            problem_ja="既存のプロセスに時間がかかりすぎる点。",
            target_user_ja="一般のビジネスオーナー。",
            current_workaround_ja="手動によるスプレッドシート処理での転記。",
            existing_solution_gap_ja="自動連携する機能の欠如。",
            mvp_direction_ja="APIによる自動連携ダッシュボードMVPの開発。",
            why_selected_ja="非常に高い市場の需要が見込めるため。",
            risks_ja="連携先外部APIの利用制限に伴うレートリミット。",
            title_en="Fixture Opportunity EN",
            summary_en="This is a test opportunity for contract verification.",
            problem_en="Existing processes take too much time in companies.",
            target_user_en="General business owners.",
            current_workaround_en="Manual spreadsheet data entry.",
            existing_solution_gap_en="Lack of automated sync function.",
            mvp_direction_en="API automated dashboard MVP.",
            why_selected_en="High market demand.",
            risks_en="API rate limits.",
        )
        session.merge(opp)

        # Create Signals
        sig1 = Signal(
            id="sig-fixture-1",
            source_id="src-fixture-1",
            signal_type=SignalType.PAIN,
            signal_role=SignalRole.DEMAND,
            title="Demand evidence: need user client target developer problem issue workaround alternative.",
            excerpt="Currently doing manual work. Pain is high.",
            canonical_url="https://example.com/fixture-1",
            content_hash="h_fixture_1",
            freshness_score=1.0,
            source_quality_score=1.0,
            collected_at=datetime.now(UTC),
        )
        sig2 = Signal(
            id="sig-fixture-2",
            source_id="src-fixture-1",
            signal_type=SignalType.PAIN,
            signal_role=SignalRole.SUPPLY,
            title="Supply evidence for alternative solution.",
            excerpt="Workaround present. High feasibility.",
            canonical_url="https://example.com/fixture-2",
            content_hash="h_fixture_2",
            freshness_score=1.0,
            source_quality_score=1.0,
            collected_at=datetime.now(UTC),
        )
        session.merge(sig1)
        session.merge(sig2)

        # Connect signals to opportunity
        opp_sig1 = OpportunitySignal(
            opportunity_id="opp-fixture-1",
            signal_id="sig-fixture-1",
            relation_type=EvidenceRelationType.SUPPORTING,
            relevance_score=1.0,
            association_source="clustering",
            is_excluded=False,
            evidence_summary_ja="検証用案件に関連する重要需要エビデンス。",
            evidence_summary_en="Important demand evidence summary.",
        )
        opp_sig2 = OpportunitySignal(
            opportunity_id="opp-fixture-1",
            signal_id="sig-fixture-2",
            relation_type=EvidenceRelationType.SUPPORTING,
            relevance_score=1.0,
            association_source="clustering",
            is_excluded=False,
            evidence_summary_ja="検証用案件に関連する供給エビデンス。",
            evidence_summary_en="Supply evidence summary.",
        )
        session.merge(opp_sig1)
        session.merge(opp_sig2)

        # Add ScoreSnapshot for score components validation
        score_snap = ScoreSnapshot(
            opportunity_id="opp-fixture-1",
            scoring_version="v2",
            total_score=85,
            evidence_score=40,
            feasibility_score=50,
            penalty_score=-5,
            confidence=Confidence.HIGH,
            explanation={
                "evidence": {
                    "components": [
                        {
                            "name": "evidence_score",
                            "score": 40,
                            "max": 50,
                            "reason": "Good evidence",
                        }
                    ]
                },
                "feasibility": {
                    "components": [
                        {
                            "name": "feasibility_score",
                            "score": 50,
                            "max": 50,
                            "reason": "Highly feasible",
                        }
                    ]
                },
                "penalties": {
                    "components": [
                        {
                            "name": "penalty_score",
                            "score": 5,
                            "max": 10,
                            "reason": "Some risk",
                        }
                    ]
                },
            },
            created_at=datetime.now(UTC),
        )
        session.add(score_snap)

        session.commit()
        print("Deterministic fixture data successfully inserted.")
    except Exception as e:
        session.rollback()
        print(f"ERROR: Failed to insert fixture data: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
