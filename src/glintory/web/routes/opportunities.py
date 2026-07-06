import pathlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from glintory.domain.enums import Confidence, OpportunityStatus
from glintory.domain.opportunities import OpportunityListFilters
from glintory.infrastructure.database import get_db
from glintory.infrastructure.opportunity_query import OpportunityQueryRepository
from glintory.services.opportunity_query import OpportunityQueryService
from glintory.web.routes.api import format_iso_utc

html_router = APIRouter(prefix="/opportunities")
api_router = APIRouter(prefix="/api/v1/opportunities")

base_dir = pathlib.Path(__file__).parent.parent.parent
templates = Jinja2Templates(directory=str(base_dir / "web" / "templates"))


@html_router.get("", response_class=HTMLResponse)
async def list_opportunities(
    request: Request,
    status_val: Annotated[str | None, Query(alias="status")] = None,
    confidence_val: Annotated[str | None, Query(alias="confidence")] = None,
    generation: str | None = None,
    min_score: int | None = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """Lists opportunities in Web UI with filtering and pagination support."""
    repo = OpportunityQueryRepository(db)
    service = OpportunityQueryService(repo)

    errors = []

    status_enum = None
    if status_val:
        try:
            status_enum = OpportunityStatus(status_val)
        except ValueError:
            errors.append("Invalid Opportunity Status.")

    conf_enum = None
    if confidence_val:
        try:
            conf_enum = Confidence(confidence_val)
        except ValueError:
            errors.append("Invalid Confidence.")

    if page < 1:
        errors.append("Page must be 1 or greater.")
    if per_page not in (10, 25, 50, 100):
        errors.append("per_page must be one of 10, 25, 50, or 100.")

    if errors:
        return templates.TemplateResponse(
            request=request,
            name="opportunities/list.html",
            context={
                "opportunities": [],
                "total_count": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "error_messages": errors,
                "status_list": list(OpportunityStatus),
                "confidence_list": list(Confidence),
                "selected_status": status_val,
                "selected_confidence": confidence_val,
                "selected_generation": generation,
                "selected_min_score": min_score,
            },
        )

    filters = OpportunityListFilters(
        status=status_enum,
        confidence=conf_enum,
        generation_method=generation,
        minimum_score=min_score,
        page=page,
        per_page=per_page,
    )

    page_result = service.list_opportunities(filters)

    return templates.TemplateResponse(
        request=request,
        name="opportunities/list.html",
        context={
            "opportunities": page_result.items,
            "total_count": page_result.total_count,
            "page": page_result.page,
            "per_page": page_result.per_page,
            "total_pages": page_result.total_pages,
            "error_messages": [],
            "status_list": list(OpportunityStatus),
            "confidence_list": list(Confidence),
            "selected_status": status_val,
            "selected_confidence": confidence_val,
            "selected_generation": generation,
            "selected_min_score": min_score,
        },
    )


@html_router.get("/{opportunity_id}", response_class=HTMLResponse)
async def opportunity_detail(
    request: Request,
    opportunity_id: str,
    db: Session = Depends(get_db),
):
    """Renders details of a single opportunity in Web UI. Returns HTTP 404 for invalid IDs."""
    repo = OpportunityQueryRepository(db)
    service = OpportunityQueryService(repo)

    detail = service.get_detail(opportunity_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )

    return templates.TemplateResponse(
        request=request,
        name="opportunities/detail.html",
        context={
            "opportunity": detail,
        },
    )


@api_router.get("")
async def list_opportunities_api(
    status_val: Annotated[str | None, Query(alias="status")] = None,
    confidence_val: Annotated[str | None, Query(alias="confidence")] = None,
    generation: str | None = None,
    min_score: int | None = None,
    page: int = 1,
    per_page: int = 25,
    db: Session = Depends(get_db),
):
    """JSON API to search and filter opportunities."""
    repo = OpportunityQueryRepository(db)
    service = OpportunityQueryService(repo)

    status_enum = None
    if status_val:
        try:
            status_enum = OpportunityStatus(status_val)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Opportunity Status.",
            ) from None

    conf_enum = None
    if confidence_val:
        try:
            conf_enum = Confidence(confidence_val)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Confidence.",
            ) from None

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

    filters = OpportunityListFilters(
        status=status_enum,
        confidence=conf_enum,
        generation_method=generation,
        minimum_score=min_score,
        page=page,
        per_page=per_page,
    )

    page_result = service.list_opportunities(filters)

    items_json = []
    for item in page_result.items:
        items_json.append(
            {
                "id": item.id,
                "title": item.title,
                "generation_method": item.generation_method,
                "status": item.status.value,
                "confidence": item.confidence.value,
                "scores": {
                    "evidence": item.evidence_score,
                    "feasibility": item.feasibility_score,
                    "penalty": item.penalty_score,
                    "total": item.total_score,
                },
                "evidence_count": item.evidence_count,
                "source_type_count": item.source_type_count,
                "current_scoring_version": "v1",
                "last_scored_at": format_iso_utc(item.last_scored_at),
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


@api_router.get("/{opportunity_id}")
async def get_opportunity_detail_api(
    opportunity_id: str,
    db: Session = Depends(get_db),
):
    """JSON API to fetch complete detailed information of a single opportunity."""
    repo = OpportunityQueryRepository(db)
    service = OpportunityQueryService(repo)

    detail = service.get_detail(opportunity_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Opportunity not found",
        )

    evidence_json = []
    for ev in detail.evidence:
        evidence_json.append(
            {
                "signal_id": ev.signal_id,
                "title": ev.title,
                "excerpt": ev.excerpt,
                "canonical_url": ev.canonical_url,
                "source_id": ev.source_id,
                "source_name": ev.source_name,
                "source_type": ev.source_type,
                "signal_type": ev.signal_type.value,
                "relation_type": ev.relation_type.value,
                "relevance_score": ev.relevance_score,
                "published_at": format_iso_utc(ev.published_at),
                "collected_at": format_iso_utc(ev.collected_at),
            }
        )

    history_json = []
    for hist in detail.score_history:
        history_json.append(
            {
                "id": hist.id,
                "scoring_version": hist.scoring_version,
                "as_of_date": hist.as_of_date.isoformat() if hist.as_of_date else None,
                "input_hash": hist.input_hash,
                "evidence_score": hist.evidence_score,
                "feasibility_score": hist.feasibility_score,
                "penalty_score": hist.penalty_score,
                "total_score": hist.total_score,
                "confidence": hist.confidence.value,
                "created_at": format_iso_utc(hist.created_at),
            }
        )

    latest_snap_json = None
    if detail.latest_snapshot:
        latest_snap_json = {
            "id": detail.latest_snapshot.id,
            "scoring_version": detail.latest_snapshot.scoring_version,
            "as_of_date": detail.latest_snapshot.as_of_date.isoformat()
            if detail.latest_snapshot.as_of_date
            else None,
            "input_hash": detail.latest_snapshot.input_hash,
            "evidence_score": detail.latest_snapshot.evidence_score,
            "feasibility_score": detail.latest_snapshot.feasibility_score,
            "penalty_score": detail.latest_snapshot.penalty_score,
            "total_score": detail.latest_snapshot.total_score,
            "confidence": detail.latest_snapshot.confidence.value,
            "explanation": detail.latest_snapshot.explanation,
            "created_at": format_iso_utc(detail.latest_snapshot.created_at),
        }

    return {
        "id": detail.id,
        "title": detail.title,
        "problem_statement": detail.problem_statement,
        "target_user": detail.target_user,
        "proposed_solution": detail.proposed_solution,
        "existing_projects": list(detail.existing_projects),
        "remaining_gap": detail.remaining_gap,
        "mvp_scope": detail.mvp_scope,
        "monetization_hypothesis": detail.monetization_hypothesis,
        "distribution_hypothesis": detail.distribution_hypothesis,
        "validation_method": detail.validation_method,
        "generation_method": detail.generation_method,
        "cluster_version": detail.cluster_version,
        "status": detail.status.value,
        "confidence": detail.confidence.value,
        "scores": {
            "evidence": detail.evidence_score,
            "feasibility": detail.feasibility_score,
            "penalty": detail.penalty_score,
            "total": detail.total_score,
        },
        "current_scoring_version": detail.current_scoring_version,
        "last_clustered_at": format_iso_utc(detail.last_clustered_at),
        "last_scored_at": format_iso_utc(detail.last_scored_at),
        "evidence": evidence_json,
        "latest_snapshot": latest_snap_json,
        "score_history": history_json,
        "created_at": format_iso_utc(detail.created_at),
        "updated_at": format_iso_utc(detail.updated_at),
    }
