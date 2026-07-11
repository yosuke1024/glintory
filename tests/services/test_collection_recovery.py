from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from glintory.collectors.base import CollectionResult
from glintory.collectors.registry import CollectorRegistry
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, CollectionRun, Source
from glintory.domain.operations import (
    CollectionTriggerType,
    SourceAlreadyRunningError,
    SourceDisabledError,
    SourceNotFoundError,
)
from glintory.services.collection import CollectionService


@pytest.fixture
def db_session_factory():
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
    return CollectorRegistry()


@pytest.fixture
def mock_collector():
    collector = AsyncMock()
    collector.collect.return_value = CollectionResult(
        items=[], warnings=[], errors=[]
    )
    collector.get_config_summary.return_value = {"feeds": "test"}
    collector.source_type = "test_type"
    return collector


@pytest.mark.asyncio
async def test_collection_stale_run_recovery(
    db_session, registry, db_session_factory, mock_collector
):
    registry.register(mock_collector)

    src = Source(
        name="test-source",
        source_type="test_type",
        enabled=True,
        config={"max_items": 10},
    )
    db_session.add(src)
    db_session.commit()

    now = datetime.now(UTC)
    stale_started = now - timedelta(minutes=90)
    stale_run = CollectionRun(
        source_id=src.id,
        status=CollectionRunStatus.RUNNING,
        started_at=stale_started,
        trigger_type=CollectionTriggerType.CLI,
    )
    db_session.add(stale_run)
    db_session.commit()

    service = CollectionService(
        db_session_factory,
        registry,
        clock=lambda: now,
    )
    res = await service.run_source(src.id, trigger_type=CollectionTriggerType.WEB)
    assert res.status == CollectionRunStatus.SUCCEEDED

    db_session.expire_all()
    recovered_run = db_session.get(CollectionRun, stale_run.id)
    assert recovered_run.status == CollectionRunStatus.ABANDONED
    
    completed_at = recovered_run.completed_at
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    assert completed_at == now
    assert "abandoned" in recovered_run.error_summary.lower()


@pytest.mark.asyncio
async def test_collection_concurrent_run_conflict(
    db_session, registry, db_session_factory, mock_collector
):
    registry.register(mock_collector)

    src = Source(
        name="test-source",
        source_type="test_type",
        enabled=True,
        config={"max_items": 10},
    )
    db_session.add(src)
    db_session.commit()

    now = datetime.now(UTC)
    active_started = now - timedelta(minutes=10)
    active_run = CollectionRun(
        source_id=src.id,
        status=CollectionRunStatus.RUNNING,
        started_at=active_started,
        trigger_type=CollectionTriggerType.CLI,
    )
    db_session.add(active_run)
    db_session.commit()

    service = CollectionService(
        db_session_factory,
        registry,
        clock=lambda: now,
    )
    with pytest.raises(SourceAlreadyRunningError):
        await service.run_source(src.id)
