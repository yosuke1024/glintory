import asyncio

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.collectors.registry import CollectorNotFoundError, CollectorRegistry
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, CollectionRun, Source
from glintory.services.collection import CollectionService
from tests.fakes.collectors import (
    CancelledFakeCollector,
    EmptySuccessfulFakeCollector,
    ExceptionFakeCollector,
    FailedResultFakeCollector,
    PartialFakeCollector,
    SuccessfulFakeCollector,
    WarningFakeCollector,
)


@pytest.fixture
def db_session_factory():
    # Setup test DB
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory


@pytest.fixture
def db_session(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


@pytest.fixture
def registry():
    reg = CollectorRegistry()
    reg.register(SuccessfulFakeCollector("success"))
    reg.register(EmptySuccessfulFakeCollector("empty"))
    reg.register(WarningFakeCollector("warning"))
    reg.register(PartialFakeCollector("partial"))
    reg.register(FailedResultFakeCollector("failed"))
    reg.register(ExceptionFakeCollector(source_type="exception"))
    reg.register(CancelledFakeCollector("cancelled"))
    return reg


@pytest.mark.asyncio
async def test_collection_success(db_session, registry, db_session_factory):
    src = Source(
        name="Github",
        source_type="success",
        enabled=True,
        consecutive_failures=2,
        last_error="Old Error",
    )
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    result = await service.run_source(src.id, max_items=10)

    assert result.status == CollectionRunStatus.SUCCEEDED
    assert result.fetched_count == 2
    assert result.error_count == 0

    # Verify DB state
    db_session.expire_all()
    db_run = db_session.get(CollectionRun, result.run_id)
    assert db_run is not None
    assert db_run.status == CollectionRunStatus.SUCCEEDED
    assert db_run.fetched_count == 2

    db_src = db_session.get(Source, src.id)
    assert db_src is not None
    assert db_src.last_success_at is not None
    assert db_src.consecutive_failures == 0
    assert db_src.last_error is None


@pytest.mark.asyncio
async def test_collection_empty_success(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="empty", enabled=True)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    result = await service.run_source(src.id)

    assert result.status == CollectionRunStatus.SUCCEEDED
    assert result.fetched_count == 0

    db_session.expire_all()
    db_run = db_session.get(CollectionRun, result.run_id)
    assert db_run.status == CollectionRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_collection_warning_success(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="warning", enabled=True)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    result = await service.run_source(src.id)

    # warnings exist but error_count == 0, so SUCCEEDED
    assert result.status == CollectionRunStatus.SUCCEEDED
    assert result.fetched_count == 1
    assert result.warning_count == 1

    db_session.expire_all()
    db_run = db_session.get(CollectionRun, result.run_id)
    assert db_run.status == CollectionRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_collection_partial(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="partial", enabled=True, consecutive_failures=1)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    result = await service.run_source(src.id)

    assert result.status == CollectionRunStatus.PARTIAL
    assert result.fetched_count == 1
    assert result.error_count == 1

    db_session.expire_all()
    db_src = db_session.get(Source, src.id)
    assert db_src.last_success_at is not None
    assert db_src.last_failure_at is not None
    assert db_src.consecutive_failures == 2
    assert db_src.last_error is not None
    assert "Failed to fetch item 2" in db_src.last_error


@pytest.mark.asyncio
async def test_collection_failed_result(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="failed", enabled=True, consecutive_failures=1)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    result = await service.run_source(src.id)

    assert result.status == CollectionRunStatus.FAILED
    assert result.fetched_count == 0
    assert result.error_count == 1

    db_session.expire_all()
    db_src = db_session.get(Source, src.id)
    assert db_src.last_failure_at is not None
    assert db_src.consecutive_failures == 2
    assert db_src.last_error is not None
    assert "Failed to connect to source" in db_src.last_error


@pytest.mark.asyncio
async def test_collection_exception_fatal(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="exception", enabled=True)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    result = await service.run_source(src.id)

    assert result.status == CollectionRunStatus.FAILED
    assert result.error_summary is not None
    assert "Collector fatal exception" in result.error_summary

    db_session.expire_all()
    db_src = db_session.get(Source, src.id)
    assert db_src.last_failure_at is not None
    assert db_src.last_error is not None
    assert "Collector fatal exception" in db_src.last_error
    # No stacktrace should leak
    assert "Traceback" not in db_src.last_error


@pytest.mark.asyncio
async def test_collection_disabled_source(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="success", enabled=False)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    with pytest.raises(ValueError, match="disabled"):
        await service.run_source(src.id)

    # No CollectionRun should be created
    runs = db_session.query(CollectionRun).all()
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_collection_unknown_collector(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="unknown-type", enabled=True)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    with pytest.raises(CollectorNotFoundError):
        await service.run_source(src.id)

    runs = db_session.query(CollectionRun).all()
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_collection_cancellation(db_session, registry, db_session_factory):
    src = Source(name="HN", source_type="cancelled", enabled=True)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)
    with pytest.raises(asyncio.CancelledError):
        await service.run_source(src.id)

    db_session.expire_all()
    # Check that it did transition to failed status and did not stay running
    db_run = db_session.query(CollectionRun).filter_by(source_id=src.id).first()
    assert db_run is not None
    assert db_run.status == CollectionRunStatus.FAILED
    assert db_run.error_summary is not None
    assert "cancelled" in db_run.error_summary.lower()


@pytest.mark.asyncio
async def test_collection_transaction_boundary(
    db_session, registry, db_session_factory
):
    src = Source(name="HN", source_type="exception", enabled=True)
    db_session.add(src)
    db_session.commit()

    # Create a custom session factory to spy on transactions
    class SessionSpy:
        def __init__(self, original_factory):
            self.factory = original_factory
            self.sessions_created = 0

        def __call__(self):
            self.sessions_created += 1
            return self.factory()

    spy_factory = SessionSpy(db_session_factory)
    service = CollectionService(spy_factory, registry)

    result = await service.run_source(src.id)
    assert result.status == CollectionRunStatus.FAILED

    # It must have used at least 2 sessions:
    # 1. Create running CollectionRun
    # 2. Update status to failed
    assert spy_factory.sessions_created >= 2
