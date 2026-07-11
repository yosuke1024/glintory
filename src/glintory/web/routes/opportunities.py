import pathlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from glintory.config import settings
from glintory.domain.enums import (
    Confidence,
    EvidenceRelationType,
    OpportunityStatus,
    SignalType,
)
from glintory.domain.models import OpportunitySignal
from glintory.domain.opportunities import OpportunityListFilters
from glintory.domain.search import SignalSearchFilters
from glintory.infrastructure.database import get_db
from glintory.infrastructure.opportunity_query import OpportunityQueryRepository
from glintory.infrastructure.signal_search import SignalSearchRepository
from glintory.services.opportunity_query import OpportunityQueryService
from glintory.services.signal_query import SignalQueryService
from glintory.web.routes.api import format_iso_utc

html_router = APIRouter(prefix="/opportunities")
api_router = APIRouter(prefix="/api/v1/opportunities")
watchlist_router = APIRouter()

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
    notice: str | None = None,
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

    # CSRF protection: generate token and set cookie
    from glintory.web.csrf import generate_csrf_token, set_csrf_cookie

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        request=request,
        name="opportunities/detail.html",
        context={
            "opportunity": detail,
            "csrf_token": csrf_token,
            "notice": notice,
        },
    )
    set_csrf_cookie(response, csrf_token, request)
    return response


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
                "score_is_stale": item.score_is_stale,
                "evidence_updated_at": format_iso_utc(item.evidence_updated_at),
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
                "association_source": ev.association_source,
                "is_excluded": ev.is_excluded,
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
        "score_is_stale": detail.score_is_stale,
        "evidence_updated_at": format_iso_utc(detail.evidence_updated_at),
    }


# ------------------------------------------------------------
# Opportunity Review Mutations & Watchlist Routes
# ------------------------------------------------------------
from sqlalchemy.exc import OperationalError

from glintory.domain.review import (
    ConcurrentStatusChangeError,
    EvidenceAddRequest,
    EvidenceAlreadyExcludedError,
    EvidenceAlreadyLinkedError,
    EvidenceNotExcludedError,
    EvidenceNotLinkedError,
    EvidenceUpdateRequest,
    InvalidStatusTransitionError,
    NoteCreateRequest,
    NoteNotFoundError,
    NoteUpdateRequest,
    OpportunityNotFoundError,
    ReviewReasonRequiredError,
    ReviewValidationError,
    SignalNotFoundError,
    StatusTransitionRequest,
)
from glintory.services.opportunity_review import OpportunityReviewService
from glintory.web.csrf import validate_csrf
from glintory.web.forms import parse_urlencoded_form


