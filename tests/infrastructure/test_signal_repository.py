from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from glintory.domain.enums import CollectionRunStatus, SignalRole, SignalType
from glintory.domain.models import Base, CollectionRun, Signal, Source
from glintory.domain.signals import NormalizedSignal, SignalIdentityCollisionError
from glintory.infrastructure.repositories import SignalRepository


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    # Create dummy source
    src = Source(id="src-1", name="GitHub", source_type="github")
    src2 = Source(id="src-2", name="HN", source_type="hackernews")
    session.add_all([src, src2])
    session.commit()

    yield session
    session.close()


def test_signal_repository_insert_and_find(db_session):
    repo = SignalRepository(db_session)
    collected_at = datetime.now(UTC)

    normalized = NormalizedSignal(
        source_id="src-1",
        collection_run_id=None,
        external_id="ext-1",
        canonical_url="https://github.com/foo/bar",
        title="Test Title",
        excerpt="Test Excerpt",
        author="alice",
        published_at=None,
        collected_at=collected_at,
        language=None,
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        categories=(),
        tags=(),
        metrics={},
        raw_metadata={},
        content_hash="hash123",
        freshness_score=1.0,
        source_quality_score=0.5,
    )

    sig = repo.insert(normalized)
    db_session.commit()

    # Find by external_id
    found_ext = repo.find_by_external_id("src-1", "ext-1")
    assert found_ext is not None
    assert found_ext.id == sig.id
    assert found_ext.canonical_url == "https://github.com/foo/bar"

    # Find by canonical_url
    found_url = repo.find_by_canonical_url("src-1", "https://github.com/foo/bar")
    assert found_url is not None
    assert found_url.id == sig.id


def test_signal_repository_different_sources_same_url(db_session):
    repo = SignalRepository(db_session)
    collected_at = datetime.now(UTC)

    def create_sig(source_id, ext_id):
        return NormalizedSignal(
            source_id=source_id,
            collection_run_id=None,
            external_id=ext_id,
            canonical_url="https://github.com/foo/bar",
            title="Test Title",
            excerpt="Test Excerpt",
            author="alice",
            published_at=None,
            collected_at=collected_at,
            language=None,
            signal_type=SignalType.PROJECT,
            signal_role=SignalRole.DEMAND,
            categories=(),
            tags=(),
            metrics={},
            raw_metadata={},
            content_hash="hash123",
            freshness_score=1.0,
            source_quality_score=0.5,
        )

    # Different sources can share the same canonical URL
    repo.insert(create_sig("src-1", "ext-1"))
    repo.insert(create_sig("src-2", "ext-2"))
    db_session.commit()


def test_signal_repository_unique_constraint(db_session):
    repo = SignalRepository(db_session)
    collected_at = datetime.now(UTC)

    def create_sig(ext_id):
        return NormalizedSignal(
            source_id="src-1",
            collection_run_id=None,
            external_id=ext_id,
            canonical_url="https://github.com/foo/bar",
            title="Test Title",
            excerpt="Test Excerpt",
            author="alice",
            published_at=None,
            collected_at=collected_at,
            language=None,
            signal_type=SignalType.PROJECT,
            signal_role=SignalRole.DEMAND,
            categories=(),
            tags=(),
            metrics={},
            raw_metadata={},
            content_hash="hash123",
            freshness_score=1.0,
            source_quality_score=0.5,
        )

    repo.insert(create_sig("ext-1"))
    db_session.commit()

    # Inserting duplicate canonical URL in same source causes integrity error
    with pytest.raises(IntegrityError):
        db_session.add(
            Signal(
                source_id="src-1",
                canonical_url="https://github.com/foo/bar",
                title="Duplicate",
                content_hash="hash456",
                freshness_score=1.0,
                source_quality_score=0.5,
                signal_type=SignalType.PROJECT,
                signal_role=SignalRole.DEMAND,
            )
        )
        db_session.commit()


