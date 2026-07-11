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

    has_any_opp = db.query(Opportunity).first() is not None

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
        },
    )
