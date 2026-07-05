import asyncio
import email.utils
import ipaddress
import json
import random
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from glintory.config import settings


# Exceptions
class HttpRequestError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class HttpResponseTooLargeError(HttpRequestError):
    pass


class HttpResponseError(HttpRequestError):
    def __init__(
        self,
        message: str,
        status_code: int,
        headers: Mapping[str, str],
        body: str,
    ):
        super().__init__(message, status_code=status_code)
        self.headers = headers
        self.body = body


class HttpInvalidJsonError(HttpRequestError):
    pass


class HttpRetryExhaustedError(HttpRequestError):
    pass


# URL Safety Verification
def validate_url_safety(url: str) -> None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise HttpRequestError(f"Unsupported URL scheme: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise HttpRequestError("URL embedded credentials are not allowed.")
    host = parsed.hostname
    if not host:
        raise HttpRequestError("URL is missing a host.")
    
    host_lower = host.lower()
    if (
        host_lower in {"localhost", "localhost."} or host_lower.endswith(".localhost") or host_lower.endswith(".local")
    ):
        raise HttpRequestError("Access to localhost is not allowed.")
    
    ip_str = host_lower.strip("[]")
    try:
        ip = ipaddress.ip_address(ip_str)
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HttpRequestError(
                f"Access to private/local/reserved IP {ip_str} is not allowed."
            )
    except ValueError:
        pass


# Protocols
class HttpTextResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str
    text: str


class HttpJsonResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str

    def json(self) -> Any: ...


class HttpBytesResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    url: str
    content: bytes


class HttpClientProtocol(Protocol):
    async def get_text(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> HttpTextResponse: ...

    async def get_json(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> HttpJsonResponse: ...

    async def get_bytes(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> HttpBytesResponse: ...


# Concrete Implementations
class HttpTextResponseImpl:
    def __init__(
        self, status_code: int, headers: Mapping[str, str], url: str, text: str
    ):
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.text = text


class HttpJsonResponseImpl:
    def __init__(
        self, status_code: int, headers: Mapping[str, str], url: str, text: str
    ):
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self._text = text

    def json(self) -> Any:
        try:
            return json.loads(self._text)
        except json.JSONDecodeError as e:
            raise HttpInvalidJsonError(
                f"Failed to parse JSON response: {e}",
                status_code=self.status_code,
            ) from e


class HttpBytesResponseImpl:
    def __init__(
        self, status_code: int, headers: Mapping[str, str], url: str, content: bytes
    ):
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.content = content


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    if not retry_after:
        return None
    # 1. Try parsing as integer seconds
    try:
        return float(retry_after)
    except ValueError:
        pass
    # 2. Try parsing as HTTP-date
    try:
        dt = email.utils.parsedate_to_datetime(retry_after)
        if dt:
            now = datetime.now(dt.tzinfo or UTC)
            diff = (dt - now).total_seconds()
            return max(0.0, diff)
    except Exception:
        pass
    return None


def calculate_backoff(attempt: int, base: float, max_delay: float = 60.0) -> float:
    temp_delay = base * (2**attempt)
    # Full jitter (uniform distribution between 0 and temp_delay)
    jitter = random.uniform(0.0, temp_delay)
    delay = temp_delay + jitter
    return min(delay, max_delay)


class HttpxHttpClient:
    # Process-wide rate control structures
    _host_locks: dict[str, asyncio.Lock] = {}
    _host_last_time: dict[str, float] = {}
    _locks_lock = asyncio.Lock()

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        sleep_func=None,
        time_func=None,
        max_retries: int | None = None,
        min_host_interval_seconds: float | None = None,
        max_response_bytes: int | None = None,
        max_redirects: int | None = None,
    ):
        # We configure httpx.AsyncClient with follow_redirects=False
        # because we implement redirect validation manually for safety.
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._sleep_func = sleep_func or asyncio.sleep
        self._time_func = time_func or time.monotonic

        # Fallback to global settings if not specified
        self._max_retries = (
            max_retries if max_retries is not None else settings.http_max_retries
        )
        self._min_host_interval = (
            min_host_interval_seconds
            if min_host_interval_seconds is not None
            else settings.http_min_host_interval_seconds
        )
        self._max_response_bytes = (
            max_response_bytes
            if max_response_bytes is not None
            else settings.http_max_response_bytes
        )
        self._max_redirects = (
            max_redirects if max_redirects is not None else settings.http_max_redirects
        )
        self._backoff_base = settings.http_backoff_base_seconds
        self._user_agent = settings.http_user_agent

    async def _get_lock_for_host(self, host: str) -> asyncio.Lock:
        async with self._locks_lock:
            if host not in self._host_locks:
                self._host_locks[host] = asyncio.Lock()
            return self._host_locks[host]

    async def get_text(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> HttpTextResponse:
        return await self._request("GET", url, headers, timeout, params=params)

    async def get_json(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> HttpJsonResponse:
        res = await self._request("GET", url, headers, timeout, params=params)
        try:
            json.loads(res.text)
        except json.JSONDecodeError as e:
            raise HttpInvalidJsonError(
                f"Failed to parse JSON response: {e}",
                status_code=res.status_code,
            ) from e
        return HttpJsonResponseImpl(
            status_code=res.status_code,
            headers=res.headers,
            url=res.url,
            text=res.text,
        )

    async def get_bytes(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> HttpBytesResponse:
        return await self._request(
            "GET", url, headers, timeout, params=params, return_bytes=True
        )

    async def _request(
        self,
        method: str,
        base_url: str,
        headers: Mapping[str, str] | None,
        timeout_sec: float | None,
        params: Mapping[str, Any] | None = None,
        *,
        return_bytes: bool = False,
    ) -> Any:
        req_headers = dict(headers or {})
        if "user-agent" not in {k.lower() for k in req_headers}:
            req_headers["User-Agent"] = self._user_agent

        for attempt in range(self._max_retries + 1):
            try:
                url = base_url
                visited_urls = {url}
                redirect_count = 0

                while True:
                    # 1. URL Validation
                    validate_url_safety(url)
                    parsed = urlparse(url)
                    host = parsed.netloc or ""

                    # 2. Per-host Rate Control
                    lock = await self._get_lock_for_host(host)
                    async with lock:
                        last_time = self._host_last_time.get(host, 0.0)
                        now = self._time_func()
                        diff = now - last_time
                        if diff < self._min_host_interval:
                            wait_time = self._min_host_interval - diff
                            await self._sleep_func(wait_time)

                        # Establish timeouts
                        if timeout_sec is not None:
                            timeout = httpx.Timeout(timeout_sec)
                        else:
                            timeout = httpx.Timeout(
                                connect=settings.http_connect_timeout_seconds,
                                read=settings.http_read_timeout_seconds,
                                write=settings.http_write_timeout_seconds,
                                pool=settings.http_pool_timeout_seconds,
                            )

                        # Stream the response to apply size limit dynamically
                        async with self._client.stream(
                            method,
                            url,
                            headers=req_headers,
                            timeout=timeout,
                            params=params,
                        ) as response:
                            # Update rate limit tracking
                            self._host_last_time[host] = self._time_func()

                            # Check for redirect (3xx)
                            if response.status_code in (
                                301,
                                302,
                                303,
                                307,
                                308,
                            ):
                                redirect_count += 1
                                if redirect_count > self._max_redirects:
                                    raise HttpRequestError(
                                        "Max redirects exceeded.",
                                        status_code=response.status_code,
                                    )

                                location = response.headers.get("location")
                                if not location:
                                    raise HttpRequestError(
                                        "Redirect status without location header.",
                                        status_code=response.status_code,
                                    )

                                next_url = str(response.url.join(location))
                                if next_url in visited_urls:
                                    raise HttpRequestError(
                                        "Redirect loop detected.",
                                        status_code=response.status_code,
                                    )

                                visited_urls.add(next_url)
                                url = next_url
                                continue  # Follow redirect in the loop

                            # Read response content chunk by chunk with size limit
                            content = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=16384):
                                content.extend(chunk)
                                if len(content) > self._max_response_bytes:
                                    raise HttpResponseTooLargeError(
                                        f"Response size exceeded limit of {self._max_response_bytes} bytes.",
                                        status_code=response.status_code,
                                    )

                            if return_bytes:
                                # Ensure we handle errors even for bytes response
                                if response.status_code >= 400:
                                    # Fallback to decode error body for HttpRequestError context
                                    text_content = content.decode(
                                        response.encoding or "utf-8",
                                        errors="replace",
                                    )
                                    if response.status_code in (
                                        408,
                                        429,
                                        500,
                                        502,
                                        503,
                                        504,
                                    ):
                                        if attempt < self._max_retries:
                                            wait_sec = parse_retry_after(
                                                response.headers
                                            )
                                            if wait_sec is None:
                                                wait_sec = calculate_backoff(
                                                    attempt, self._backoff_base
                                                )
                                            else:
                                                wait_sec = min(wait_sec, 60.0)

                                            await self._sleep_func(wait_sec)
                                            break  # Break inner loop to retry
                                        raise HttpRetryExhaustedError(
                                            f"HTTP request failed with status code {response.status_code} after {self._max_retries} retries.",
                                            status_code=response.status_code,
                                        )
                                    raise HttpResponseError(
                                        f"HTTP request failed with status code {response.status_code}.",
                                        status_code=response.status_code,
                                        headers=dict(response.headers),
                                        body=text_content,
                                    )
                                return HttpBytesResponseImpl(
                                    status_code=response.status_code,
                                    headers=response.headers,
                                    url=url,
                                    content=bytes(content),
                                )

                            text_content = content.decode(
                                response.encoding or "utf-8", errors="replace"
                            )

                            # Handle error status codes
                            if response.status_code >= 400:
                                if response.status_code in (
                                    408,
                                    429,
                                    500,
                                    502,
                                    503,
                                    504,
                                ):
                                    if attempt < self._max_retries:
                                        wait_sec = parse_retry_after(response.headers)
                                        if wait_sec is None:
                                            wait_sec = calculate_backoff(
                                                attempt, self._backoff_base
                                            )
                                        else:
                                            # Clamp Retry-After to reasonable limits
                                            wait_sec = min(wait_sec, 60.0)

                                        await self._sleep_func(wait_sec)
                                        break  # Break inner loop to retry outer loop
                                    raise HttpRetryExhaustedError(
                                        f"HTTP request failed with status code {response.status_code} after {self._max_retries} retries.",
                                        status_code=response.status_code,
                                    )
                                raise HttpResponseError(
                                    f"HTTP request failed with status code {response.status_code}.",
                                    status_code=response.status_code,
                                    headers=dict(response.headers),
                                    body=text_content,
                                )

                            return HttpTextResponseImpl(
                                status_code=response.status_code,
                                  headers=response.headers,
                                url=url,
                                text=text_content,
                            )

            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
            ) as e:
                if attempt < self._max_retries:
                    wait_sec = calculate_backoff(attempt, self._backoff_base)
                    await self._sleep_func(wait_sec)
                else:
                    raise HttpRetryExhaustedError(
                        f"HTTP request failed after {self._max_retries} retries. Connection/Timeout error: {e}"
                    ) from e
            except httpx.HTTPError as e:
                # Other non-retryable httpx errors
                raise HttpRequestError(f"HTTP transport error: {e}") from e

        raise HttpRetryExhaustedError("HTTP request failed (retries exhausted).")

