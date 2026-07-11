from fastapi import HTTPException, Query


def validate_executions_params(
    status: str | None = None,
    per_page: int = 25,
    page: int = 1,
) -> None:
    # 1. status validation
    allowed_statuses = {
        "running",
        "succeeded",
        "partial",
        "failed",
        "skipped_busy",
        "skipped_disabled",
        "abandoned",
    }
    if status is not None and status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status parameter. Must be one of: {', '.join(allowed_statuses)}",
        )

    # 2. per_page validation
    allowed_per_pages = {10, 25, 50, 100}
    if per_page not in allowed_per_pages:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid per_page parameter. Must be one of: {', '.join(map(str, allowed_per_pages))}",
        )

    # 3. page validation
    if page < 1:
        raise HTTPException(
            status_code=400, detail="Page parameter must be 1 or greater"
        )


def execution_query_parameters(
    source: str | None = Query(None),
    status: str | None = Query(None, alias="status"),
    page: int = Query(1),
    per_page: int = Query(25),
) -> dict:
    validate_executions_params(status, per_page, page)
    return {"source": source, "status": status, "page": page, "per_page": per_page}
