import time

import httpx
import pytest

from glintory.infrastructure.http import (
    HttpInvalidJsonError,
    HttpRequestError,
    HttpResponseTooLargeError,
    HttpRetryExhaustedError,
    HttpxHttpClient,
)


@pytest.mark.asyncio
async def test_get_text_success():
    def handler(request: httpx.Request):
        return httpx.Response(200, text="hello world")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    res = await http_client.get_text("http://example.com")
    assert res.status_code == 200
    assert res.text == "hello world"
    assert res.url == "http://example.com"


@pytest.mark.asyncio
async def test_get_json_success():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"key": "value"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    res = await http_client.get_json("http://example.com")
    assert res.status_code == 200
    assert res.json() == {"key": "value"}


@pytest.mark.asyncio
async def test_get_invalid_json():
    def handler(request: httpx.Request):
        return httpx.Response(200, text="not a json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    with pytest.raises(HttpInvalidJsonError):
        await http_client.get_json("http://example.com")


@pytest.mark.asyncio
async def test_no_retry_on_404():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        return httpx.Response(404, text="Not Found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client)
    with pytest.raises(HttpRequestError) as exc_info:
        await http_client.get_text("http://example.com")

    assert exc_info.value.status_code == 404
    assert calls == 1  # No retry


@pytest.mark.asyncio
async def test_retry_on_500_then_success():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text="Internal Error")
        return httpx.Response(200, text="success")

    # Injected sleep mock to avoid real delays
    sleep_calls = []

    async def mock_sleep(seconds: float):
        sleep_calls.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(
        client=client, sleep_func=mock_sleep, min_host_interval_seconds=0
    )
    res = await http_client.get_text("http://example.com")
    assert res.status_code == 200
    assert res.text == "success"
    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


@pytest.mark.asyncio
async def test_retry_retry_after_header_seconds():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "10"})
        return httpx.Response(200, text="success")

    sleep_calls = []

    async def mock_sleep(seconds: float):
        sleep_calls.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(
        client=client, sleep_func=mock_sleep, min_host_interval_seconds=0
    )
    res = await http_client.get_text("http://example.com")
    assert res.status_code == 200
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 10.0


@pytest.mark.asyncio
async def test_retry_exhausted():
    def handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Error")

    async def mock_sleep(seconds: float):
        pass

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(
        client=client, sleep_func=mock_sleep, max_retries=2, min_host_interval_seconds=0
    )
    with pytest.raises(HttpRetryExhaustedError):
        await http_client.get_text("http://example.com")


@pytest.mark.asyncio
async def test_response_too_large():
    def handler(request: httpx.Request):
        # Return 100 bytes
        return httpx.Response(200, content=b"a" * 100)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # Limit to 50 bytes
    http_client = HttpxHttpClient(client=client, max_response_bytes=50)
    with pytest.raises(HttpResponseTooLargeError):
        await http_client.get_text("http://example.com")


@pytest.mark.asyncio
async def test_redirect_safety_and_max_redirects():
    def handler(request: httpx.Request):
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "/redirect"})
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_client = HttpxHttpClient(client=client, max_redirects=3)
    # This will trigger a redirect loop
    with pytest.raises(HttpRequestError) as exc_info:
        await http_client.get_text("http://example.com/redirect")
    assert (
        "loop" in str(exc_info.value).lower()
        or "limit" in str(exc_info.value).lower()
        or "redirect" in str(exc_info.value).lower()
    )


@pytest.mark.asyncio
async def test_rejects_file_scheme():
    http_client = HttpxHttpClient()
    with pytest.raises(HttpRequestError) as exc_info:
        await http_client.get_text("file:///etc/passwd")
    assert "scheme" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_rejects_embedded_credentials():
    http_client = HttpxHttpClient()
    with pytest.raises(HttpRequestError) as exc_info:
        await http_client.get_text("http://user:pass@example.com")
    assert (
        "credentials" in str(exc_info.value).lower()
        or "user" in str(exc_info.value).lower()
    )


@pytest.mark.asyncio
async def test_rate_control_per_host():
    call_times = []

    def handler(request: httpx.Request):
        call_times.append((request.url.host, time.time()))
        return httpx.Response(200, text="ok")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # Mocking sleep and time to control execution precisely
    current_time = 100.0
    sleeps = []

    def mock_time():
        return current_time

    async def mock_sleep(seconds: float):
        nonlocal current_time
        sleeps.append(seconds)
        current_time += seconds

    http_client = HttpxHttpClient(
        client=client,
        min_host_interval_seconds=2.0,
        sleep_func=mock_sleep,
        time_func=mock_time,
    )

    # 1. Request to host A
    await http_client.get_text("http://host-a.com/1")
    # 2. Request to host A again immediately (should sleep 2.0s)
    await http_client.get_text("http://host-a.com/2")
    # 3. Request to host B immediately (should not sleep because different host)
    await http_client.get_text("http://host-b.com/1")

    assert len(sleeps) == 1
    assert sleeps[0] == 2.0
