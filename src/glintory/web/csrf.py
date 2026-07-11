import hmac
import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from glintory.config import settings


def generate_csrf_token() -> str:
    """Generate a secure cryptographically random CSRF token."""
    return secrets.token_urlsafe(settings.web_csrf_token_bytes)


def set_csrf_cookie(response: Response, token: str, request: Request) -> None:
    """Set the CSRF token in a Secure (if HTTPS), HttpOnly cookie."""
    is_secure = request.url.scheme == "https"
    response.set_cookie(
        key=settings.web_csrf_cookie_name,
        value=token,
        httponly=True,
        samesite="strict",
        path="/",
        secure=is_secure,
    )


def _get_origin(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_csrf(request: Request, form_token: str) -> None:
    """Validate CSRF token and origin/referer consistency."""
    # 1. Retrieve cookie token
    cookie_token = request.cookies.get(settings.web_csrf_cookie_name)
    if not cookie_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF cookie missing",
        )

    # 2. Retrieve form token
    if not form_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing",
        )

    # 3. Securely compare tokens
    if not hmac.compare_digest(cookie_token, form_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )

    # 4. Origin / Referer Validation
    request_origin = f"{request.url.scheme}://{request.url.netloc}"

    origin_header = request.headers.get("origin")
    if origin_header:
        if origin_header.strip() != request_origin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF origin mismatch",
            )
    else:
        referer_header = request.headers.get("referer")
        if referer_header:
            referer_origin = _get_origin(referer_header)
            if referer_origin != request_origin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF referer mismatch",
                )
