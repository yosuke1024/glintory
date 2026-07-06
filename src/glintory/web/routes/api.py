import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from glintory.domain.enums import SignalType
from glintory.domain.search import SignalSearchFilters
from glintory.infrastructure.database import get_db
from glintory.infrastructure.signal_search import SignalSearchRepository
from glintory.services.signal_query import SignalQueryService

router = APIRouter(prefix="/api/v1/signals")


def format_iso_utc(dt: datetime | None) -> str | None:
    """Formats a datetime object to a strict ISO 8601 string with 'Z' suffix in UTC timezone."""
    if dt is None:
        return None
    # If timezone is naive, treat it as UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # Convert to UTC timezone first
    utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("")
async def list_signals_api(
    q: str | None = None,
    source: str | None = None,
    type_val: Annotated[str | None, Query(alias="type")] = None,
    published_from: Annotated[str | None, Query(alias="from")] = None,
    published_to: Annotated[str | None, Query(alias="to")] = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """JSON API to search and filter collected signals."""
    repo = SignalSearchRepository(db)
    service = SignalQueryService(repo)

    # 1. Validation checks
    if q and len(q) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query cannot exceed 200 characters.",
        )

    if source:
        try:
            uuid.UUID(source)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Source ID format.",
            ) from None

    sig_type = None
    if type_val:
        try:
            sig_type = SignalType(type_val)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Signal Type.",
            ) from None

    from_date = None
    to_date = None
    if published_from:
        try:
            from_date = date.fromisoformat(published_from)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'From' date format (YYYY-MM-DD expected).",
            ) from None

    if published_to:
        try:
            to_date = date.fromisoformat(published_to)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'To' date format (YYYY-MM-DD expected).",
            ) from None

    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'From' date cannot be after 'To' date.",
        )

    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Page must be 1 or greater.",
        )

    if per_page not in (10, 25, 50, 100):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="per_page must be one of 10, 25, 50, or 100.",
        )

    # 2. Search execution
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
        if "SQLite build with FTS5 support" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SQLite build with FTS5 support is required.",
            ) from None
        if "no such table: signals_fts" in err_msg or "signals_fts" in err_msg:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Signal search index is not initialized. Database migrations required.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected database error occurred.",
        ) from None

    # 3. Serialize output list (excluding database keys, ranks, or raw metadata)
    items_json = []
    for item in page_result.items:
        items_json.append(
            {
                "id": item.id,
                "title": item.title,
                "excerpt": item.excerpt,
                "author": item.author,
                "canonical_url": item.canonical_url,
                "source": {
                    "id": item.source_id,
                    "name": item.source_name,
                    "type": item.source_type,
                },
                "signal_type": item.signal_type.value,
                "published_at": format_iso_utc(item.published_at),
                "collected_at": format_iso_utc(item.collected_at),
                "freshness_score": item.freshness_score,
            }
        )

    return {
        "items": items_json,
        "pagination": {
            "page": page_result.page,
            "per_page": page_result.per_page,
            "total_count": page_result.total_count,
            "total_pages": page_result.total_pages,
        },
    }


@router.get("/{signal_id}")
async def get_signal_detail_api(
    signal_id: str,
    db: Session = Depends(get_db),
):
    """JSON API to fetch complete detailed information of a single signal."""
    repo = SignalSearchRepository(db)
    service = SignalQueryService(repo)

    detail = service.get_detail(signal_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signal not found",
        )

    # Return safe fields, excluding internal database configs or sensitive source keys
    return {
        "id": detail.id,
        "source_id": detail.source_id,
        "source_name": detail.source_name,
        "source_type": detail.source_type,
        "collection_run_id": detail.collection_run_id,
        "external_id": detail.external_id,
        "canonical_url": detail.canonical_url,
        "title": detail.title,
        "excerpt": detail.excerpt,
        "author": detail.author,
        "published_at": format_iso_utc(detail.published_at),
        "collected_at": format_iso_utc(detail.collected_at),
        "language": detail.language,
        "signal_type": detail.signal_type.value,
        "categories": detail.categories,
        "tags": detail.tags,
        "metrics": detail.metrics,
        "raw_metadata": detail.raw_metadata,
        "content_hash": detail.content_hash,
        "freshness_score": detail.freshness_score,
        "source_quality_score": detail.source_quality_score,
        "created_at": format_iso_utc(detail.created_at),
        "updated_at": format_iso_utc(detail.updated_at),
    }
