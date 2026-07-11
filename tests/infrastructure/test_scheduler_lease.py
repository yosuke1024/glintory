import pytest
from datetime import datetime, UTC, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from glintory.domain.models import Base, SchedulerLease
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository
from glintory.domain.scheduling import SchedulerLeaseLostError

@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()

def test_scheduler_lease_lifecycle(db_session):
    repo = SchedulerLeaseRepository(db_session)

    # 1. First acquire
    assert repo.acquire(owner_token="owner1", lease_seconds=10) is True

    # Check status
    status = repo.get_status()
    assert status["active"] is True
    assert status["heartbeat_at"] is not None
    assert status["lease_expires_at"] is not None

    # Verify timezone-aware
    assert status["lease_expires_at"].tzinfo == UTC

    # 2. Active lease blocks second owner
    assert repo.acquire(owner_token="owner2", lease_seconds=10) is False

    # 3. Same owner renews
    assert repo.acquire(owner_token="owner1", lease_seconds=20) is True
    repo.assert_owned(owner_token="owner1")

    # 4. Wrong owner assert / renew fails
    with pytest.raises(SchedulerLeaseLostError):
        repo.assert_owned(owner_token="owner2")

    with pytest.raises(SchedulerLeaseLostError):
        repo.renew(owner_token="owner2", lease_seconds=10)

    # 5. Wrong owner release does not affect owner1
    repo.release(owner_token="owner2")
    repo.assert_owned(owner_token="owner1")

    # 6. Release by owner1
    repo.release(owner_token="owner1")
    assert repo.get_status()["active"] is False

def test_scheduler_lease_takeover_expired(db_session):
    repo = SchedulerLeaseRepository(db_session)

    # 1. Acquire lease
    assert repo.acquire(owner_token="owner1", lease_seconds=10) is True

    # 2. Expire the lease manually (obeying CHECK constraints)
    lease = db_session.query(SchedulerLease).filter_by(lease_name="default").first()
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=10)
    lease.heartbeat_at = lease.expires_at - timedelta(seconds=1)
    lease.acquired_at = lease.heartbeat_at - timedelta(seconds=1)
    db_session.commit()

    # 3. assert_owned fails because it has expired
    with pytest.raises(SchedulerLeaseLostError):
        repo.assert_owned(owner_token="owner1")

    # 4. Takeover by owner2 succeeds
    assert repo.acquire(owner_token="owner2", lease_seconds=10) is True
    repo.assert_owned(owner_token="owner2")
