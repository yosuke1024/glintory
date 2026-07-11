import math
import pathlib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from glintory.infrastructure.schedule_execution_repository import (
    ScheduleExecutionRepository,
)
from glintory.services.schedule_management import ScheduleManagementService

base_dir = pathlib.Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(base_dir / "templates"))

router = APIRouter()


def get_schedule_management_service(request: Request) -> ScheduleManagementService:
    app = request.app
    return ScheduleManagementService(session_factory=app.state.session_factory)


def get_schedule_execution_repo(request: Request) -> ScheduleExecutionRepository:
    app = request.app
    # Note: Repository needs session, so we manage session manually or via dependency.
    # In routes, we instantiate it with a session from state.session_factory.
    return ScheduleExecutionRepository(session=app.state.session_factory())


@router.get("/schedules", response_class=HTMLResponse)
async def list_schedules(
    request: Request,
    enabled: bool | None = Query(None),
    source: str | None = Query(None),
    service: ScheduleManagementService = Depends(get_schedule_management_service),
):
    schedules = service.list_schedules(enabled=enabled, source_name_filter=source)
    return templates.TemplateResponse(
        request=request,
        name="schedules/list.html",
        context={
            "schedules": schedules,
            "selected_enabled": enabled,
            "selected_source": source,
        },
    )


from glintory.web.validation import execution_query_parameters


@router.get("/schedule-executions", response_class=HTMLResponse)
async def list_schedule_executions(
    request: Request,
    params: dict = Depends(execution_query_parameters),
):
    source = params["source"]
    status_filter = params["status"]
    page = params["page"]
    per_page = params["per_page"]

    session = request.app.state.session_factory()
    try:
        repo = ScheduleExecutionRepository(session)
        runs, total = repo.list_executions(
            source_filter=source,
            status_filter=status_filter,
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        total_pages = math.ceil(total / per_page) if total > 0 else 0
        return templates.TemplateResponse(
            request=request,
            name="schedule_executions/list.html",
            context={
                "executions": runs,
                "selected_source": source,
                "selected_status": status_filter,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "total_count": total,
            },
        )
    finally:
        session.close()


@router.get("/schedule-executions/{execution_id}", response_class=HTMLResponse)
async def get_schedule_execution_detail(
    request: Request,
    execution_id: str,
):
    session = request.app.state.session_factory()
    try:
        repo = ScheduleExecutionRepository(session)
        detail = repo.get_execution_detail(execution_id)
        if not detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found."
            )
        return templates.TemplateResponse(
            request=request,
            name="schedule_executions/detail.html",
            context={"execution": detail},
        )
    finally:
        session.close()
