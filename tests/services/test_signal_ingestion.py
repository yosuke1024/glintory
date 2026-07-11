from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from glintory.collectors.base import RawItem
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, CollectionRun, Signal, Source
from glintory.services.signal_ingestion import SignalIngestionService


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    # Initialize sources and runs
    session = session_factory()
    session.add(Source(id="src-1", name="GitHub", source_type="github"))
    session.flush()

    session.add(
        CollectionRun(id="run-1", source_id="src-1", status=CollectionRunStatus.SUCCEEDED)
    )
    session.add(
        CollectionRun(id="run-2", source_id="src-1", status=CollectionRunStatus.RUNNING)
    )

    session.commit()
    session.close()

    return session_factory


def test_ingest_first_collection(session_factory):
    service = SignalIngestionService(session_factory)
    collected_at = datetime.now(UTC)

    items = [
        RawItem(
            external_id="item-1",
            url="https://github.com/foo/bar",
            title="First Item",
            item_type="repository",
            metadata={"stargazers_count": 100},
        ),
        RawItem(
            external_id="item-2",
            url="https://github.com/foo/baz",
            title="Second Item",
            item_type="issue",
        ),
    ]

    result = service.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        raw_items=items,
        collected_at=collected_at,
    )

    assert result.inserted_count == 2
    assert result.updated_count == 0
    assert result.duplicate_count == 0
    assert len(result.signal_ids) == 2
    assert len(result.errors) == 0

    # Verify saved
    session = session_factory()
    sigs = session.query(Signal).all()
    assert len(sigs) == 2
    assert {sig.external_id for sig in sigs} == {"item-1", "item-2"}
    session.close()


def test_ingest_duplicate_and_update(session_factory):
    service = SignalIngestionService(session_factory)
    collected_at1 = datetime.now(UTC) - timedelta(hours=1)

    items = [
        RawItem(
            external_id="item-1",
            url="https://github.com/foo/bar",
            title="First Item",
            item_type="repository",
            metadata={"stargazers_count": 100},
        ),
        RawItem(
            external_id="item-2",
            url="https://github.com/foo/baz",
            title="Second Item",
            item_type="issue",
        ),
    ]

    # First run: inserts
    service.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        raw_items=items,
        collected_at=collected_at1,
    )

    # Second run: item-1 is duplicate, item-2 is updated, item-3 is new
    collected_at2 = datetime.now(UTC)
    items_run2 = [
        # Duplicate
        RawItem(
            external_id="item-1",
            url="https://github.com/foo/bar",
            title="First Item",
            item_type="repository",
            metadata={"stargazers_count": 100},
        ),
        # Updated title
        RawItem(
            external_id="item-2",
            url="https://github.com/foo/baz",
            title="Second Item Updated",
            item_type="issue",
        ),
        # New item
        RawItem(
            external_id="item-3",
            url="https://github.com/foo/qux",
            title="Third Item",
            item_type="issue",
        ),
    ]

    result = service.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-2",
        raw_items=items_run2,
        collected_at=collected_at2,
    )

    assert result.inserted_count == 1  # item-3
    assert result.updated_count == 1  # item-2
    assert result.duplicate_count == 1  # item-1
    assert len(result.errors) == 0

    session = session_factory()
    sig1 = session.query(Signal).filter(Signal.external_id == "item-1").one()
    sig2 = session.query(Signal).filter(Signal.external_id == "item-2").one()

    # Duplicate should update collected_at and run_id but keep updated_at unchanged
    assert sig1.collection_run_id == "run-2"
    assert sig1.collected_at.replace(tzinfo=UTC) == collected_at2
    # Ensure updated_at was not changed since it was a duplicate
    assert sig1.updated_at == sig1.created_at

    # Updated should update everything
    assert sig2.title == "Second Item Updated"
    assert sig2.collection_run_id == "run-2"
    assert sig2.collected_at.replace(tzinfo=UTC) == collected_at2
    session.close()


