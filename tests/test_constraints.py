import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from glintory.domain.enums import (
    CollectionRunStatus,
    Confidence,
    EvidenceRelationType,
    OpportunityStatus,
    SignalType,
)
from glintory.domain.models import (
    Base,
    CollectionRun,
    Decision,
    Note,
    Opportunity,
    OpportunitySignal,
    ScoreSnapshot,
    Signal,
    Source,
)


@pytest.fixture
def db_session():
    # Use SQLite in-memory for fast isolation, but enforce foreign keys
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


# --- Constraint Tests ---


def test_source_name_unique(db_session):
    s1 = Source(name="Github", source_type="github")
    db_session.add(s1)
    db_session.commit()

    s2 = Source(name="Github", source_type="github_other")
    db_session.add(s2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_signal_conditional_unique(db_session):
    s = Source(name="Github", source_type="github")
    db_session.add(s)
    db_session.commit()

    # Same source, same external_id -> Fails
    sig1 = Signal(
        source_id=s.id,
        external_id="123",
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    sig2 = Signal(
        source_id=s.id,
        external_id="123",
        canonical_url="https://github.com/123-2",
        title="Title 2",
        content_hash="hash2",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig1)
    db_session.add(sig2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Different sources, same external_id -> Succeeds
    s2 = Source(name="HackerNews", source_type="hn")
    db_session.add(s2)
    db_session.commit()

    sig1 = Signal(
        source_id=s.id,
        external_id="123",
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    sig2 = Signal(
        source_id=s2.id,
        external_id="123",
        canonical_url="https://hn.com/123",
        title="Title 2",
        content_hash="hash2",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig1)
    db_session.add(sig2)
    db_session.commit()  # Should not raise

    # Null external_id -> Multiple allowed
    sig3 = Signal(
        source_id=s.id,
        external_id=None,
        canonical_url="https://github.com/null1",
        title="Title Null 1",
        content_hash="hash_n1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    sig4 = Signal(
        source_id=s.id,
        external_id=None,
        canonical_url="https://github.com/null2",
        title="Title Null 2",
        content_hash="hash_n2",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig3)
    db_session.add(sig4)
    db_session.commit()  # Should not raise


def test_opportunity_scores_range(db_session):
    # Evidence Score (0-50)
    opp = Opportunity(title="Opp", evidence_score=51)
    db_session.add(opp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    opp = Opportunity(title="Opp", evidence_score=-1)
    db_session.add(opp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Feasibility Score (0-50)
    opp = Opportunity(title="Opp", feasibility_score=51)
    db_session.add(opp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Penalty Score (<= 0)
    opp = Opportunity(title="Opp", penalty_score=1)
    db_session.add(opp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Total Score (0-100)
    opp = Opportunity(title="Opp", total_score=101)
    db_session.add(opp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_opportunity_signal_unique(db_session):
    s = Source(name="Github", source_type="github")
    db_session.add(s)
    db_session.commit()

    sig = Signal(
        source_id=s.id,
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    opp = Opportunity(title="Opp")
    db_session.add(sig)
    db_session.add(opp)
    db_session.commit()

    rel1 = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=sig.id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
    )
    rel2 = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=sig.id,
        relation_type=EvidenceRelationType.RELATED,
        relevance_score=0.5,
    )
    db_session.add(rel1)
    db_session.add(rel2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_foreign_key_violations(db_session):
    # Invalid source_id in Signal
    sig = Signal(
        source_id="invalid-uuid",
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_opportunity_defaults(db_session):
    opp = Opportunity(title="New Opp")
    db_session.add(opp)
    db_session.commit()

    assert opp.status == OpportunityStatus.INBOX
    assert opp.confidence == Confidence.LOW
    assert opp.evidence_score == 0
    assert opp.feasibility_score == 0
    assert opp.penalty_score == 0
    assert opp.total_score == 0


def test_empty_note_body_rejected(db_session):
    opp = Opportunity(title="Opp")
    db_session.add(opp)
    db_session.commit()

    note1 = Note(opportunity_id=opp.id, body="")
    db_session.add(note1)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    note2 = Note(opportunity_id=opp.id, body="   ")
    db_session.add(note2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- Relationship Tests ---


def test_opportunity_cascade_delete(db_session):
    s = Source(name="Github", source_type="github")
    opp = Opportunity(title="Opp")
    db_session.add(s)
    db_session.add(opp)
    db_session.commit()

    sig = Signal(
        source_id=s.id,
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig)
    db_session.commit()

    # Create related records
    rel = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=sig.id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
    )
    snap = ScoreSnapshot(
        opportunity_id=opp.id,
        evidence_score=10,
        feasibility_score=20,
        penalty_score=0,
        total_score=30,
        confidence=Confidence.MEDIUM,
        scoring_version="1.0",
        explanation={},
    )
    dec = Decision(opportunity_id=opp.id, to_status=OpportunityStatus.WATCH)
    note = Note(opportunity_id=opp.id, body="Valid note")

    db_session.add_all([rel, snap, dec, note])
    db_session.commit()

    # Delete Opportunity
    db_session.delete(opp)
    db_session.commit()

    # Verify CASCADE
    assert (
        db_session.query(OpportunitySignal).filter_by(opportunity_id=opp.id).count()
        == 0
    )
    assert db_session.query(ScoreSnapshot).filter_by(opportunity_id=opp.id).count() == 0
    assert db_session.query(Decision).filter_by(opportunity_id=opp.id).count() == 0
    assert db_session.query(Note).filter_by(opportunity_id=opp.id).count() == 0

    # Signal itself must NOT be deleted
    assert db_session.query(Signal).filter_by(id=sig.id).count() == 1


def test_signal_cascade_delete(db_session):
    s = Source(name="Github", source_type="github")
    opp = Opportunity(title="Opp")
    db_session.add(s)
    db_session.add(opp)
    db_session.commit()

    sig = Signal(
        source_id=s.id,
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig)
    db_session.commit()

    rel = OpportunitySignal(
        opportunity_id=opp.id,
        signal_id=sig.id,
        relation_type=EvidenceRelationType.SUPPORTING,
        relevance_score=0.9,
    )
    db_session.add(rel)
    db_session.commit()

    # Delete Signal
    db_session.delete(sig)
    db_session.commit()

    # OpportunitySignal must be CASCADE deleted
    assert db_session.query(OpportunitySignal).filter_by(signal_id=sig.id).count() == 0
    # Opportunity must still exist
    assert db_session.query(Opportunity).filter_by(id=opp.id).count() == 1


def test_source_delete_restrict(db_session):
    s = Source(name="Github", source_type="github")
    db_session.add(s)
    db_session.commit()

    sig = Signal(
        source_id=s.id,
        canonical_url="https://github.com/123",
        title="Title 1",
        content_hash="hash1",
        signal_type=SignalType.PROJECT,
        freshness_score=0.5,
        source_quality_score=0.8,
    )
    db_session.add(sig)
    db_session.commit()

    # Deleting Source should fail because a Signal references it (RESTRICT/NO ACTION default behaviour)
    db_session.delete(s)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_source_delete_collection_run_set_null(db_session):
    s = Source(name="Github", source_type="github")
    db_session.add(s)
    db_session.commit()

    run = CollectionRun(source_id=s.id, status=CollectionRunStatus.RUNNING)
    db_session.add(run)
    db_session.commit()

    # Delete Source
    db_session.delete(s)
    db_session.commit()  # Should succeed, because collection_runs.source_id is ondelete="SET NULL"

    db_session.refresh(run)
    assert run.source_id is None