def test_signal_repository_canonical_url_match_resolve(db_session):
    # Set up CollectionRun for run-2
    db_session.add(
        CollectionRun(
            id="run-2",
            source_id="src-1",
            status=CollectionRunStatus.RUNNING,
        )
    )
    db_session.commit()

    repo = SignalRepository(db_session)
    collected_at = datetime.now(UTC)

    # 1. Insert existing with no external_id
    existing_normalized = NormalizedSignal(
        source_id="src-1",
        collection_run_id=None,
        external_id=None,
        canonical_url="https://github.com/foo/bar",
        title="Test Title",
        excerpt="Test Excerpt",
        author="alice",
        published_at=None,
        collected_at=collected_at,
        language=None,
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        categories=(),
        tags=(),
        metrics={},
        raw_metadata={},
        content_hash="hash123",
        freshness_score=1.0,
        source_quality_score=0.5,
    )
    existing_sig = repo.insert(existing_normalized)
    db_session.commit()

    # 2. Incoming signal has external_id, matches canonical URL
    incoming = NormalizedSignal(
        source_id="src-1",
        collection_run_id="run-2",
        external_id="ext-new",
        canonical_url="https://github.com/foo/bar",
        title="Test Title Updated",
        excerpt="Test Excerpt",
        author="alice",
        published_at=None,
        collected_at=collected_at,
        language=None,
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        categories=(),
        tags=(),
        metrics={},
        raw_metadata={},
        content_hash="hash123",
        freshness_score=1.0,
        source_quality_score=0.5,
    )

    # Resolve using canonical_url match and populate external_id
    repo.update_existing(existing_sig, incoming)
    db_session.commit()

    # Verify external_id populated
    db_session.refresh(existing_sig)
    assert existing_sig.external_id == "ext-new"


def test_signal_repository_collision_different_ext_ids(db_session):
    repo = SignalRepository(db_session)
    collected_at = datetime.now(UTC)

    # Existing with ext-1
    repo.insert(
        NormalizedSignal(
            source_id="src-1",
            collection_run_id=None,
            external_id="ext-1",
            canonical_url="https://github.com/foo/bar",
            title="Test",
            excerpt="",
            author=None,
            published_at=None,
            collected_at=collected_at,
            language=None,
            signal_type=SignalType.PROJECT,
            signal_role=SignalRole.DEMAND,
            categories=(),
            tags=(),
            metrics={},
            raw_metadata={},
            content_hash="h1",
            freshness_score=1.0,
            source_quality_score=0.5,
        )
    )
    db_session.commit()

    # Incoming with different ext_id on same canonical URL -> Collision!
    incoming = NormalizedSignal(
        source_id="src-1",
        collection_run_id=None,
        external_id="ext-2",
        canonical_url="https://github.com/foo/bar",
        title="Test",
        excerpt="",
        author=None,
        published_at=None,
        collected_at=collected_at,
        language=None,
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        categories=(),
        tags=(),
        metrics={},
        raw_metadata={},
        content_hash="h2",
        freshness_score=1.0,
        source_quality_score=0.5,
    )

    existing = repo.find_by_canonical_url("src-1", "https://github.com/foo/bar")
    assert existing is not None
    with pytest.raises(SignalIdentityCollisionError):
        repo.update_existing(existing, incoming)


def test_signal_repository_update_url_change(db_session):
    repo = SignalRepository(db_session)
    collected_at = datetime.now(UTC)

    # Existing
    existing_sig = repo.insert(
        NormalizedSignal(
            source_id="src-1",
            collection_run_id=None,
            external_id="ext-1",
            canonical_url="https://github.com/foo/bar",
            title="Test",
            excerpt="",
            author=None,
            published_at=None,
            collected_at=collected_at,
            language=None,
            signal_type=SignalType.PROJECT,
            signal_role=SignalRole.DEMAND,
            categories=(),
            tags=(),
            metrics={},
            raw_metadata={},
            content_hash="h1",
            freshness_score=1.0,
            source_quality_score=0.5,
        )
    )
    db_session.commit()

    # Incoming changes URL
    incoming = NormalizedSignal(
        source_id="src-1",
        collection_run_id=None,
        external_id="ext-1",
        canonical_url="https://github.com/foo/new-url",
        title="Test",
        excerpt="",
        author=None,
        published_at=None,
        collected_at=collected_at,
        language=None,
        signal_type=SignalType.PROJECT,
        signal_role=SignalRole.DEMAND,
        categories=(),
        tags=(),
        metrics={},
        raw_metadata={},
        content_hash="h2",
        freshness_score=1.0,
        source_quality_score=0.5,
    )

    repo.update_existing(existing_sig, incoming)
    db_session.commit()
    db_session.refresh(existing_sig)
    assert existing_sig.canonical_url == "https://github.com/foo/new-url"
