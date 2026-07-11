from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, CollectionRun, Source
from glintory.infrastructure.repositories import (
    CollectionRunRepository,
    SourceRepository,
)


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
    yield session
    session.close()


def test_source_repository_get_by_id(db_session):
    src = Source(name="HN", source_type="hackernews", enabled=True)
    db_session.add(src)
    db_session.commit()

    repo = SourceRepository(db_session)
    fetched = repo.get_by_id(src.id)
    assert fetched is not None
    assert fetched.name == "HN"


def test_source_repository_get_enabled_by_id(db_session):
    src_enabled = Source(name="HN1", source_type="hackernews", enabled=True)
    src_disabled = Source(name="HN2", source_type="hackernews", enabled=False)
    db_session.add_all([src_enabled, src_disabled])
    db_session.commit()

    repo = SourceRepository(db_session)
    assert repo.get_enabled_by_id(src_enabled.id) is not None
    assert repo.get_enabled_by_id(src_disabled.id) is None


def test_source_repository_record_success(db_session):
    src = Source(
        name="HN",
        source_type="hackernews",
        consecutive_failures=3,
        last_error="Old Error",
    )
    db_session.add(src)
    db_session.commit()

    repo = SourceRepository(db_session)
    now = datetime.now(UTC)
    repo.record_success(src.id, now)
    db_session.commit()
    db_session.expire_all()
    updated = repo.get_by_id(src.id)
    assert updated is not None
    assert updated.last_success_at is not None
    assert updated.last_success_at.replace(tzinfo=UTC) == now
    assert updated.consecutive_failures == 0
    assert updated.last_error is None


def test_source_repository_record_partial(db_session):
    src = Source(name="HN", source_type="hackernews", consecutive_failures=1)
    db_session.add(src)
    db_session.commit()

    repo = SourceRepository(db_session)
    success_at = datetime.now(UTC)
    failure_at = datetime.now(UTC)
    repo.record_partial(src.id, success_at, failure_at, "Some items failed")
    db_session.commit()
    db_session.expire_all()
    updated = repo.get_by_id(src.id)
    assert updated is not None
    assert updated.last_success_at is not None
    assert updated.last_success_at.replace(tzinfo=UTC) == success_at
    assert updated.last_failure_at is not None
    assert updated.last_failure_at.replace(tzinfo=UTC) == failure_at
    assert updated.consecutive_failures == 2
    assert updated.last_error == "Some items failed"


def test_source_repository_record_failure(db_session):
    src = Source(name="HN", source_type="hackernews", consecutive_failures=1)
    db_session.add(src)
    db_session.commit()

    repo = SourceRepository(db_session)
    failure_at = datetime.now(UTC)
    repo.record_failure(src.id, failure_at, "Connection lost")
    db_session.commit()
    db_session.expire_all()
    updated = repo.get_by_id(src.id)
    assert updated is not None
    assert updated.last_success_at is None
    assert updated.last_failure_at is not None
    assert updated.last_failure_at.replace(tzinfo=UTC) == failure_at
    assert updated.consecutive_failures == 2
    assert updated.last_error == "Connection lost"


def test_collection_run_repository_create_running(db_session):
    src = Source(name="HN", source_type="hackernews")
    db_session.add(src)
    db_session.commit()

    repo = CollectionRunRepository(db_session)
    run = repo.create_running(src.id)

    assert run.id is not None
    assert run.source_id == src.id
    assert run.status == CollectionRunStatus.RUNNING
    assert run.started_at is not None


def test_collection_run_repository_finish_succeeded(db_session):
    src = Source(name="HN", source_type="hackernews")
    db_session.add(src)
    db_session.commit()

    repo = CollectionRunRepository(db_session)
    run = repo.create_running(src.id)

    completed_at = datetime.now(UTC)
    repo.finish_succeeded(
        run.id,
        completed_at,
        fetched_count=5,
        inserted_count=2,
        updated_count=1,
        duplicate_count=2,
        warning_count=2,
    )
    db_session.commit()
    db_session.expire_all()
    updated = db_session.get(CollectionRun, run.id)
    assert updated is not None
    assert updated.status == CollectionRunStatus.SUCCEEDED
    assert updated.completed_at is not None
    assert updated.completed_at.replace(tzinfo=UTC) == completed_at
    assert updated.fetched_count == 5
    assert updated.inserted_count == 2
    assert updated.updated_count == 1
    assert updated.duplicate_count == 2
    assert updated.warning_count == 2
    assert updated.error_count == 0


def test_collection_run_repository_finish_partial(db_session):
    src = Source(name="HN", source_type="hackernews")
    db_session.add(src)
    db_session.commit()

    repo = CollectionRunRepository(db_session)
    run = repo.create_running(src.id)

    completed_at = datetime.now(UTC)
    repo.finish_partial(
        run.id,
        completed_at,
        fetched_count=10,
        inserted_count=3,
        updated_count=2,
        duplicate_count=5,
        warning_count=1,
        error_count=3,
        error_summary="Minor errors",
    )
    db_session.commit()
    db_session.expire_all()
    updated = db_session.get(CollectionRun, run.id)
    assert updated is not None
    assert updated.status == CollectionRunStatus.PARTIAL
    assert updated.completed_at is not None
    assert updated.completed_at.replace(tzinfo=UTC) == completed_at
    assert updated.fetched_count == 10
    assert updated.inserted_count == 3
    assert updated.updated_count == 2
    assert updated.duplicate_count == 5
    assert updated.warning_count == 1
    assert updated.error_count == 3
    assert updated.error_summary == "Minor errors"


def test_collection_run_repository_finish_failed(db_session):
    src = Source(name="HN", source_type="hackernews")
    db_session.add(src)
    db_session.commit()

    repo = CollectionRunRepository(db_session)
    run = repo.create_running(src.id)

    completed_at = datetime.now(UTC)
    repo.finish_failed(
        run_id=run.id,
        completed_at=completed_at,
        error_summary="Fatal exception occurred",
    )
    db_session.commit()
    db_session.expire_all()
    updated = db_session.get(CollectionRun, run.id)
    assert updated is not None
    assert updated.status == CollectionRunStatus.FAILED
    assert updated.completed_at is not None
    assert updated.completed_at.replace(tzinfo=UTC) == completed_at
    assert updated.error_summary == "Fatal exception occurred"
    assert updated.error_count == 1


def test_list_collection_runs_n_plus_one(db_session):
    from glintory.infrastructure.source_operations import SourceOperationsRepository

    # Create multiple sources
    srcs = [Source(name=f"Src {i}", source_type=f"type_{i}") for i in range(10)]
    db_session.add_all(srcs)
    db_session.commit()

    # Create 100 runs
    runs = []
    for i in range(100):
        src = srcs[i % 10]
        run = CollectionRun(
            source_id=src.id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        runs.append(run)
    db_session.add_all(runs)
    db_session.commit()

    queries = []
    engine = db_session.bind

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        queries.append(statement)

    repo = SourceOperationsRepository(db_session)

    queries.clear()

    items, total = repo.list_collection_runs(limit=50)

    event.remove(engine, "before_cursor_execute", before_cursor_execute)

    assert total == 100
    assert len(items) == 50
    # Queries should be exactly 2: 1 for count, 1 for runs + source details
    assert len(queries) == 2
