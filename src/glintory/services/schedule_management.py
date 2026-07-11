import datetime as dt
from datetime import UTC, datetime, timedelta
from typing import Callable
from sqlalchemy.orm import Session
from glintory.domain.models import Source, SourceSchedule
from glintory.domain.scheduling import (
    SourceScheduleItem,
    ScheduleNotFoundError,
    InvalidScheduleError,
)
from glintory.domain.operations import SourceNotFoundError
from glintory.infrastructure.schedule_repository import ScheduleRepository
from glintory.config import settings

class ScheduleManagementService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def list_schedules(
        self,
        *,
        enabled: bool | None = None,
        source_name_filter: str | None = None,
    ) -> list[SourceScheduleItem]:
        session = self.session_factory()
        try:
            repo = ScheduleRepository(session)
            return list(repo.list_schedules(enabled=enabled, source_name_filter=source_name_filter))
        finally:
            session.close()

    def get_schedule(self, source_id: str) -> SourceScheduleItem:
        session = self.session_factory()
        try:
            repo = ScheduleRepository(session)
            item = repo.get_schedule_detail(source_id)
            if not item:
                raise ScheduleNotFoundError(f"Schedule for Source {source_id} not found.")
            return item
        finally:
            session.close()

    def set_schedule(
        self,
        *,
        source_id: str,
        interval_minutes: int,
        first_run_at: datetime | None = None,
        enabled: bool = True,
    ) -> SourceScheduleItem:
        # Validate interval limits in settings
        min_i = settings.schedule_min_interval_minutes
        max_i = settings.schedule_max_interval_minutes
        if not (min_i <= interval_minutes <= max_i):
            raise InvalidScheduleError(f"interval_minutes must be between {min_i} and {max_i} minutes.")

        now = datetime.now(UTC)

        if first_run_at is not None:
            if first_run_at.tzinfo is None or first_run_at.tzinfo.utcoffset(first_run_at) != timedelta(0):
                raise InvalidScheduleError("first_run_at must be timezone-aware UTC datetime.")
            if first_run_at < now:
                raise InvalidScheduleError("first_run_at cannot be in the past.")
            next_run_at = first_run_at
        else:
            next_run_at = now + timedelta(minutes=interval_minutes)

        session = self.session_factory()
        try:
            # Verify source existence
            source = session.get(Source, source_id)
            if not source:
                raise SourceNotFoundError(f"Source with ID {source_id} not found.")

            repo = ScheduleRepository(session)
            sched = repo.get_schedule(source_id)
            if sched:
                sched.interval_minutes = interval_minutes
                sched.next_run_at = next_run_at
                sched.enabled = enabled
                sched.updated_at = now
            else:
                sched = SourceSchedule(
                    source_id=source_id,
                    interval_minutes=interval_minutes,
                    next_run_at=next_run_at,
                    enabled=enabled,
                    created_at=now,
                    updated_at=now,
                )
            repo.save_schedule(sched)
            session.commit()

            item = repo.get_schedule_detail(source_id)
            assert item is not None
            return item
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def enable_schedule(self, source_id: str) -> SourceScheduleItem:
        session = self.session_factory()
        try:
            repo = ScheduleRepository(session)
            sched = repo.get_schedule(source_id)
            if not sched:
                raise ScheduleNotFoundError(f"Schedule for Source {source_id} not found.")

            now = datetime.now(UTC)
            sched.enabled = True
            next_run = sched.next_run_at
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=UTC)
            if next_run <= now:
                sched.next_run_at = now + timedelta(minutes=sched.interval_minutes)
            sched.updated_at = now

            session.commit()

            item = repo.get_schedule_detail(source_id)
            assert item is not None
            return item
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def disable_schedule(self, source_id: str) -> SourceScheduleItem:
        session = self.session_factory()
        try:
            repo = ScheduleRepository(session)
            sched = repo.get_schedule(source_id)
            if not sched:
                raise ScheduleNotFoundError(f"Schedule for Source {source_id} not found.")

            sched.enabled = False
            sched.updated_at = datetime.now(UTC)
            session.commit()

            item = repo.get_schedule_detail(source_id)
            assert item is not None
            return item
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
