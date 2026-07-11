import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from glintory.domain.models import (
    ScheduleExecution,
    SchedulerLease,
    Source,
    SourceSchedule,
)
from glintory.domain.scheduling import ScheduleExecutionStatus
from glintory.infrastructure.database import get_db
from glintory.infrastructure.schedule_execution_repository import (
    ScheduleExecutionRepository,
)
from glintory.services.schedule_management import ScheduleManagementService

router = APIRouter(prefix="/api/v1")


def format_iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/schedules")
async def api_list_schedules(
    enabled: bool | None = Query(None),
    source: str | None = Query(None),
    db: Session = Depends(get_db),
):
    service = ScheduleManagementService(lambda: db)
    schedules = service.list_schedules(enabled=enabled, source_name_filter=source)
    return [
        {
            "source_id": s.source_id,
            "source_name": s.source_name,
            "source_type": s.source_type,
            "source_enabled": s.source_enabled,
            "schedule_enabled": s.schedule_enabled,
            "interval_minutes": s.interval_minutes,
            "next_run_at": format_iso_utc(s.next_run_at),
            "last_execution_status": s.last_execution_status.value
            if s.last_execution_status
            else None,
            "last_execution_at": format_iso_utc(s.last_execution_at),
        }
        for s in schedules
    ]


@router.get("/schedules/{source_id}")
async def api_get_schedule(
    source_id: str,
    db: Session = Depends(get_db),
):
    service = ScheduleManagementService(lambda: db)
    from glintory.domain.scheduling import ScheduleNotFoundError

    try:
        s = service.get_schedule(source_id)
        return {
            "source_id": s.source_id,
            "source_name": s.source_name,
            "source_type": s.source_type,
            "source_enabled": s.source_enabled,
            "schedule_enabled": s.schedule_enabled,
            "interval_minutes": s.interval_minutes,
            "next_run_at": format_iso_utc(s.next_run_at),
            "last_execution_status": s.last_execution_status.value
            if s.last_execution_status
            else None,
            "last_execution_at": format_iso_utc(s.last_execution_at),
        }
    except ScheduleNotFoundError:
        raise HTTPException(status_code=404, detail="Schedule not found")


from glintory.web.validation import execution_query_parameters


@router.get("/schedule-executions")
async def api_list_executions(
    params: dict = Depends(execution_query_parameters),
    db: Session = Depends(get_db),
):
    source = params["source"]
    status_filter = params["status"]
    page = params["page"]
    per_page = params["per_page"]

    repo = ScheduleExecutionRepository(db)
    items, total = repo.list_executions(
        source_filter=source,
        status_filter=status_filter,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    total_pages = math.ceil(total / per_page) if total > 0 else 0
    return {
        "items": [
            {
                "id": x.id,
                "source_id": x.source_id,
                "source_name": x.source_name,
                "source_type": x.source_type,
                "scheduled_for": format_iso_utc(x.scheduled_for),
                "started_at": format_iso_utc(x.started_at),
                "completed_at": format_iso_utc(x.completed_at),
                "status": x.status.value,
                "collection_run_id": x.collection_run_id,
                "coalesced_count": x.coalesced_count,
                "error_summary": x.safe_error_summary,
            }
            for x in items
        ],
        "total_count": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


@router.get("/schedule-executions/{execution_id}")
async def api_get_execution(
    execution_id: str,
    db: Session = Depends(get_db),
):
    repo = ScheduleExecutionRepository(db)
    x = repo.get_execution_detail(execution_id)
    if not x:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {
        "id": x.id,
        "source_id": x.source_id,
        "source_name": x.source_name,
        "source_type": x.source_type,
        "scheduled_for": format_iso_utc(x.scheduled_for),
        "started_at": format_iso_utc(x.started_at),
        "completed_at": format_iso_utc(x.completed_at),
        "status": x.status.value,
        "collection_run_id": x.collection_run_id,
        "coalesced_count": x.coalesced_count,
        "error_summary": x.safe_error_summary,
    }


@router.get("/scheduler/status")
async def api_scheduler_status(
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)

    # 1. Active status
    lease = db.query(SchedulerLease).filter_by(lease_name="default").first()
    active = False
    heartbeat_at = None
    lease_expires_at = None
    if lease:
        lease_expires_tz = lease.expires_at
        if lease_expires_tz and lease_expires_tz.tzinfo is None:
            lease_expires_tz = lease_expires_tz.replace(tzinfo=UTC)

        active = lease_expires_tz > now
        heartbeat_at = format_iso_utc(lease.heartbeat_at)
        lease_expires_at = format_iso_utc(lease_expires_tz)

    # 2. Due schedule count
    due_schedule_count = (
        db.query(SourceSchedule)
        .join(Source, SourceSchedule.source_id == Source.id)
        .filter(SourceSchedule.enabled)
        .filter(Source.enabled)
        .filter(SourceSchedule.next_run_at <= now)
        .count()
    )

    # 3. Running execution count
    running_execution_count = (
        db.query(ScheduleExecution)
        .filter(ScheduleExecution.status == ScheduleExecutionStatus.RUNNING.value)
        .count()
    )

    return {
        "active": active,
        "heartbeat_at": heartbeat_at,
        "lease_expires_at": lease_expires_at,
        "due_schedule_count": due_schedule_count,
        "running_execution_count": running_execution_count,
    }
