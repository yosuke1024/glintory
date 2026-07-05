from glintory.infrastructure.error_sanitizer import sanitize_error


def test_sanitize_error_truncation():
    # Message longer than 1000 characters should be truncated to 1000
    long_msg = "a" * 1200
    sanitized = sanitize_error(long_msg)
    assert len(sanitized) <= 1000
    assert sanitized.endswith("...")


def test_sanitize_error_newline_normalization():
    # Newlines should be normalized (replaced by spaces or single lines)
    msg = "line1\nline2\r\nline3\rline4"
    sanitized = sanitize_error(msg)
    assert "\n" not in sanitized
    assert "\r" not in sanitized
    assert "line1 line2 line3 line4" in sanitized


def test_sanitize_error_bearer_token():
    msg = "Bearer secret-token-12345"
    sanitized = sanitize_error(msg)
    assert "secret-token-12345" not in sanitized
    assert "Bearer" in sanitized


def test_sanitize_error_auth_header():
    msg = "Authorization: Bearer secret-token-12345"
    sanitized = sanitize_error(msg)
    assert "secret-token-12345" not in sanitized
    assert "Authorization" in sanitized


def test_sanitize_error_query_parameters():
    # Query parameters like ?api_key=xxx, &token=xxx, etc.
    msg = "Request failed at http://example.com/api?api_key=secret-key-123&foo=bar&token=secret-token"
    sanitized = sanitize_error(msg)
    assert "secret-key-123" not in sanitized
    assert "secret-token" not in sanitized
    assert (
        "foo=bar" in sanitized
    )  # non-sensitive params should remain or everything masked safely


def test_sanitize_error_database_url():
    msg = "Connection failed: sqlite:///private/path/database.sqlite3"
    sanitized = sanitize_error(msg)
    assert "sqlite:///" not in sanitized or "private/path" not in sanitized


def test_sanitize_error_combined():
    msg = (
        "Database error: sqlite:///private/path/database.sqlite3\n"
        "Authorization: Bearer secret-token-abc\n"
        "URL: http://api.com/v1?token=123456&api_key=abcdef"
    )
    sanitized = sanitize_error(msg)
    assert "sqlite:///private/path" not in sanitized
    assert "secret-token-abc" not in sanitized
    assert "123456" not in sanitized
    assert "abcdef" not in sanitized
    assert "\n" not in sanitized
