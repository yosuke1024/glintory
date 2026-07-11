import os
import pathlib
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
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
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)
from glintory.infrastructure.database import get_db, reset_db_connections
from glintory.main import app


@pytest.fixture
def test_web_db(tmp_path):
    """Sets up temporary database for testing the web routing layer."""
    db_file = tmp_path / "test_web_opp.sqlite3"
    db_url = f"sqlite:///{db_file}"

    # Override database_url
    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    # Apply migrations
    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed initial source
    src = Source(id="src-web-1", name="HN Web", source_type="hackernews")
    session.add(src)
    session.commit()

    # Override get_db dependency in FastAPI app
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield session

    session.close()
    app.dependency_overrides.clear()

    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_web_opportunities_list_empty(test_web_db) -> None:
    client = TestClient(app)
    response = client.get("/opportunities")
    assert response.status_code == 200
    assert "Opportunities" in response.text
    assert "No opportunity candidates are available yet." in response.text


def test_web_opportunities_list_and_detail(test_web_db) -> None:
    # 1. Insert Opportunity
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Web UI Test Opportunity",
        problem_statement="Problem description",
        target_user="Target developer",
        proposed_solution="Proposed solution details",
        existing_projects='["project-a", "project-b"]',
        generation_method="deterministic_cluster",
        status=OpportunityStatus.INBOX,
        confidence=Confidence.LOW,
        evidence_score=10,
        feasibility_score=15,
        penalty_score=-4,
        total_score=21,
        current_scoring_version="v1",
        last_scored_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )
    test_web_db.add(opp)

    # 2. Insert Signal
    sig = Signal(
        id="00000000-0000-0000-0000-000000000002",
        source_id="src-web-1",
        canonical_url="https://example.com/test-opp",
        title="Evidence Signal Title",
        excerpt="Important evidence excerpt details",
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PAIN,
        content_hash="hash-web-2",
        freshness_score=1.0,
        source_quality_score=0.9,
    )
    test_web_db.add(sig)
    test_web_db.commit()

    # 3. Link Signal to Opportunity
    opp_sig = OpportunitySignal(
        opportunity_id="00000000-0000-0000-0000-000000000001",
        signal_id="00000000-0000-0000-0000-000000000002",
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=1.0,
    )
    test_web_db.add(opp_sig)

    # 4. Insert ScoreSnapshot
    snap = ScoreSnapshot(
        opportunity_id="00000000-0000-0000-0000-000000000001",
        evidence_score=10,
        feasibility_score=15,
        penalty_score=-4,
        total_score=21,
        confidence=Confidence.LOW,
        scoring_version="v1",
        input_hash="hash-input-val",
        as_of_date=date(2026, 7, 1),
        explanation={
            "evidence": {
                "components": [
                    {
                        "name": "evidence_volume",
                        "score": 3,
                        "maximum": 12,
                        "explanation": "Volume explanation",
                        "facts": {},
                    }
                ]
            }
        },
        created_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )
    test_web_db.add(snap)
    test_web_db.commit()

    client = TestClient(app)

    # Test HTML List view
    response_list = client.get("/opportunities")
    assert response_list.status_code == 200
    assert "Web UI Test Opportunity" in response_list.text
    assert "21" in response_list.text  # Total score
    assert "Status: inbox" in response_list.text

    # Test HTML Detail view
    response_detail = client.get("/opportunities/00000000-0000-0000-0000-000000000001")
    assert response_detail.status_code == 200
    assert "Web UI Test Opportunity" in response_detail.text
    assert "Problem Statement" in response_detail.text
    assert "Problem description" in response_detail.text
    assert "project-a" in response_detail.text
    assert "project-b" in response_detail.text
    assert "evidence_volume" in response_detail.text

    # Test JSON List API
    response_api_list = client.get("/api/v1/opportunities")
    assert response_api_list.status_code == 200
    res_json = response_api_list.json()
    assert len(res_json["items"]) == 1
    assert res_json["items"][0]["title"] == "Web UI Test Opportunity"
    assert res_json["items"][0]["scores"]["total"] == 21

    # Test JSON Detail API
    response_api_detail = client.get(
        "/api/v1/opportunities/00000000-0000-0000-0000-000000000001"
    )
    assert response_api_detail.status_code == 200
    res_det_json = response_api_detail.json()
    assert res_det_json["title"] == "Web UI Test Opportunity"
    assert res_det_json["latest_snapshot"]["input_hash"] == "hash-input-val"