def test_ingest_mixed_valid_and_invalid(session_factory):
    service = SignalIngestionService(session_factory)
    collected_at = datetime.now(UTC)

    items = [
        # Valid
        RawItem(
            external_id="item-1",
            url="https://github.com/foo/bar",
            title="Valid Item",
            item_type="repository",
        ),
        # Invalid (Empty title)
        RawItem(
            external_id="item-2",
            url="https://github.com/foo/baz",
            title="   ",
            item_type="issue",
        ),
    ]

    result = service.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        raw_items=items,
        collected_at=collected_at,
    )

    # Valid item saved, invalid item recorded as error
    assert result.inserted_count == 1
    assert result.updated_count == 0
    assert result.duplicate_count == 0
    assert len(result.errors) == 1
    assert result.errors[0].code == "empty_title"

    session = session_factory()
    sigs = session.query(Signal).all()
    assert len(sigs) == 1
    assert sigs[0].external_id == "item-1"
    session.close()


def test_ingest_database_failure(session_factory):
    collected_at = datetime.now(UTC)

    items = [
        RawItem(
            external_id="item-1",
            url="https://github.com/foo/bar",
            title="First Item",
            item_type="repository",
        )
    ]

    # Monkeypatch the session maker to raise an operational error on commit
    def failing_session_factory():
        session = session_factory()

        # Mock commit to raise operational error
        def failing_commit():
            raise OperationalError("Mock DB Failure", {}, Exception("Mock Exception"))

        session.commit = failing_commit
        return session

    failing_service = SignalIngestionService(failing_session_factory)
    result = failing_service.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        raw_items=items,
        collected_at=collected_at,
    )

    # Counts should be 0 because transaction rolled back
    assert result.inserted_count == 0
    assert result.updated_count == 0
    assert result.duplicate_count == 0
    assert len(result.errors) == 1
    assert result.errors[0].code == "database_error"
    # Ensure raw SQL or details are not leaked in the message
    assert "Mock DB Failure" not in result.errors[0].message

    # Verify no signal was saved
    session = session_factory()
    assert session.query(Signal).count() == 0
    session.close()


def test_ingest_duplicate_session_coherence(session_factory):
    service = SignalIngestionService(session_factory)
    collected_at1 = datetime.now(UTC) - timedelta(hours=1)

    items = [
        RawItem(
            external_id="item-1",
            url="https://github.com/foo/bar",
            title="First Item",
            item_type="repository",
            metadata={"stargazers_count": 100},
        )
    ]

    # 1. First run: Ingest (inserts new signal)
    service.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-1",
        raw_items=items,
        collected_at=collected_at1,
    )

    # Load the persisted Signal object in a session to get baseline values
    session = session_factory()
    sig = session.query(Signal).filter(Signal.external_id == "item-1").one()
    orig_created_at = sig.created_at
    orig_updated_at = sig.updated_at
    orig_title = sig.title
    orig_excerpt = sig.excerpt
    orig_content_hash = sig.content_hash
    session.close()

    # 2. Second run: Ingest duplicate in a new run, but using the same session instance
    collected_at2 = datetime.now(UTC)

    session2 = session_factory()
    sig_in_session = session2.query(Signal).filter(Signal.external_id == "item-1").one()

    # Intercept session.close() to keep the session alive during assertions
    original_close = session2.close
    session2.close = lambda: None

    # Share the same session instance for ingestion
    def custom_session_maker():
        return session2

    service_same_session = SignalIngestionService(custom_session_maker)
    result = service_same_session.ingest(
        source_id="src-1",
        source_type="github",
        collection_run_id="run-2",
        raw_items=items,
        collected_at=collected_at2,
    )

    assert result.duplicate_count == 1
    assert result.inserted_count == 0
    assert result.updated_count == 0

    # 3. Verify that the loaded sig_in_session in session2 is coherent
    assert sig_in_session.collection_run_id == "run-2"
    assert sig_in_session.collected_at.replace(tzinfo=UTC) == collected_at2
    assert sig_in_session.updated_at == orig_updated_at
    assert sig_in_session.created_at == orig_created_at
    assert sig_in_session.title == orig_title
    assert sig_in_session.excerpt == orig_excerpt
    assert sig_in_session.content_hash == orig_content_hash

    # Clean up the session properly using the original close method
    original_close()
