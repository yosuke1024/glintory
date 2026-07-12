import pathlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from glintory.infrastructure.dashboard_repository import DashboardRepository
from glintory.infrastructure.database import get_db
from glintory.services.dashboard_query import DashboardQueryService

router = APIRouter()

# Identify absolute path for the templates directory
base_dir = pathlib.Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(base_dir / "templates"))


@router.get("/", response_class=HTMLResponse)
async def read_today(request: Request, db: Session = Depends(get_db)):
    """Renders the dashboard with database metrics, recent signals, and project placeholders."""
    repo = DashboardRepository(db)
    service = DashboardQueryService(repo)

    data = service.get_dashboard_data()
    summary = data["summary"]
    recent = data["recent_signals"]

    # Format the last collection time to string in UTC timezone
    last_collected_str = "—"
    if summary["last_success_at"]:
        last_success_utc = summary["last_success_at"].astimezone(UTC)
        last_collected_str = last_success_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    last_status = summary["last_collection_status"] or "—"

    # Fetch top 3 opportunities
    from glintory.infrastructure.opportunity_query import OpportunityQueryRepository
    from glintory.services.opportunity_query import OpportunityQueryService

    query_repo = OpportunityQueryRepository(db)
    query_service = OpportunityQueryService(query_repo)
    top_opps = query_service.get_top_opportunities(limit=3)

    from glintory.domain.enums import CollectionRunStatus
    from glintory.domain.models import CollectionRun, Opportunity, Source

    has_any_opp = (
        db.query(Opportunity).filter(Opportunity.last_scored_at.isnot(None)).first()
        is not None
    )

    # Calculate Source Operations Summary
    enabled_sources_count = db.query(Source).filter(Source.enabled).count()

    running_sources_count = (
        db.query(CollectionRun)
        .filter(CollectionRun.status == CollectionRunStatus.RUNNING)
        .group_by(CollectionRun.source_id)
        .count()
    )

    now = datetime.now(UTC)
    last_24h = now - timedelta(hours=24)
    failed_runs_24h_count = (
        db.query(CollectionRun)
        .filter(
            CollectionRun.status.in_(
                [CollectionRunStatus.FAILED, CollectionRunStatus.ABANDONED]
            ),
            CollectionRun.started_at >= last_24h,
        )
        .count()
    )

    last_success_run = (
        db.query(func.max(CollectionRun.completed_at))
        .filter(
            CollectionRun.status.in_(
                [CollectionRunStatus.SUCCEEDED, CollectionRunStatus.PARTIAL]
            )
        )
        .scalar()
    )
    last_success_run_str = "—"
    if last_success_run:
        if last_success_run.tzinfo is None:
            last_success_run = last_success_run.replace(tzinfo=UTC)
        last_success_run_str = last_success_run.astimezone(UTC).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    # Scheduler Summary
    from glintory.domain.models import ScheduleExecution, SchedulerLease, SourceSchedule
    from glintory.domain.operations import CollectionTriggerType
    from glintory.domain.scheduling import ScheduleExecutionStatus

    scheduled_sources_count = db.query(SourceSchedule).count()
    enabled_schedules_count = (
        db.query(SourceSchedule).filter(SourceSchedule.enabled).count()
    )

    due_schedules_count = (
        db.query(SourceSchedule)
        .join(Source, SourceSchedule.source_id == Source.id)
        .filter(SourceSchedule.enabled)
        .filter(Source.enabled)
        .filter(SourceSchedule.next_run_at <= now)
        .count()
    )

    lease = db.query(SchedulerLease).filter_by(lease_name="default").first()
    scheduler_active = False
    if lease:
        lease_expires = lease.expires_at
        if lease_expires and lease_expires.tzinfo is None:
            lease_expires = lease_expires.replace(tzinfo=UTC)
        scheduler_active = lease_expires > now

    last_scheduled_collection = (
        db.query(func.max(CollectionRun.completed_at))
        .filter(
            CollectionRun.status.in_(
                [CollectionRunStatus.SUCCEEDED, CollectionRunStatus.PARTIAL]
            ),
            CollectionRun.trigger_type == CollectionTriggerType.SCHEDULED.value,
        )
        .scalar()
    )
    last_scheduled_collection_str = "—"
    if last_scheduled_collection:
        if last_scheduled_collection.tzinfo is None:
            last_scheduled_collection = last_scheduled_collection.replace(tzinfo=UTC)
        last_scheduled_collection_str = last_scheduled_collection.astimezone(
            UTC
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

    failed_scheduled_executions_24h_count = (
        db.query(ScheduleExecution)
        .filter(
            ScheduleExecution.status.in_(
                [
                    ScheduleExecutionStatus.FAILED.value,
                    ScheduleExecutionStatus.ABANDONED.value,
                ]
            ),
            ScheduleExecution.started_at >= last_24h,
        )
        .count()
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "total_signals": summary["total_signals"],
            "total_sources": summary["total_sources_with_signals"],
            "last_collected": last_collected_str,
            "last_status": last_status,
            "recent_signals": recent,
            "top_opportunities": top_opps,
            "has_any_opportunity": has_any_opp,
            "enabled_sources_count": enabled_sources_count,
            "running_sources_count": running_sources_count,
            "failed_runs_24h_count": failed_runs_24h_count,
            "last_success_run_str": last_success_run_str,
            "scheduled_sources_count": scheduled_sources_count,
            "enabled_schedules_count": enabled_schedules_count,
            "due_schedules_count": due_schedules_count,
            "scheduler_active": scheduler_active,
            "last_scheduled_collection_str": last_scheduled_collection_str,
            "failed_scheduled_executions_24h_count": failed_scheduled_executions_24h_count,
        },
    )