def handle_review_error(e: Exception) -> RedirectResponse:
    if isinstance(
        e, (OpportunityNotFoundError, NoteNotFoundError, SignalNotFoundError)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    if isinstance(e, ConcurrentStatusChangeError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if isinstance(
        e,
        (
            ReviewValidationError,
            ReviewReasonRequiredError,
            InvalidStatusTransitionError,
            EvidenceAlreadyLinkedError,
            EvidenceNotLinkedError,
            EvidenceAlreadyExcludedError,
            EvidenceNotExcludedError,
        ),
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if isinstance(e, OperationalError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable",
        )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred",
    )


@html_router.post("/{opportunity_id}/status")
async def update_status(
    request: Request,
    opportunity_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    expected_status_str = form.get("expected_status", "")
    target_status_str = form.get("target_status", "")
    reason = form.get("reason")

    try:
        expected_status = OpportunityStatus(expected_status_str)
        target_status = OpportunityStatus(target_status_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status value")

    service = OpportunityReviewService(lambda: db)
    try:
        service.transition_status(
            StatusTransitionRequest(
                opportunity_id=opportunity_id,
                expected_status=expected_status,
                target_status=target_status,
                reason=reason,
            )
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=status_updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/notes")
async def add_note(
    request: Request,
    opportunity_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    body = form.get("body", "")

    service = OpportunityReviewService(lambda: db)
    try:
        service.create_note(
            NoteCreateRequest(
                opportunity_id=opportunity_id,
                body=body,
            )
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=note_added",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/notes/{note_id}/edit")
async def edit_note(
    request: Request,
    opportunity_id: str,
    note_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    body = form.get("body", "")

    service = OpportunityReviewService(lambda: db)
    try:
        service.update_note(
            NoteUpdateRequest(
                opportunity_id=opportunity_id,
                note_id=note_id,
                body=body,
            )
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=note_updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/notes/{note_id}/delete")
async def delete_note(
    request: Request,
    opportunity_id: str,
    note_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    service = OpportunityReviewService(lambda: db)
    try:
        service.delete_note(
            opportunity_id=opportunity_id,
            note_id=note_id,
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=note_deleted",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/evidence")
async def add_evidence(
    request: Request,
    opportunity_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    signal_id = form.get("signal_id", "")
    relation_type_str = form.get("relation_type", "")
    relevance_score_str = form.get("relevance_score", "0.0")
    review_note = form.get("review_note")

    try:
        relation_type = EvidenceRelationType(relation_type_str)
        relevance_score = float(relevance_score_str)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid relation type or relevance score"
        )

    service = OpportunityReviewService(lambda: db)
    try:
        res = service.add_evidence(
            EvidenceAddRequest(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                relation_type=relation_type,
                relevance_score=relevance_score,
                review_note=review_note,
            )
        )
        notice = "evidence_restored" if res.action == "restored" else "evidence_added"
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice={notice}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/evidence/{signal_id}/update")
async def update_evidence(
    request: Request,
    opportunity_id: str,
    signal_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    relation_type_str = form.get("relation_type", "")
    relevance_score_str = form.get("relevance_score", "0.0")
    review_note = form.get("review_note")

    try:
        relation_type = EvidenceRelationType(relation_type_str)
        relevance_score = float(relevance_score_str)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid relation type or relevance score"
        )

    service = OpportunityReviewService(lambda: db)
    try:
        service.update_evidence(
            EvidenceUpdateRequest(
                opportunity_id=opportunity_id,
                signal_id=signal_id,
                relation_type=relation_type,
                relevance_score=relevance_score,
                review_note=review_note,
            )
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=evidence_updated",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/evidence/{signal_id}/exclude")
async def exclude_evidence(
    request: Request,
    opportunity_id: str,
    signal_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    review_note = form.get("review_note", "")

    service = OpportunityReviewService(lambda: db)
    try:
        service.exclude_evidence(
            opportunity_id=opportunity_id,
            signal_id=signal_id,
            review_note=review_note,
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=evidence_excluded",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@html_router.post("/{opportunity_id}/evidence/{signal_id}/restore")
async def restore_evidence(
    request: Request,
    opportunity_id: str,
    signal_id: str,
    db: Session = Depends(get_db),
):
    form = await parse_urlencoded_form(request, max_bytes=settings.web_max_form_bytes)
    csrf_token = form.get("csrf_token", "")
    validate_csrf(request, csrf_token)

    relation_type_str = form.get("relation_type", "")
    relevance_score_str = form.get("relevance_score", "0.0")
    review_note = form.get("review_note")

    try:
        relation_type = EvidenceRelationType(relation_type_str)
        relevance_score = float(relevance_score_str)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid relation type or relevance score"
        )

    service = OpportunityReviewService(lambda: db)
    try:
        service.restore_evidence(
            opportunity_id=opportunity_id,
            signal_id=signal_id,
            relation_type=relation_type,
            relevance_score=relevance_score,
            review_note=review_note,
        )
    except Exception as e:
        handle_review_error(e)

    return RedirectResponse(
        url=f"/opportunities/{opportunity_id}?notice=evidence_restored",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@watchlist_router.get("/watchlist", response_class=HTMLResponse)
async def get_watchlist(
    request: Request,
    db: Session = Depends(get_db),
):
    """Renders the Watchlist page showing opportunities with status == watch."""
    repo = OpportunityQueryRepository(db)
    service = OpportunityQueryService(repo)

    # Use existing list filtering by targeting WATCH status
    filters = OpportunityListFilters(
        status=OpportunityStatus.WATCH,
        page=1,
        per_page=100,  # Grab a larger set for the Watchlist dashboard
    )
    page_result = service.list_opportunities(filters)

    return templates.TemplateResponse(
        request=request,
        name="opportunities/watchlist.html",
        context={
            "opportunities": page_result.items,
            "total_count": page_result.total_count,
        },
    )


@html_router.get("/{opportunity_id}/evidence/search", response_class=HTMLResponse)
async def search_opportunity_evidence(
    request: Request,
    opportunity_id: str,
    q: str | None = None,
    source: str | None = None,
    type_val: Annotated[str | None, Query(alias="type")] = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    """FTS search for signals to link to a specific opportunity."""
    # 1. Fetch opportunity
    opp_repo = OpportunityQueryRepository(db)
    opp_detail = opp_repo.get_detail(opportunity_id)
    if not opp_detail:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    # 2. Setup search filters
    search_repo = SignalSearchRepository(db)
    search_service = SignalQueryService(search_repo)

    active_sources = search_repo.get_active_sources()
    errors = []

    if q and len(q) > 200:
        errors.append("Search query cannot exceed 200 characters.")

    sig_type = None
    if type_val:
        try:
            sig_type = SignalType(type_val)
        except ValueError:
            errors.append("Invalid Signal Type.")

    page = max(page, 1)

    per_page = settings.evidence_search_per_page

    # 3. Perform FTS Search
    decorated_items = []
    total_count = 0
    total_pages = 0

    if not errors:
        filters = SignalSearchFilters(
            query=q,
            source_id=source if source else None,
            signal_type=sig_type,
            page=page,
            per_page=per_page,
        )
        try:
            page_result = search_service.search(filters)
            total_count = page_result.total_count
            total_pages = page_result.total_pages

            sig_ids = [item.id for item in page_result.items]

            # Bulk load relation state for the current opportunity
            links = (
                db.query(OpportunitySignal)
                .filter(OpportunitySignal.opportunity_id == opportunity_id)
                .filter(OpportunitySignal.signal_id.in_(sig_ids))
                .all()
            )
            link_map = {link.signal_id: link for link in links}

            # Bulk load other active links count to display
            other_active_links = (
                db.query(
                    OpportunitySignal.signal_id,
                    func.count(OpportunitySignal.opportunity_id),
                )
                .filter(OpportunitySignal.opportunity_id != opportunity_id)
                .filter(OpportunitySignal.signal_id.in_(sig_ids))
                .filter(OpportunitySignal.is_excluded.is_(False))
                .group_by(OpportunitySignal.signal_id)
                .all()
            )
            other_link_counts = {
                sig_id: count for sig_id, count in other_active_links
            }

            for item in page_result.items:
                link = link_map.get(item.id)
                if not link:
                    state = "unlinked"
                elif link.is_excluded:
                    state = "excluded"
                else:
                    state = "active"

                decorated_items.append(
                    {
                        "signal": item,
                        "state": state,
                        "relation_type": link.relation_type if link else None,
                        "relevance_score": link.relevance_score if link else 0.0,
                        "review_note": link.review_note if link else None,
                        "other_link_count": other_link_counts.get(item.id, 0),
                    }
                )
        except Exception as e:
            errors.append(f"Search failed: {str(e)}")

    # 4. Generate CSRF token for Add / Restore actions on the search page
    from glintory.web.csrf import generate_csrf_token, set_csrf_cookie

    csrf_token = generate_csrf_token()

    response = templates.TemplateResponse(
        request=request,
        name="opportunities/evidence_search.html",
        context={
            "opportunity": opp_detail,
            "signals": decorated_items,
            "total_count": total_count,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "sources": active_sources,
            "signal_types": list(SignalType),
            "selected_source": source,
            "selected_type": type_val,
            "q": q,
            "csrf_token": csrf_token,
            "error_messages": errors,
        },
    )
    set_csrf_cookie(response, csrf_token, request)
    return response
