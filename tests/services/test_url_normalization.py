import pytest

from glintory.config import settings
from glintory.services.url_normalization import (
    InvalidSignalUrlError,
    SignalUrlTooLongError,
    normalize_url,
)


def test_normalize_url_basic():
    # Trim and lowercase
    assert normalize_url("  HTTPS://Example.COM/foo  ") == "https://example.com/foo"


def test_normalize_url_scheme_validation():
    # Only http and https allowed
    with pytest.raises(InvalidSignalUrlError):
        normalize_url("ftp://example.com")
    with pytest.raises(InvalidSignalUrlError):
        normalize_url("file:///etc/passwd")


def test_normalize_url_missing_host():
    with pytest.raises(InvalidSignalUrlError):
        normalize_url("https://")
    with pytest.raises(InvalidSignalUrlError):
        normalize_url("https:///path")


def test_normalize_url_credentials():
    with pytest.raises(InvalidSignalUrlError):
        normalize_url("https://user:password@example.com")


def test_normalize_url_fragment():
    assert normalize_url("https://example.com/foo#bar") == "https://example.com/foo"


def test_normalize_url_ports():
    assert normalize_url("https://example.com:443/foo") == "https://example.com/foo"
    assert normalize_url("http://example.com:80/foo") == "http://example.com/foo"
    # Keep non-default port
    assert (
        normalize_url("https://example.com:8443/foo") == "https://example.com:8443/foo"
    )


def test_normalize_url_trailing_slash():
    # Keep root trailing slash / or remove?
    # Spec: "root以外の末尾スラッシュを削除" -> root trailing slash should be handled (e.g. keep root or strip it if empty path, but root path itself is just / or empty. If we strip root slash: https://example.com/ -> https://example.com)
    # Let's say root trailing slash is removed if we treat it as empty path, but if we keep it, it's ok.
    # Spec says "root以外の末尾スラッシュを削除" so:
    assert normalize_url("https://example.com/foo/") == "https://example.com/foo"
    # Root path should not be stripped to empty, e.g. "https://example.com/" -> "https://example.com" or "https://example.com/"
    # If path is "/", it's root. Let's make "https://example.com/" normalize to "https://example.com" (or keep it, but standard is to strip path-only trailing slashes but keep root slash if we must, or strip it as well since "https://example.com" is identical).
    # Let's make "https://example.com/" normalize to "https://example.com" or "https://example.com" since it's the root.
    assert normalize_url("https://example.com/") == "https://example.com"


def test_normalize_url_query_params():
    # Sort and remove empty/tracking
    assert (
        normalize_url("https://example.com/foo?b=2&a=1&c=")
        == "https://example.com/foo?a=1&b=2"
    )


def test_normalize_url_tracking_params():
    assert (
        normalize_url(
            "https://example.com/foo?q=test&utm_source=twitter&UTM_MEDIUM=social&fbclid=123"
        )
        == "https://example.com/foo?q=test"
    )
    # Test tracking params with utm_ prefix
    assert (
        normalize_url("https://example.com/foo?utm_campaign=xyz&id=1")
        == "https://example.com/foo?id=1"
    )


def test_normalize_url_keep_params():
    # Keep important parameters
    assert (
        normalize_url("https://example.com/foo?id=1&page=2&q=query&ref=1&lang=en")
        == "https://example.com/foo?id=1&lang=en&page=2&q=query&ref=1"
    )


def test_normalize_url_max_length(monkeypatch):
    # Set max URL length limit to small value for testing
    monkeypatch.setattr(settings, "signal_url_max_chars", 30)
    with pytest.raises(SignalUrlTooLongError) as exc_info:
        normalize_url("https://example.com/this-is-too-long-url")
    # Verify no query string or full secret leaked in error message
    assert "https://example.com/this-is-too-long-url" not in str(exc_info.value)


def test_normalize_url_unicode_host():
    # Unicode host handles safely (punycode/idna)
    assert normalize_url("https://日本語.jp/foo") == "https://xn--wgv71a119e.jp/foo"


def test_normalize_url_deterministic():
    url = "https://example.com/foo?b=2&a=1&utm_source=test"
    assert normalize_url(url) == normalize_url(url)
