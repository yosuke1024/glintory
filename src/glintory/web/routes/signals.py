import pathlib
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from glintory.domain.enums import SignalType
from glintory.domain.search import SignalSearchFilters
from glintory.infrastructure.database import get_db
from glintory.infrastructure.signal_search import SignalSearchRepository
from glintory.services.signal_query import SignalQueryService

router = APIRouter(prefix="/signals")

# templates directory setup
base_dir = pathlib.Path(__file__).parent.parent.parent
templates = Jinja2Templates(directory=str(base_dir / "web" / "templates"))


@router.get("", response_class=HTMLResponse)
async def list_signals(
    request: Request,
    q: str | None = None,
    source: str | None = None,
    type_val: Annotated[str | None, Query(alias="type")] = None,
    published_from: Annotated[str | None, Query(alias="from")] = None,
    published_to: Annotated[str | None, Query(alias="to")] = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Lists signals with full-text search, filtering, and pagination support."""
    repo = SignalSearchRepository(db)
    service = SignalQueryService(repo)

    # 1. Fetch distinct sources that have signals for filtering select box
    active_sources = repo.get_active_sources()

    errors: list[str] = []

    # 2. Query validations
    if q and len(q) > 200:
        errors.append("Search query cannot exceed 200 characters.")

    if source:
        try:
            uuid.UUID(source)
        except ValueError:
            errors.append("Invalid Source ID format.")

    sig_type = None
    if type_val:
        try:
            sig_type = SignalType(type_val)
        except ValueError:
            errors.append("Invalid Signal Type.")

    from_date = None
    to_date = None
    if published_from:
        try:
            from_date = date.fromisoformat(published_from)
        except ValueError:
            errors.append("Invalid 'From' date format (YYYY-MM-DD expected).")

    if published_to:
        try:
            to_date = date.fromisoformat(published_to)
        except ValueError:
            errors.append("Invalid 'To' date format (YYYY-MM-DD expected).")

    if from_date and to_date and from_date > to_date:
        errors.append("'From' date cannot be after 'To' date.")

    if page < 1:
        errors.append("Page must be 1 or greater.")

    if per_page not in (10, 25, 50, 100):
        errors.append("per_page must be one of 10, 25, 50, or 100.")

    # 3. If validation errors occur, render the template with error alerts
    if errors:
        return templates.TemplateResponse(
            request=request,
            name="signals/list.html",
            context={
                "signals": [],
                "total_count": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "sources": active_sources,
                "signal_types": list(SignalType),
                "q": q,
                "selected_source": source,
                "selected_type": type_val,
                "published_from": published_from,
                "published_to": published_to,
                "error_messages": errors,
            },
        )

    # 4. Perform repository search through service layer
    filters = SignalSearchFilters(
        query=q,
        source_id=source,
        signal_type=sig_type,
        published_from=from_date,
        published_to=to_date,
        page=page,
        per_page=per_page,
    )

    try:
        page_result = service.search(filters)
    except Exception as e:
        err_msg = str(e)
        # Catch explicit FTS5 issues to return clean 503 Service Unavailable
        if "SQLite build with FTS5 support" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SQLite build with FTS5 support is required",
            ) from None
        if "no such table: signals_fts" in err_msg or "signals_fts" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Signal search index is not initialized. Database migration might be required.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected database error occurred.",
        ) from None

    return templates.TemplateResponse(
        request=request,
        name="signals/list.html",
        context={
            "signals": page_result.items,
            "total_count": page_result.total_count,
            "page": page_result.page,
            "per_page": page_result.per_page,
            "total_pages": page_result.total_pages,
            "sources": active_sources,
            "signal_types": list(SignalType),
            "q": q,
            "selected_source": source,
            "selected_type": type_val,
            "published_from": published_from,
            "published_to": published_to,
            "error_messages": [],
        },
    )


@router.get("/{signal_id}", response_class=HTMLResponse)
async def signal_detail(
    request: Request,
    signal_id: str,
    db: Session = Depends(get_db),
):
    """Renders details of a single signal. Returns HTTP 404 for invalid IDs."""
    repo = SignalSearchRepository(db)
    service = SignalQueryService(repo)

    detail = service.get_detail(signal_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )

    return templates.TemplateResponse(
        request=request,
        name="signals/detail.html",
        context={
            "signal": detail,
        },
    )
