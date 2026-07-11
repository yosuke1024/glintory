import pytest
from fastapi import HTTPException

from glintory.config import settings
from glintory.web.csrf import generate_csrf_token, validate_csrf
from glintory.web.forms import parse_urlencoded_form


@pytest.mark.asyncio
async def test_parse_urlencoded_form_success():
    # Simulate a small urlencoded form body
    body = b"csrf_token=test-token-123&expected_status=inbox&target_status=watch"

    # Simple Mock Request
    class MockRequest:
        def __init__(self, body_bytes: bytes):
            self.body_bytes = body_bytes
            self.headers = {
                "content-length": str(len(body_bytes)),
                "content-type": "application/x-www-form-urlencoded",
            }

        async def stream(self):
            yield self.body_bytes

    req = MockRequest(body)
    parsed = await parse_urlencoded_form(req, max_bytes=1000)  # type: ignore

    assert parsed["csrf_token"] == "test-token-123"
    assert parsed["expected_status"] == "inbox"
    assert parsed["target_status"] == "watch"


@pytest.mark.asyncio
async def test_parse_urlencoded_form_too_large():
    body = b"csrf_token=" + b"A" * 2000

    class MockRequest:
        def __init__(self, body_bytes: bytes):
            self.body_bytes = body_bytes
            self.headers = {
                "content-length": str(len(body_bytes)),
                "content-type": "application/x-www-form-urlencoded",
            }

        async def stream(self):
            yield self.body_bytes

    req = MockRequest(body)
    with pytest.raises(HTTPException) as excinfo:
        await parse_urlencoded_form(req, max_bytes=1000)  # type: ignore
    assert excinfo.value.status_code == 413


def test_csrf_token_generation():
    token1 = generate_csrf_token()
    token2 = generate_csrf_token()

    assert len(token1) >= 43
    assert len(token2) >= 43
    assert token1 != token2


def test_validate_csrf_success():
    token = generate_csrf_token()

    class MockRequest:
        def __init__(
            self,
            cookie_token: str,
            origin: str | None = None,
            referer: str | None = None,
        ):
            self.cookies = {settings.web_csrf_cookie_name: cookie_token}
            self.headers = {}
            if origin:
                self.headers["origin"] = origin
            if referer:
                self.headers["referer"] = referer
            # Mock request URL
            self.url = type("URL", (), {"scheme": "http", "netloc": "localhost:8000"})()

    # Match cookie, form, and origin/referer
    req = MockRequest(
        token, origin="http://localhost:8000", referer="http://localhost:8000/some-path"
    )
    # Should run without error
    validate_csrf(req, token)  # type: ignore


def test_validate_csrf_token_mismatch():
    token_cookie = generate_csrf_token()
    token_form = generate_csrf_token()

    class MockRequest:
        def __init__(self, cookie_token: str):
            self.cookies = {settings.web_csrf_cookie_name: cookie_token}
            self.headers = {}
            self.url = type("URL", (), {"scheme": "http", "netloc": "localhost:8000"})()

    req = MockRequest(token_cookie)
    with pytest.raises(HTTPException) as excinfo:
        validate_csrf(req, token_form)  # type: ignore
    assert excinfo.value.status_code == 403


def test_validate_csrf_origin_mismatch():
    token = generate_csrf_token()

    class MockRequest:
        def __init__(self, cookie_token: str, origin: str):
            self.cookies = {settings.web_csrf_cookie_name: cookie_token}
            self.headers = {"origin": origin}
            self.url = type("URL", (), {"scheme": "http", "netloc": "localhost:8000"})()

    req = MockRequest(token, origin="http://malicious.com")
    with pytest.raises(HTTPException) as excinfo:
        validate_csrf(req, token)  # type: ignore
    assert excinfo.value.status_code == 403
