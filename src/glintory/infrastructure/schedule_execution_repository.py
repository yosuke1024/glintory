import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from glintory.domain.models import (
    ScheduleExecution,
    Source,
    SourceSchedule,
)
from glintory.domain.scheduling import (
    ClaimedScheduleExecution,
    ScheduleExecutionAlreadyFinalizedError,
    ScheduleExecutionItem,
    ScheduleExecutionNotFoundError,
    ScheduleExecutionStatus,
)
from glintory.infrastructure.scheduler_lease import SchedulerLeaseRepository
from glintory.services.schedule_calculation import calculate_due_occurrence


class ScheduleExecutionRepository:
    def __init__(
        self, session: Session, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lease_repo = SchedulerLeaseRepository(session, clock=self.clock)

    def recover_stale_executions(
        self, *, now: datetime, stale_threshold_dt: datetime
    ) -> int:
        stmt = (
            self.session.query(ScheduleExecution)
            .filter(ScheduleExecution.status == ScheduleExecutionStatus.RUNNING)
            .filter(ScheduleExecution.started_at < stale_threshold_dt)
            .update(
                {
                    ScheduleExecution.status: ScheduleExecutionStatus.ABANDONED,
                    ScheduleExecution.completed_at: now,
                    ScheduleExecution.error_summary: "Scheduled execution was abandoned after exceeding the stale threshold.",
                },
                synchronize_session=False,
            )
        )
        self.session.flush()
        if stmt > 0:
            self.session.expire_all()
        return stmt

    def claim_due_executions(
        self,
        *,
        owner_token: str,
        max_due: int,
        now: datetime,
        force: bool = False,
    ) -> list[ClaimedScheduleExecution]:
        # 1. Assert lease ownership
        self.lease_repo.assert_owned(owner_token=owner_token)

        # 2. Fetch due schedules that are enabled and belong to enabled sources
        query = (
            self.session.query(SourceSchedule, Source.name, Source.source_type)
            .join(Source, SourceSchedule.source_id == Source.id)
            .filter(SourceSchedule.enabled)
            .filter(Source.enabled)
        )
        if not force:
            query = query.filter(SourceSchedule.next_run_at <= now)

        query = query.order_by(SourceSchedule.next_run_at.asc(), SourceSchedule.source_id.asc()).limit(max_due)

        due_schedules = query.all()
        claimed = []

        for sched, source_name, source_type in due_schedules:
            next_run_tz = sched.next_run_at
            if next_run_tz.tzinfo is None:
                next_run_tz = next_run_tz.replace(tzinfo=UTC)

            occurrence = calculate_due_occurrence(
                current_next_run_at=next_run_tz,
                now=now,
                interval_minutes=sched.interval_minutes,
            )
            if not occurrence:
                continue

            # Update schedule's next_run_at in DB
            sched.next_run_at = occurrence.next_run_at
            sched.updated_at = now
            self.session.flush()

            # Insert execution record
            exec_id = str(uuid.uuid4())
            stmt = (
                sqlite_insert(ScheduleExecution)
                .values(
                    id=exec_id,
                    source_id=sched.source_id,
                    scheduled_for=occurrence.scheduled_for,
                    started_at=now,
                    status=ScheduleExecutionStatus.RUNNING.value,
                    coalesced_count=occurrence.coalesced_count,
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=["source_id", "scheduled_for"])
            )

            result = cast(CursorResult, self.session.execute(stmt))
            if result.rowcount > 0:
                claimed.append(
                    ClaimedScheduleExecution(
                        execution_id=exec_id,
                        source_id=sched.source_id,
                        source_name=source_name,
                        source_type=source_type,
                        source_enabled=True,
                        scheduled_for=occurrence.scheduled_for,
                        coalesced_count=occurrence.coalesced_count,
                    )
                )

        return claimed

    def finalize_execution(
        self,
        *,
        execution_id: str,
        status: ScheduleExecutionStatus,
        completed_at: datetime,
        collection_run_id: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        exec_record = (
            self.session.query(ScheduleExecution).filter_by(id=execution_id).first()
        )
        if not exec_record:
            raise ScheduleExecutionNotFoundError(f"Execution {execution_id} not found.")

        # Terminal Guard
        if exec_record.status != ScheduleExecutionStatus.RUNNING:
            raise ScheduleExecutionAlreadyFinalizedError(
                f"Execution {execution_id} is already in a final state: {exec_record.status}."
            )

        if status == ScheduleExecutionStatus.RUNNING:
            raise ValueError("Cannot finalize execution to RUNNING state.")

        exec_record.status = status.value
        exec_record.completed_at = completed_at
        exec_record.collection_run_id = collection_run_id
        exec_record.error_summary = error_summary
        self.session.flush()

    def list_executions(
        self,
        *,
        source_filter: str | None = None,
        status_filter: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[Sequence[ScheduleExecutionItem], int]:
        query = self.session.query(
            ScheduleExecution, Source.name, Source.source_type
        ).outerjoin(Source, ScheduleExecution.source_id == Source.id)

        if source_filter:
            # Match by source name or source ID
            query = query.filter(
                (Source.name.icontains(source_filter))
                | (ScheduleExecution.source_id == source_filter)
            )
        if status_filter:
            query = query.filter(ScheduleExecution.status == status_filter)

        total_count = query.count()

        results = (
            query.order_by(
                ScheduleExecution.scheduled_for.desc(),
                ScheduleExecution.started_at.desc(),
                ScheduleExecution.id.desc(),
            )
            .limit(limit)
            .offset(offset)
            .all()
        )

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

        items = []
        for exec_record, source_name, source_type in results:
            s_name = source_name or "Deleted Source"
            s_type = source_type or "unknown"
            items.append(
                ScheduleExecutionItem(
                    id=exec_record.id,
                    source_id=exec_record.source_id,
                    source_name=s_name,
                    source_type=s_type,
                    scheduled_for=to_utc_non_optional(exec_record.scheduled_for),
                    started_at=to_utc_non_optional(exec_record.started_at),
                    completed_at=to_utc(exec_record.completed_at),
                    status=ScheduleExecutionStatus(exec_record.status),
                    collection_run_id=exec_record.collection_run_id,
                    coalesced_count=exec_record.coalesced_count,
                    safe_error_summary=exec_record.error_summary,
                )
            )
        return items, total_count

    def get_execution_detail(self, execution_id: str) -> ScheduleExecutionItem | None:
        result = (
            self.session.query(ScheduleExecution, Source.name, Source.source_type)
            .outerjoin(Source, ScheduleExecution.source_id == Source.id)
            .filter(ScheduleExecution.id == execution_id)
            .first()
        )
        if not result:
            return None

        exec_record, source_name, source_type = result
        s_name = source_name or "Deleted Source"
        s_type = source_type or "unknown"

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

        return ScheduleExecutionItem(
            id=exec_record.id,
            source_id=exec_record.source_id,
            source_name=s_name,
            source_type=s_type,
            scheduled_for=to_utc_non_optional(exec_record.scheduled_for),
            started_at=to_utc_non_optional(exec_record.started_at),
            completed_at=to_utc(exec_record.completed_at),
            status=ScheduleExecutionStatus(exec_record.status),
            collection_run_id=exec_record.collection_run_id,
            coalesced_count=exec_record.coalesced_count,
            safe_error_summary=exec_record.error_summary,
        )
