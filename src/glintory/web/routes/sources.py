import math
import pathlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from glintory.config import settings
from glintory.domain.enums import CollectionRunStatus
from glintory.domain.operations import (
    CollectionRunNotFoundError,
    SourceAlreadyRunningError,
    SourceDisabledError,
    SourceNotFoundError,
)
from glintory.services.source_operations import SourceOperationsService
from glintory.web.csrf import generate_csrf_token, set_csrf_cookie, validate_csrf
from glintory.web.forms import parse_urlencoded_form

# Set up templates directory
base_dir = pathlib.Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(base_dir / "templates"))


# Custom template filters
def format_duration(finished_at, started_at) -> str:
    if not finished_at or not started_at:
        return "—"
    delta = finished_at - started_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}m {seconds}s"


templates.env.filters["format_duration"] = format_duration

router = APIRouter(prefix="/sources")
runs_router = APIRouter(prefix="/collection-runs")


def get_source_ops_service(request: Request) -> SourceOperationsService:
    app = request.app
    return SourceOperationsService(
        session_factory=app.state.session_factory,
        registry=app.state.registry,
        collection_service=app.state.collection_service,
    )


@router.get("", response_class=HTMLResponse)
async def list_sources(
    request: Request,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    sources = service.list_sources()
    return templates.TemplateResponse(
        request=request, name="sources/list.html", context={"sources": sources}
    )


@router.get("/{source_id}", response_class=HTMLResponse)
async def get_source_detail(
    request: Request,
    source_id: str,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    try:
        source = service.get_source_detail(source_id)
        runs = service.list_collection_runs(source_id=source_id, per_page=10)[0]
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    csrf_token = generate_csrf_token()
    response = templates.TemplateResponse(
        request=request,
        name="sources/detail.html",
        context={"source": source, "runs": runs, "csrf_token": csrf_token},
    )
    set_csrf_cookie(response, csrf_token, request)
    return response


@router.post("/{source_id}/enable")
async def enable_source(
    request: Request,
    source_id: str,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    try:
        service.enable_source(source_id)
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred.",
        )

    return RedirectResponse(
        url=f"/sources/{source_id}?notice=source_enabled",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{source_id}/disable")
async def disable_source(
    request: Request,
    source_id: str,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    try:
        service.disable_source(source_id)
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected error occurred.",
        )

    return RedirectResponse(
        url=f"/sources/{source_id}?notice=source_disabled",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{source_id}/collect")
async def collect_source(
    request: Request,
    source_id: str,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    try:
        result = await service.collect_now(source_id)
    except SourceNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except SourceDisabledError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except SourceAlreadyRunningError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        from sqlalchemy.exc import OperationalError

        if isinstance(e, OperationalError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database temporarily unavailable",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
        )

    notice_map = {
        CollectionRunStatus.SUCCEEDED: "collection_succeeded",
        CollectionRunStatus.PARTIAL: "collection_partial",
        CollectionRunStatus.FAILED: "collection_failed",
    }
    notice = notice_map.get(result.status, "collection_failed")

    return RedirectResponse(
        url=f"/sources/{source_id}?notice={notice}&run_id={result.collection_run_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@runs_router.get("", response_class=HTMLResponse)
async def list_collection_runs(
    request: Request,
    source: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    trigger: str | None = None,
    page: int = 1,
    per_page: int | None = None,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    if per_page is None:
        per_page = settings.collection_history_per_page

    try:
        runs, total = service.list_collection_runs(
            source_id=source,
            status=status_filter,
            trigger_type=trigger,
            page=page,
            per_page=per_page,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    sources = service.list_sources()
    total_pages = math.ceil(total / per_page) if total > 0 else 0

    return templates.TemplateResponse(
        request=request,
        name="collection_runs/list.html",
        context={
            "runs": runs,
            "sources": sources,
            "selected_source": source,
            "selected_status": status_filter,
            "selected_trigger": trigger,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_count": total,
        },
    )


@runs_router.get("/{run_id}", response_class=HTMLResponse)
async def get_collection_run_detail(
    request: Request,
    run_id: str,
    service: SourceOperationsService = Depends(get_source_ops_service),
):
    try:
        detail = service.get_collection_run_detail(run_id)
    except CollectionRunNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return templates.TemplateResponse(
        request=request, name="collection_runs/detail.html", context={"run": detail}
    )
