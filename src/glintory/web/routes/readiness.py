from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from glintory.infrastructure.database import check_database_connection, get_db

router = APIRouter()


@router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness endpoint to check if the application is ready to handle traffic.

    Verifies database connectivity without leaking internal exceptions or URLs.
    """
    # Simply perform a SELECT 1 check on the database.
    # Exclude internal error details from the public response to maintain security.
    if not check_database_connection(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "database": "unavailable"},
        )
    return {"status": "ready", "database": "ok"}
