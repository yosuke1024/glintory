import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.models import Base, SchedulerLease
from glintory.domain.scheduling import SchedulerLeaseLostError
from glintory.services.scheduler_runner import SchedulerRunner
from glintory.services.scheduler_service import SchedulerService
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository

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
async def test_scheduler_runner_once_success(db_session_factory, db_session):
    mock_service = MagicMock(spec=SchedulerService)
    mock_service.run_tick = AsyncMock()

    runner = SchedulerRunner(db_session_factory, mock_service, owner_token="owner-1")
    code = await runner.run_once()
    
    assert code == 0
    mock_service.run_tick.assert_called_once_with(owner_token="owner-1")

    # Verify lease is released
    lease = db_session.query(SchedulerLease).filter_by(lease_name="default").first()
    assert lease is None

@pytest.mark.anyio
async def test_scheduler_runner_once_lease_blocked(db_session_factory, db_session):
    # Setup active lease for another owner
    lease_repo = SchedulerLeaseRepository(db_session)
    lease_repo.acquire(owner_token="another-owner", lease_seconds=120)
    db_session.commit()

    mock_service = MagicMock(spec=SchedulerService)
    runner = SchedulerRunner(db_session_factory, mock_service, owner_token="owner-1")
    
    code = await runner.run_once()
    assert code == 6
    mock_service.run_tick.assert_not_called()

@pytest.mark.anyio
async def test_scheduler_runner_continuous_lease_lost(db_session_factory, db_session, monkeypatch):
    # Set config to very small values for testing
    monkeypatch.setattr("glintory.config.settings.scheduler_poll_seconds", 1)
    monkeypatch.setattr("glintory.config.settings.scheduler_heartbeat_seconds", 1)
    monkeypatch.setattr("glintory.config.settings.scheduler_lease_seconds", 3)

    mock_service = MagicMock(spec=SchedulerService)
    # Simulate a lease lost error during tick
    mock_service.run_tick = AsyncMock(side_effect=SchedulerLeaseLostError())

    runner = SchedulerRunner(db_session_factory, mock_service, owner_token="owner-1")
    
    # Run continuous, it should exit with code 7 when lease lost
    # We set poll_seconds = 1, so it will tick soon.
    runner.poll_seconds = 0.1
    runner.heartbeat_seconds = 0.1
    runner.lease_seconds = 0.5

    code = await runner.run_continuous()
    assert code == 7
