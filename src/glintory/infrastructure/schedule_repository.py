from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from glintory.domain.models import ScheduleExecution, Source, SourceSchedule
from glintory.domain.scheduling import (
    ScheduleExecutionStatus,
    ScheduleNotFoundError,
    SourceScheduleItem,
)


class ScheduleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_schedules(
        self,
        *,
        enabled: bool | None = None,
        source_name_filter: str | None = None,
    ) -> Sequence[SourceScheduleItem]:
        rn_sub = self.session.query(
            ScheduleExecution.id.label("exec_id"),
            ScheduleExecution.source_id.label("source_id"),
            func.row_number()
            .over(
                partition_by=ScheduleExecution.source_id,
                order_by=(
                    ScheduleExecution.started_at.desc(),
                    ScheduleExecution.id.desc(),
                ),
            )
            .label("rn"),
        ).subquery()

        query = (
            self.session.query(Source, SourceSchedule, ScheduleExecution)
            .join(SourceSchedule, Source.id == SourceSchedule.source_id)
            .outerjoin(rn_sub, and_(Source.id == rn_sub.c.source_id, rn_sub.c.rn == 1))
            .outerjoin(ScheduleExecution, ScheduleExecution.id == rn_sub.c.exec_id)
        )

        if enabled is not None:
            query = query.filter(SourceSchedule.enabled == enabled)
        if source_name_filter:
            query = query.filter(Source.name.icontains(source_name_filter))

        query = query.order_by(
            SourceSchedule.next_run_at.asc(),
            Source.name.asc(),
            Source.id.asc(),
        )

        results = query.all()
        items = []
        for src, sched, exec_hist in results:

            def to_utc(dt: datetime | None) -> datetime | None:
                if dt is None:
                    return None
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=UTC)
                return dt

            def to_utc_non_optional(dt: datetime) -> datetime:
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=UTC)
                return dt

            status = None
            last_exec_at = None
            if exec_hist:
                status = ScheduleExecutionStatus(exec_hist.status)
                last_exec_at = to_utc(exec_hist.started_at)

            items.append(
                SourceScheduleItem(
                    source_id=src.id,
                    source_name=src.name,
                    source_type=src.source_type,
                    source_enabled=src.enabled,
                    schedule_enabled=sched.enabled,
                    interval_minutes=sched.interval_minutes,
                    next_run_at=to_utc_non_optional(sched.next_run_at),
                    last_execution_status=status,
                    last_execution_at=last_exec_at,
                    created_at=to_utc_non_optional(sched.created_at),
                    updated_at=to_utc_non_optional(sched.updated_at),
                )
            )
        return items

    def get_schedule_detail(self, source_id: str) -> SourceScheduleItem | None:
        rn_sub = self.session.query(
            ScheduleExecution.id.label("exec_id"),
            ScheduleExecution.source_id.label("source_id"),
            func.row_number()
            .over(
                partition_by=ScheduleExecution.source_id,
                order_by=(
                    ScheduleExecution.started_at.desc(),
                    ScheduleExecution.id.desc(),
                ),
            )
            .label("rn"),
        ).subquery()

        result = (
            self.session.query(Source, SourceSchedule, ScheduleExecution)
            .join(SourceSchedule, Source.id == SourceSchedule.source_id)
            .outerjoin(rn_sub, and_(Source.id == rn_sub.c.source_id, rn_sub.c.rn == 1))
            .outerjoin(ScheduleExecution, ScheduleExecution.id == rn_sub.c.exec_id)
            .filter(Source.id == source_id)
            .first()
        )

        if not result:
            return None

        src, sched, exec_hist = result

        def to_utc(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt

        def to_utc_non_optional(dt: datetime) -> datetime:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt

        status = None
        last_exec_at = None
        if exec_hist:
            status = ScheduleExecutionStatus(exec_hist.status)
            last_exec_at = to_utc(exec_hist.started_at)

        return SourceScheduleItem(
            source_id=src.id,
            source_name=src.name,
            source_type=src.source_type,
            source_enabled=src.enabled,
            schedule_enabled=sched.enabled,
            interval_minutes=sched.interval_minutes,
            next_run_at=to_utc_non_optional(sched.next_run_at),
            last_execution_status=status,
            last_execution_at=last_exec_at,
            created_at=to_utc_non_optional(sched.created_at),
            updated_at=to_utc_non_optional(sched.updated_at),
        )

    def get_schedule(self, source_id: str) -> SourceSchedule | None:
        return (
            self.session.query(SourceSchedule)
            .filter(SourceSchedule.source_id == source_id)
            .first()
        )

    def save_schedule(self, schedule: SourceSchedule) -> None:
        self.session.add(schedule)
        self.session.flush()

    def delete_schedule(self, source_id: str) -> None:
        sched = self.get_schedule(source_id)
        if not sched:
            raise ScheduleNotFoundError(f"Schedule for Source {source_id} not found.")
        self.session.delete(sched)
        self.session.flush()