def test_web_status_transition_success(test_web_db) -> None:
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Web UI Test Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_web_db.add(opp)
    test_web_db.commit()

    client = TestClient(app)
    response_get = client.get("/opportunities/00000000-0000-0000-0000-000000000001")
    assert response_get.status_code == 200
    csrf_token = response_get.cookies.get("glintory_csrf")
    assert csrf_token is not None

    headers = {
        "Origin": "http://testserver",
        "Referer": "http://testserver/opportunities/00000000-0000-0000-0000-000000000001",
    }
    data = {
        "csrf_token": csrf_token,
        "expected_status": "inbox",
        "target_status": "watch",
        "reason": "Looking interesting",
    }
    client.cookies.set("glintory_csrf", csrf_token)
    response_post = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/status",
        data=data,
        headers=headers,
        follow_redirects=False,
    )
    assert response_post.status_code == 303
    assert (
        "/opportunities/00000000-0000-0000-0000-000000000001?notice=status_updated"
        in response_post.headers["location"]
    )

    opp_updated = test_web_db.get(Opportunity, "00000000-0000-0000-0000-000000000001")
    assert opp_updated.status == OpportunityStatus.WATCH


def test_web_status_transition_csrf_failure(test_web_db) -> None:
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Web UI Test Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_web_db.add(opp)
    test_web_db.commit()

    client = TestClient(app)
    data = {
        "csrf_token": "bad-or-missing-token",
        "expected_status": "inbox",
        "target_status": "watch",
        "reason": "CSRF test",
    }
    response_post = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/status",
        data=data,
        follow_redirects=False,
    )
    assert response_post.status_code == 403
    assert "CSRF cookie missing" in response_post.text


def test_web_notes_operations(test_web_db) -> None:
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Web UI Test Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_web_db.add(opp)
    test_web_db.commit()

    client = TestClient(app)
    response_get = client.get("/opportunities/00000000-0000-0000-0000-000000000001")
    csrf_token = response_get.cookies.get("glintory_csrf")
    headers = {
        "Origin": "http://testserver",
        "Referer": "http://testserver/opportunities/00000000-0000-0000-0000-000000000001",
    }
    client.cookies.set("glintory_csrf", csrf_token)

    # 1. Add Note
    response_add = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/notes",
        data={"csrf_token": csrf_token, "body": "This is a new test note"},
        headers=headers,
        follow_redirects=False,
    )
    assert response_add.status_code == 303

    # Load details to verify note was added and extract note ID
    client.get("/api/v1/opportunities/00000000-0000-0000-0000-000000000001")
    # Note details are NOT in the default JSON detail API according to specs (Note本文, Decision Reason, Evidence Review Note are omitted by default)
    # So check via the DB directly
    from glintory.domain.models import Note

    notes = (
        test_web_db.query(Note)
        .filter_by(opportunity_id="00000000-0000-0000-0000-000000000001")
        .all()
    )
    assert len(notes) == 1
    assert notes[0].body == "This is a new test note"
    note_id = notes[0].id

    # 2. Edit Note
    response_edit = client.post(
        f"/opportunities/00000000-0000-0000-0000-000000000001/notes/{note_id}/edit",
        data={"csrf_token": csrf_token, "body": "Updated test note"},
        headers=headers,
        follow_redirects=False,
    )
    assert response_edit.status_code == 303
    test_web_db.expire_all()
    note_updated = test_web_db.get(Note, note_id)
    assert note_updated.body == "Updated test note"

    # 3. Delete Note
    response_delete = client.post(
        f"/opportunities/00000000-0000-0000-0000-000000000001/notes/{note_id}/delete",
        data={"csrf_token": csrf_token},
        headers=headers,
        follow_redirects=False,
    )
    assert response_delete.status_code == 303
    assert test_web_db.query(Note).filter_by(id=note_id).first() is None


