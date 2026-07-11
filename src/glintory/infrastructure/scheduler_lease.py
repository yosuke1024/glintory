from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from glintory.domain.models import SchedulerLease
from glintory.domain.scheduling import SchedulerLeaseLostError


class SchedulerLeaseRepository:
    def __init__(
        self, session: Session, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))

    def acquire(self, *, owner_token: str, lease_seconds: int) -> bool:
        now = self.clock()
        expires_at = now + timedelta(seconds=lease_seconds)

        # 1. Check if lease exists
        lease = (
            self.session.query(SchedulerLease).filter_by(lease_name="default").first()
        )

        if not lease:
            # Try to insert the first lease
            try:
                new_lease = SchedulerLease(
                    lease_name="default",
                    owner_token=owner_token,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=expires_at,
                )
                self.session.add(new_lease)
                self.session.flush()
                return True
            except IntegrityError:
                # Concurrent insert collision
                self.session.rollback()
                return False

        # Make sure the loaded expiration timestamp is timezone-aware UTC
        lease_expires = lease.expires_at
        if lease_expires.tzinfo is None:
            lease_expires = lease_expires.replace(tzinfo=UTC)

        # 2. If expired, takeover atomically
        if lease_expires < now:
            stmt = (
                self.session.query(SchedulerLease)
                .filter(SchedulerLease.lease_name == "default")
                .filter(SchedulerLease.owner_token == lease.owner_token)
                .filter(SchedulerLease.expires_at == lease.expires_at)
                .update(
                    {
                        SchedulerLease.owner_token: owner_token,
                        SchedulerLease.acquired_at: now,
                        SchedulerLease.heartbeat_at: now,
                        SchedulerLease.expires_at: expires_at,
                    },
                    synchronize_session=False,
                )
            )
            self.session.flush()
            if stmt > 0:
                self.session.expire_all()
            return stmt > 0

        # 3. If already owned by me, renew
        if lease.owner_token == owner_token:
            lease.heartbeat_at = now
            lease.expires_at = expires_at
            self.session.flush()
            return True

        # 4. Another active lease exists
        return False

    def renew(self, *, owner_token: str, lease_seconds: int) -> None:
        now = self.clock()
        expires_at = now + timedelta(seconds=lease_seconds)

        stmt = (
            self.session.query(SchedulerLease)
            .filter(SchedulerLease.lease_name == "default")
            .filter(SchedulerLease.owner_token == owner_token)
            .update(
                {
                    SchedulerLease.heartbeat_at: now,
                    SchedulerLease.expires_at: expires_at,
                },
                synchronize_session=False,
            )
        )
        self.session.flush()
        if stmt == 0:
            raise SchedulerLeaseLostError(
                "Scheduler lease has been lost or acquired by another process."
            )
        self.session.expire_all()

    def assert_owned(self, *, owner_token: str) -> None:
        lease = (
            self.session.query(SchedulerLease).filter_by(lease_name="default").first()
        )
        if not lease or lease.owner_token != owner_token:
            raise SchedulerLeaseLostError(
                "Scheduler lease is not owned by this runner."
            )

        now = self.clock()
        lease_expires = lease.expires_at
        if lease_expires.tzinfo is None:
            lease_expires = lease_expires.replace(tzinfo=UTC)

        if lease_expires < now:
            raise SchedulerLeaseLostError("Scheduler lease has expired.")

    def release(self, *, owner_token: str) -> None:
        (
            self.session.query(SchedulerLease)
            .filter(SchedulerLease.lease_name == "default")
            .filter(SchedulerLease.owner_token == owner_token)
            .delete(synchronize_session=False)
        )
        self.session.flush()

    def get_status(self) -> dict:
        lease = (
            self.session.query(SchedulerLease).filter_by(lease_name="default").first()
        )
        if not lease:
            return {
                "active": False,
                "heartbeat_at": None,
                "lease_expires_at": None,
            }

        now = self.clock()
        lease_expires = lease.expires_at
        if lease_expires.tzinfo is None:
            lease_expires = lease_expires.replace(tzinfo=UTC)

        is_active = lease_expires >= now

        def to_utc(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt

        return {
            "active": is_active,
            "heartbeat_at": to_utc(lease.heartbeat_at),
            "lease_expires_at": to_utc(lease.expires_at),
        }
