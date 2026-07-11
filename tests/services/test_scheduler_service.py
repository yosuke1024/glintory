from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.enums import CollectionRunStatus
from glintory.domain.models import Base, ScheduleExecution, Source, SourceSchedule
from glintory.domain.operations import CollectionTriggerType, SourceAlreadyRunningError
from glintory.domain.scheduling import ScheduleExecutionStatus
from glintory.infrastructure.schedule_execution_repository import (
    ScheduleExecutionRepository,
)
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository
from glintory.services.collection import CollectionExecutionResult, CollectionService
from glintory.services.scheduler_service import SchedulerService


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    yield session_factory
    engine.dispose()


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
    mock_collection.run_source = AsyncMock(
        return_value=CollectionExecutionResult(
            run_id="run-123",
            status=CollectionRunStatus.SUCCEEDED,
            fetched_count=10,
            inserted_count=5,
            updated_count=0,
            duplicate_count=5,
            warning_count=0,
            error_count=0,
            signal_ids=[],
        )
    )

    # Run tick
    service = SchedulerService(db_session_factory, mock_collection)
    result = await service.run_tick(owner_token="owner-1")

    assert result.due_schedule_count == 1
    assert result.claimed_execution_count == 1
    assert result.succeeded_count == 1
    assert len(result.execution_ids) == 1

    # Verify execution history
    exec_record = (
        db_session.query(ScheduleExecution)
        .filter_by(id=result.execution_ids[0])
        .first()
    )
    assert exec_record is not None
    assert exec_record.status == ScheduleExecutionStatus.SUCCEEDED
    assert exec_record.collection_run_id == "run-123"
    assert exec_record.coalesced_count == 0

    # Verify collection service was called with proper trigger type
    mock_collection.run_source.assert_called_once_with(
        src.id, trigger_type=CollectionTriggerType.SCHEDULED
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
        return CollectionExecutionResult(
            run_id="run-456",
            status=CollectionRunStatus.FAILED,
            fetched_count=0,
            inserted_count=0,
            updated_count=0,
            duplicate_count=0,
            warning_count=0,
            error_count=1,
            signal_ids=[],
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


from collections.abc import Mapping
from typing import Any

from glintory.collectors.base import CollectionContext, CollectionResult, RawItem


class FakeSchedulerCollector:
    source_type = "fake_sched"

    def validate_config(self, config: Mapping[str, object]) -> Mapping[str, object]:
        return config

    def get_config_summary(self, config: Mapping[str, Any]) -> str:
        _ = config
        return "fake_summary"

    async def collect(self, context: CollectionContext) -> CollectionResult:
        _ = context
        return CollectionResult(
            items=[
                RawItem(
                    external_id="test",
                    url="https://example.com/test",
                    title="test",
                    excerpt="test",
                    item_type="issue",
                )
            ],
            warnings=(),
            errors=(),
        )


@pytest.mark.anyio
async def test_scheduler_integration_with_real_collection_service(
    db_session_factory, db_session
):
    from glintory.collectors.registry import CollectorRegistry
    from glintory.services.collection import CollectionService

    # Register fake collector
    registry = CollectorRegistry()
    registry.register(FakeSchedulerCollector())

    # 1. Setup Source and Schedule
    src = Source(name="Fake RSS", source_type="fake_sched", enabled=True, config={})
    db_session.add(src)
    db_session.commit()

    sched = SourceSchedule(
        source_id=src.id,
        interval_minutes=60,
        next_run_at=datetime.now(UTC) - timedelta(minutes=10),
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(sched)
    db_session.commit()

    # Acquire lease
    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="owner-real", lease_seconds=120)
    db_session.commit()

    # Create real collection service (using memory DB / engine)
    collection_service = CollectionService(
        session_factory=db_session_factory,
        registry=registry,
    )

    service = SchedulerService(db_session_factory, collection_service)
    result = await service.run_tick(owner_token="owner-real")

    assert result.succeeded_count == 1
    assert len(result.execution_ids) == 1

    # Verify DB states
    from glintory.domain.models import CollectionRun

    db_session.expire_all()

    # ScheduleExecution status = succeeded, collection_run_id = CollectionRun.id
    exec_rec = (
        db_session.query(ScheduleExecution)
        .filter_by(id=result.execution_ids[0])
        .first()
    )
    assert exec_rec is not None
    assert exec_rec.status == ScheduleExecutionStatus.SUCCEEDED
    assert exec_rec.collection_run_id is not None

    run_rec = (
        db_session.query(CollectionRun).filter_by(id=exec_rec.collection_run_id).first()
    )
    assert run_rec is not None
    assert run_rec.trigger_type == "scheduled"
    assert run_rec.status == "succeeded"


@pytest.mark.anyio
async def test_scheduler_claim_rollback_regression(db_session_factory, db_session):
    # Due Claim Transaction regression test
    # Source A: due, executionなし
    # Source B: due, 同じDue Slotのexecutionが既に存在
    src_a = Source(id="src-a", name="Source A", source_type="fake_sched", enabled=True)
    src_b = Source(id="src-b", name="Source B", source_type="fake_sched", enabled=True)
    db_session.add_all([src_a, src_b])
    db_session.commit()

    past_time = datetime.now(UTC) - timedelta(minutes=10)

    # Pre-insert execution for Source B in the exact scheduled_for slot
    # uq_schedule_executions_source_scheduled constraint: (source_id, scheduled_for)
    existing_exec = ScheduleExecution(
        id="existing-b",
        source_id="src-b",
        scheduled_for=past_time,
        started_at=past_time,
        status=ScheduleExecutionStatus.SUCCEEDED.value,
    )
    db_session.add(existing_exec)

    # Schedules for both
    sched_a = SourceSchedule(
        source_id="src-a",
        interval_minutes=60,
        next_run_at=past_time,
        enabled=True,
        created_at=past_time,
        updated_at=past_time,
    )
    sched_b = SourceSchedule(
        source_id="src-b",
        interval_minutes=60,
        next_run_at=past_time,
        enabled=True,
        created_at=past_time,
        updated_at=past_time,
    )
    db_session.add_all([sched_a, sched_b])
    db_session.commit()

    # Acquire lease
    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="owner-rollback-test", lease_seconds=120)
    db_session.commit()

    # Run claim_due_executions
    exec_repo = ScheduleExecutionRepository(db_session)
    claimed = exec_repo.claim_due_executions(
        owner_token="owner-rollback-test",
        max_due=10,
        now=datetime.now(UTC),
    )
    db_session.commit()

    # Verify claims
    # Source A claimed, Source B duplicate DO NOTHING (not claimed)
    assert len(claimed) == 1
    assert claimed[0].source_id == "src-a"

    # Verify Source A execution exists in DB
    db_session.expire_all()
    exec_a = db_session.query(ScheduleExecution).filter_by(source_id="src-a").first()
    assert exec_a is not None

    # Verify next_run_at is advanced for BOTH A and B
    sched_a_db = db_session.query(SourceSchedule).filter_by(source_id="src-a").first()
    sched_b_db = db_session.query(SourceSchedule).filter_by(source_id="src-b").first()
    assert sched_a_db is not None
    assert sched_b_db is not None

    sched_a_next = (
        sched_a_db.next_run_at.replace(tzinfo=UTC)
        if sched_a_db.next_run_at.tzinfo is None
        else sched_a_db.next_run_at
    )
    sched_b_next = (
        sched_b_db.next_run_at.replace(tzinfo=UTC)
        if sched_b_db.next_run_at.tzinfo is None
        else sched_b_db.next_run_at
    )

    assert sched_a_next > past_time
    assert sched_b_next > past_time


@pytest.mark.anyio
async def test_scheduler_security_sanitization_regression(
    db_session_factory, db_session
):
    # Throw error containing sensitive information and verify sanitization
    src = Source(name="Test RSS", source_type="rss", enabled=True)
    db_session.add(src)
    db_session.commit()

    sched = SourceSchedule(
        source_id=src.id,
        interval_minutes=60,
        next_run_at=datetime.now(UTC) - timedelta(minutes=10),
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(sched)
    db_session.commit()

    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="owner-sec-test", lease_seconds=120)
    db_session.commit()

    mock_collection = MagicMock(spec=CollectionService)
    # Raising error with secrets
    mock_collection.run_source = AsyncMock(
        side_effect=ValueError(
            "token=secret Authorization: Bearer secret sqlite:///private.db SELECT * FROM secrets"
        )
    )

    service = SchedulerService(db_session_factory, mock_collection)
    result = await service.run_tick(owner_token="owner-sec-test")

    # Check that warnings do not contain secrets
    for warning in result.warnings:
        assert "secret" not in warning
        assert "Bearer" not in warning
        assert "sqlite://" not in warning
        assert "SELECT" not in warning

    # Check that error_summary stored in DB is sanitized
    db_session.expire_all()
    exec_rec = (
        db_session.query(ScheduleExecution)
        .filter_by(id=result.execution_ids[0])
        .first()
    )
    assert exec_rec is not None
    assert "token=secret" not in exec_rec.error_summary
    assert "Bearer secret" not in exec_rec.error_summary
    assert "Scheduled collection failed unexpectedly." in exec_rec.error_summary