def test_web_evidence_operations_and_search(test_web_db) -> None:
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Web UI Test Opportunity",
        status=OpportunityStatus.INBOX,
    )
    sig = Signal(
        id="00000000-0000-0000-0000-000000000002",
        source_id="src-web-1",
        canonical_url="https://example.com/test-opp",
        title="evidence signal title",
        excerpt="Important evidence excerpt details",
        collected_at=datetime.now(UTC),
        signal_type=SignalType.PAIN,
        content_hash="hash-web-2",
        freshness_score=1.0,
        source_quality_score=0.9,
    )
    test_web_db.add(opp)
    test_web_db.add(sig)
    test_web_db.commit()
    test_web_db.close()
    test_web_db.bind.dispose()

    client = TestClient(app)

    # Check Evidence Search Page
    response_search = client.get(
        "/opportunities/00000000-0000-0000-0000-000000000001/evidence/search?q=evidence"
    )
    assert response_search.status_code == 200
    assert "evidence signal title" in response_search.text

    csrf_token = response_search.cookies.get("glintory_csrf")
    headers = {
        "Origin": "http://testserver",
        "Referer": "http://testserver/opportunities/00000000-0000-0000-0000-000000000001/evidence/search",
    }
    client.cookies.set("glintory_csrf", csrf_token)

    # 1. Add Evidence (Link)
    response_link = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/evidence",
        data={
            "csrf_token": csrf_token,
            "signal_id": "00000000-0000-0000-0000-000000000002",
            "relation_type": "supporting",
            "relevance_score": "0.95",
            "review_note": "Linking manually",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert response_link.status_code == 303

    opp_sig = (
        test_web_db.query(OpportunitySignal)
        .filter_by(
            opportunity_id="00000000-0000-0000-0000-000000000001",
            signal_id="00000000-0000-0000-0000-000000000002",
        )
        .first()
    )
    assert opp_sig is not None
    assert opp_sig.is_excluded is False
    assert opp_sig.relation_type == EvidenceRelationType.SUPPORTING
    assert opp_sig.relevance_score == 0.95

    # 2. Update Evidence
    response_update = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/evidence/00000000-0000-0000-0000-000000000002/update",
        data={
            "csrf_token": csrf_token,
            "relation_type": "related",
            "relevance_score": "0.75",
            "review_note": "Updating relation manually",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert response_update.status_code == 303
    test_web_db.expire_all()
    opp_sig = (
        test_web_db.query(OpportunitySignal)
        .filter_by(
            opportunity_id="00000000-0000-0000-0000-000000000001",
            signal_id="00000000-0000-0000-0000-000000000002",
        )
        .first()
    )
    assert opp_sig.relation_type == EvidenceRelationType.RELATED
    assert opp_sig.relevance_score == 0.75

    # 3. Exclude Evidence
    response_exclude = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/evidence/00000000-0000-0000-0000-000000000002/exclude",
        data={"csrf_token": csrf_token, "review_note": "Excluding manually"},
        headers=headers,
        follow_redirects=False,
    )
    assert response_exclude.status_code == 303
    test_web_db.expire_all()
    opp_sig = (
        test_web_db.query(OpportunitySignal)
        .filter_by(
            opportunity_id="00000000-0000-0000-0000-000000000001",
            signal_id="00000000-0000-0000-0000-000000000002",
        )
        .first()
    )
    assert opp_sig.is_excluded is True

    # 4. Restore Evidence
    response_restore = client.post(
        "/opportunities/00000000-0000-0000-0000-000000000001/evidence/00000000-0000-0000-0000-000000000002/restore",
        data={
            "csrf_token": csrf_token,
            "relation_type": "supporting",
            "relevance_score": "0.85",
            "review_note": "Restoring manually",
        },
        headers=headers,
        follow_redirects=False,
    )
    assert response_restore.status_code == 303
    test_web_db.expire_all()
    opp_sig = (
        test_web_db.query(OpportunitySignal)
        .filter_by(
            opportunity_id="00000000-0000-0000-0000-000000000001",
            signal_id="00000000-0000-0000-0000-000000000002",
        )
        .first()
    )
    assert opp_sig.is_excluded is False
    assert opp_sig.relation_type == EvidenceRelationType.SUPPORTING
    assert opp_sig.relevance_score == 0.85


def test_web_watchlist(test_web_db) -> None:
    opp1 = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Watchlist Opportunity",
        status=OpportunityStatus.WATCH,
    )
    opp2 = Opportunity(
        id="00000000-0000-0000-0000-000000000002",
        title="Inbox Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_web_db.add(opp1)
    test_web_db.add(opp2)
    test_web_db.commit()

    client = TestClient(app)
    response = client.get("/watchlist")
    assert response.status_code == 200
    assert "Watchlist" in response.text
    assert "Watchlist Opportunity" in response.text
    assert "Inbox Opportunity" not in response.text
