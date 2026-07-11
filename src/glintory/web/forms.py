from collections.abc import Mapping
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request, status


async def parse_urlencoded_form(
    request: Request,
    *,
    max_bytes: int,
) -> Mapping[str, str]:
    """Parse application/x-www-form-urlencoded body with strict size limits."""
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported Content-Type. Expected application/x-www-form-urlencoded",
        )

    body_bytes = bytearray()
    async for chunk in request.stream():
        body_bytes.extend(chunk)
        if len(body_bytes) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Request entity too large",
            )

    try:
        body_str = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid UTF-8 encoding",
        )

    try:
        # strict_parsing=True ensures ValueError is raised on malformed data
        parsed_pairs = parse_qsl(body_str, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL-encoded form data",
        )

    form_data = {}
    for key, value in parsed_pairs:
        if key in form_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate field detected: {key}",
            )
        form_data[key] = value

    return form_data
