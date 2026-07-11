import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.models import Base, Source, SourceSchedule, ScheduleExecution
from glintory.domain.scheduling import ScheduleExecutionStatus, SchedulerLeaseLostError
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.operations import SourceAlreadyRunningError, ManualCollectionResult, CollectionTriggerType
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository
from glintory.infrastructure.schedule_execution_repository import ScheduleExecutionRepository
from glintory.services.scheduler_service import SchedulerService
from glintory.services.collection import CollectionService

@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory

@pytest.fixture
def db_session(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()

@pytest.mark.anyio
async def test_scheduler_service_tick_succeeded(db_session_factory, db_session):
    # Setup source and schedule
    src = Source(name="Test RSS", source_type="rss", enabled=True)
    db_session.add(src)
    db_session.commit()

    # Schedule: next_run_at in the past
    sched = SourceSchedule(
        source_id=src.id,
        interval_minutes=60,
        next_run_at=datetime.now(UTC) - timedelta(minutes=10),
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(sched)

    # Acquire lease
    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="owner-1", lease_seconds=120)
    db_session.commit()

    # Mock collection service
    mock_collection = MagicMock(spec=CollectionService)
    # run_source is an async method
    mock_collection.run_source = AsyncMock(return_value=ManualCollectionResult(
        source_id=src.id,
        source_name="Test RSS",
        collection_run_id="run-123",
        status=CollectionRunStatus.SUCCEEDED,
        fetched_count=10,
        inserted_count=5,
        updated_count=0,
        duplicate_count=5,
        warning_count=0,
        error_count=0
    ))

    # Run tick
    service = SchedulerService(db_session_factory, mock_collection)
    result = await service.run_tick(owner_token="owner-1")

    assert result.due_schedule_count == 1
    assert result.claimed_execution_count == 1
    assert result.succeeded_count == 1
    assert len(result.execution_ids) == 1

    # Verify execution history
    exec_record = db_session.query(ScheduleExecution).filter_by(id=result.execution_ids[0]).first()
    assert exec_record is not None
    assert exec_record.status == ScheduleExecutionStatus.SUCCEEDED
    assert exec_record.collection_run_id == "run-123"
    assert exec_record.coalesced_count == 0

    # Verify collection service was called with proper trigger type
    mock_collection.run_source.assert_called_once_with(
        src.id,
        trigger_type=CollectionTriggerType.SCHEDULED
    )

@pytest.mark.anyio
async def test_scheduler_service_tick_busy_and_failed(db_session_factory, db_session):
    # Two sources
    src1 = Source(name="Src 1", source_type="rss", enabled=True)
    src2 = Source(name="Src 2", source_type="rss", enabled=True)
    db_session.add_all([src1, src2])
    db_session.commit()

    # Schedules due
    sched1 = SourceSchedule(
        source_id=src1.id,
        interval_minutes=60,
        next_run_at=datetime.now(UTC) - timedelta(minutes=5),
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    sched2 = SourceSchedule(
        source_id=src2.id,
        interval_minutes=60,
        next_run_at=datetime.now(UTC) - timedelta(minutes=5),
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add_all([sched1, sched2])

    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="owner-1", lease_seconds=120)
    db_session.commit()

    # Mock collection service: Src 1 throws SourceAlreadyRunningError, Src 2 fails
    mock_collection = MagicMock(spec=CollectionService)
    
    async def mock_run_source(source_id, trigger_type):
        if source_id == src1.id:
            raise SourceAlreadyRunningError()
        else:
            return ManualCollectionResult(
                source_id=source_id,
                source_name="Src 2",
                collection_run_id="run-456",
                status=CollectionRunStatus.FAILED,
                fetched_count=0, inserted_count=0, updated_count=0, duplicate_count=0, warning_count=0, error_count=1
            )
            
    mock_collection.run_source = mock_run_source

    service = SchedulerService(db_session_factory, mock_collection)
    result = await service.run_tick(owner_token="owner-1")

    assert result.due_schedule_count == 2
    assert result.skipped_busy_count == 1
    assert result.failed_count == 1

@pytest.mark.anyio
async def test_stale_execution_recovery(db_session_factory, db_session):
    # Setup source
    src = Source(name="Test RSS", source_type="rss", enabled=True)
    db_session.add(src)
    db_session.commit()

    # Insert a stale running execution manually
    stale_time = datetime.now(UTC) - timedelta(minutes=70)
    stale_exec = ScheduleExecution(
        id="stale-1",
        source_id=src.id,
        scheduled_for=stale_time,
        started_at=stale_time,
        status=ScheduleExecutionStatus.RUNNING,
    )
    db_session.add(stale_exec)

    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="owner-1", lease_seconds=120)
    db_session.commit()

    mock_collection = MagicMock(spec=CollectionService)
    service = SchedulerService(db_session_factory, mock_collection)
    
    # Run tick
    await service.run_tick(owner_token="owner-1")

    # Verify stale execution was recovered to abandoned status
    db_session.expire_all()
    recovered = db_session.query(ScheduleExecution).filter_by(id="stale-1").first()
    assert recovered.status == ScheduleExecutionStatus.ABANDONED
    assert recovered.completed_at is not None
    assert "exceeding the stale threshold" in recovered.error_summary
