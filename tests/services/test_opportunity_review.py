import os
import pathlib
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.config import settings
from glintory.domain.enums import EvidenceRelationType, OpportunityStatus
from glintory.domain.models import Note, Opportunity, Signal, Source
from glintory.domain.review import (
    EvidenceAddRequest,
    InvalidStatusTransitionError,
    NoteCreateRequest,
    ReviewReasonRequiredError,
    ReviewValidationError,
    StatusTransitionRequest,
)
from glintory.infrastructure.database import reset_db_connections
from glintory.services.opportunity_review import OpportunityReviewService


@pytest.fixture
def test_db(tmp_path):
    """Sets up temporary database for testing the review service layer."""
    db_file = tmp_path / "test_review_service.sqlite3"
    db_url = f"sqlite:///{db_file}"

    original_url = settings.database_url
    settings.database_url = db_url
    os.environ["GLINTORY_DATABASE_URL"] = db_url
    reset_db_connections()

    project_root = pathlib.Path(__file__).parent.parent.parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    engine = create_engine(db_url)
    with engine.connect() as connection:
        alembic_cfg.attributes["connection"] = connection
        command.upgrade(alembic_cfg, "head")

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Seed source & signal
    src = Source(id="src-rev-1", name="Review Source", source_type="github")
    session.add(src)
    sig = Signal(
        id="sig-rev-1",
        source_id="src-rev-1",
        canonical_url="https://example.com/rev",
        title="Pain point signal",
        excerpt="The service is slow.",
        collected_at=datetime.now(UTC),
        signal_type="pain",
        content_hash="hash-rev",
        freshness_score=1.0,
        source_quality_score=0.9,
    )
    session.add(sig)
    session.commit()

    yield session

    session.close()
    if db_file.exists():
        db_file.unlink()

    os.environ.pop("GLINTORY_DATABASE_URL", None)
    settings.database_url = original_url
    reset_db_connections()


def test_transition_status_valid(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)
    service.transition_status(
        StatusTransitionRequest(
            opportunity_id="00000000-0000-0000-0000-000000000001",
            expected_status=OpportunityStatus.INBOX,
            target_status=OpportunityStatus.WATCH,
            reason="Good candidate",
        )
    )

    opp_refreshed = test_db.get(Opportunity, "00000000-0000-0000-0000-000000000001")
    assert opp_refreshed.status == OpportunityStatus.WATCH


def test_transition_status_invalid_transition(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)
    # inbox -> build is NOT allowed directly
    with pytest.raises(InvalidStatusTransitionError):
        service.transition_status(
            StatusTransitionRequest(
                opportunity_id="00000000-0000-0000-0000-000000000001",
                expected_status=OpportunityStatus.INBOX,
                target_status=OpportunityStatus.BUILD,
                reason=None,
            )
        )


def test_transition_status_reason_required(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)
    # transition to REJECTED requires reason
    with pytest.raises(ReviewReasonRequiredError):
        service.transition_status(
            StatusTransitionRequest(
                opportunity_id="00000000-0000-0000-0000-000000000001",
                expected_status=OpportunityStatus.INBOX,
                target_status=OpportunityStatus.REJECTED,
                reason="",  # empty
            )
        )


def test_create_note_validation(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)

    # Empty note body
    with pytest.raises(ReviewValidationError):
        service.create_note(
            NoteCreateRequest(
                opportunity_id="00000000-0000-0000-0000-000000000001", body=""
            )
        )

    # Extremely long note body
    with pytest.raises(ReviewValidationError):
        service.create_note(
            NoteCreateRequest(
                opportunity_id="00000000-0000-0000-0000-000000000001",
                body="A" * (settings.review_note_max_chars + 1),
            )
        )


def test_create_note_success(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)
    note_id = service.create_note(
        NoteCreateRequest(
            opportunity_id="00000000-0000-0000-0000-000000000001",
            body="Initial thoughts on validation",
        )
    )
    assert note_id is not None

    note = test_db.get(Note, note_id)
    assert note is not None
    assert note.body == "Initial thoughts on validation"


def test_evidence_relevance_validation(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)

    # Score > 1.0
    with pytest.raises(ReviewValidationError):
        service.add_evidence(
            EvidenceAddRequest(
                opportunity_id="00000000-0000-0000-0000-000000000001",
                signal_id="sig-rev-1",
                relation_type=EvidenceRelationType.SUPPORTING,
                relevance_score=1.5,
                review_note=None,
            )
        )


def test_evidence_contradicting_requires_note(test_db):
    opp = Opportunity(
        id="00000000-0000-0000-0000-000000000001",
        title="Deterministic Opportunity",
        status=OpportunityStatus.INBOX,
    )
    test_db.add(opp)
    test_db.commit()

    service = OpportunityReviewService(lambda: test_db)

    with pytest.raises(ReviewValidationError):
        service.add_evidence(
            EvidenceAddRequest(
                opportunity_id="00000000-0000-0000-0000-000000000001",
                signal_id="sig-rev-1",
                relation_type=EvidenceRelationType.CONTRADICTING,
                relevance_score=0.8,
                review_note="",  # empty contradicting note
            )
        )
