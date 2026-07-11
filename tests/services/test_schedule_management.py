import pytest
from datetime import datetime, UTC, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.models import Base, Source, ScheduleExecution
from glintory.domain.scheduling import (
    ScheduleNotFoundError,
    InvalidScheduleError,
    ScheduleExecutionStatus,
)
from glintory.domain.operations import SourceNotFoundError
from glintory.services.schedule_management import ScheduleManagementService
from glintory.infrastructure.schedule_repository import ScheduleRepository

@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session, session_factory
    session.close()

def test_schedule_management_lifecycle(db_session):
    session, session_factory = db_session
    service = ScheduleManagementService(session_factory)

    # 1. Create a source
    src = Source(name="HN", source_type="hackernews")
    session.add(src)
    session.commit()

    # 2. Source not found
    with pytest.raises(SourceNotFoundError):
        service.set_schedule(source_id="invalid-id", interval_minutes=60)

    # 3. Create schedule
    item = service.set_schedule(source_id=src.id, interval_minutes=60, enabled=True)
    assert item.source_id == src.id
    assert item.interval_minutes == 60
    assert item.schedule_enabled is True
    assert (item.next_run_at - datetime.now(UTC)) < timedelta(minutes=61)

    # 4. Get schedule
    fetched = service.get_schedule(src.id)
    assert fetched.source_id == src.id
    assert fetched.interval_minutes == 60

    # 5. Update schedule
    item_up = service.set_schedule(source_id=src.id, interval_minutes=120, enabled=True)
    assert item_up.interval_minutes == 120

    # 6. Disable schedule
    item_dis = service.disable_schedule(src.id)
    assert item_dis.schedule_enabled is False

    # 7. Enable schedule (since next_run_at might still be in the future, it shouldn't recalculate unless next_run_at <= now)
    # Let's force next_run_at to the past
    session.close()
    session = session_factory()
    repo = ScheduleRepository(session)
    sched = repo.get_schedule(src.id)
    sched.next_run_at = datetime.now(UTC) - timedelta(minutes=10)
    session.commit()
    session.close()

    # Enable and check if next_run_at has been forwarded
    item_en = service.enable_schedule(src.id)
    assert item_en.schedule_enabled is True
    assert item_en.next_run_at > datetime.now(UTC)

def test_schedule_management_validation(db_session):
    session, session_factory = db_session
    service = ScheduleManagementService(session_factory)

    src = Source(name="HN", source_type="hackernews")
    session.add(src)
    session.commit()

    # Invalid interval
    with pytest.raises(InvalidScheduleError):
        service.set_schedule(source_id=src.id, interval_minutes=4)  # settings.schedule_min_interval_minutes is 15

    # Naive first_run_at
    with pytest.raises(InvalidScheduleError):
        service.set_schedule(source_id=src.id, interval_minutes=60, first_run_at=datetime(2026, 7, 12, 0, 0))

    # Past first_run_at
    with pytest.raises(InvalidScheduleError):
        past_time = datetime.now(UTC) - timedelta(minutes=10)
        service.set_schedule(source_id=src.id, interval_minutes=60, first_run_at=past_time)

def test_schedule_management_history_retained(db_session):
    session, session_factory = db_session
    service = ScheduleManagementService(session_factory)

    src = Source(name="HN", source_type="hackernews")
    session.add(src)
    session.commit()

    # Create schedule
    service.set_schedule(source_id=src.id, interval_minutes=60)

    # Insert execution history
    exec_history = ScheduleExecution(
        id="exec-1",
        source_id=src.id,
        scheduled_for=datetime.now(UTC),
        started_at=datetime.now(UTC),
        status=ScheduleExecutionStatus.SUCCEEDED,
    )
    session.add(exec_history)
    session.commit()

    # Update schedule config
    service.set_schedule(source_id=src.id, interval_minutes=120)

    # Verify execution history still exists
    assert session.query(ScheduleExecution).filter_by(id="exec-1").count() == 1
