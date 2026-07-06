import asyncio

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.collectors.base import (
    CollectionError,
    CollectionResult,
    Collector,
    RawItem,
)
from glintory.collectors.registry import CollectorNotFoundError, CollectorRegistry
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, CollectionRun, Signal, Source
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


@pytest.mark.asyncio
async def test_collection_persistence_integration(
    db_session, registry, db_session_factory
):
    src = Source(name="GithubPersistence", source_type="success", enabled=True)
    db_session.add(src)
    db_session.commit()

    service = CollectionService(db_session_factory, registry)

    # First execution (Ingest / Insert)
    result = await service.run_source(src.id, max_items=10)
    assert result.status == CollectionRunStatus.SUCCEEDED
    assert result.fetched_count == 2
    assert result.inserted_count == 2
    assert result.updated_count == 0
    assert result.duplicate_count == 0
    assert len(result.signal_ids) == 2

    # Verify DB Signal entries
    db_session.expire_all()
    sigs = db_session.query(Signal).filter(Signal.source_id == src.id).all()
    assert len(sigs) == 2
    assert {sig.external_id for sig in sigs} == {"1", "2"}

    # Second execution (Duplicates)
    result2 = await service.run_source(src.id, max_items=10)
    assert result2.status == CollectionRunStatus.SUCCEEDED
    assert result2.fetched_count == 2
    assert result2.inserted_count == 0
    assert result2.updated_count == 0
    assert result2.duplicate_count == 2


@pytest.mark.asyncio
async def test_collection_status_duplicate_and_invalid(db_session, db_session_factory):
    # Setup source
    src = Source(name="DupInvalid", source_type="dup_invalid", enabled=True)
    db_session.add(src)
    db_session.commit()

    # Collector that yields 1 item first, then 1 duplicate and 1 invalid item
    class DynamicCollector(Collector):
        source_type = "dup_invalid"

        def __init__(self):
            self.call_count = 0

        def validate_config(self, config):
            return config

        def get_config_summary(self, config):
            _ = config
            return "Dynamic config summary"

        async def collect(self, _context):
            self.call_count += 1
            if self.call_count == 1:
                return CollectionResult(
                    items=[
                        RawItem(
                            external_id="1",
                            url="http://example.com/1",
                            title="Item 1",
                            item_type="repository",
                        )
                    ],
                    warnings=(),
                    errors=(),
                )
            return CollectionResult(
                items=[
                    RawItem(
                        external_id="1",
                        url="http://example.com/1",
                        title="Item 1",
                        item_type="repository",
                    ),
                    RawItem(
                        external_id="2",
                        url="http://example.com/2",
                        title="",
                        item_type="repository",
                    ),  # invalid title -> normalization error
                ],
                warnings=(),
                errors=(),
            )

    reg = CollectorRegistry()
    reg.register(DynamicCollector())

    service = CollectionService(db_session_factory, reg)
    # Run 1: Ingests item 1 (Inserted)
    await service.run_source(src.id)

    # Run 2: Item 1 is duplicate, Item 2 is invalid (Normalization error)
    result = await service.run_source(src.id)

    # Assert status is PARTIAL
    assert result.status == CollectionRunStatus.PARTIAL
    assert result.duplicate_count == 1
    assert result.error_count == 1
    assert result.inserted_count == 0


@pytest.mark.asyncio
async def test_collection_status_all_invalid(db_session, db_session_factory):
    src = Source(name="AllInvalid", source_type="all_invalid", enabled=True)
    db_session.add(src)
    db_session.commit()

    class InvalidCollector(Collector):
        source_type = "all_invalid"

        def validate_config(self, config):
            return config

        def get_config_summary(self, config):
            _ = config
            return "Invalid config summary"

        async def collect(self, _context):
            return CollectionResult(
                items=[
                    RawItem(
                        external_id="1",
                        url="http://example.com/1",
                        title="",
                        item_type="repository",
                    ),  # invalid title -> normalization error
                    RawItem(
                        external_id="2",
                        url="http://example.com/2",
                        title="Item 2",
                        item_type="unsupported",
                    ),  # invalid type -> unsupported_item_type error
                ],
                warnings=(),
                errors=(),
            )

    reg = CollectorRegistry()
    reg.register(InvalidCollector())

    service = CollectionService(db_session_factory, reg)
    result = await service.run_source(src.id)

    assert result.status == CollectionRunStatus.FAILED
    assert result.error_count == 2
    assert result.inserted_count == 0
    assert result.duplicate_count == 0


@pytest.mark.asyncio
async def test_collection_status_updated_and_collector_error(
    db_session, db_session_factory
):
    src = Source(name="UpdateCollectorError", source_type="update_error", enabled=True)
    db_session.add(src)
    db_session.commit()

    # Collector that yields 1 item first, then returns updated title for that item + 1 collector error
    class DynamicUpdateErrorCollector(Collector):
        source_type = "update_error"

        def __init__(self):
            self.call_count = 0

        def validate_config(self, config):
            return config

        def get_config_summary(self, config):
            _ = config
            return "Update error config summary"

        async def collect(self, _context):
            self.call_count += 1
            if self.call_count == 1:
                return CollectionResult(
                    items=[
                        RawItem(
                            external_id="1",
                            url="http://example.com/1",
                            title="Original Title",
                            item_type="repository",
                        ),
                    ],
                    warnings=(),
                    errors=(),
                )
            return CollectionResult(
                items=[
                    RawItem(
                        external_id="1",
                        url="http://example.com/1",
                        title="New Title",
                        item_type="repository",
                    ),
                ],
                warnings=(),
                errors=[
                    CollectionError(
                        code="fetch_failed",
                        message="Failed to fetch item 2",
                        retryable=False,
                    )
                ],
            )

    reg = CollectorRegistry()
    reg.register(DynamicUpdateErrorCollector())

    service = CollectionService(db_session_factory, reg)
    # Run 1: Insert item 1
    await service.run_source(src.id)

    # Run 2: Update item 1 and trigger collector error
    result = await service.run_source(src.id)

    # Assert status is PARTIAL because 1 update succeeded and 1 collector error occurred
    assert result.status == CollectionRunStatus.PARTIAL
    assert result.updated_count == 1
    assert result.error_count == 1
