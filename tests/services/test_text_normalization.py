import pytest

from glintory.config import settings
from glintory.services.text_normalization import (
    normalize_excerpt,
    normalize_optional_text,
    normalize_string_list,
    normalize_title,
)


def test_normalize_common_behavior():
    # Unicode NFC normalization (e.g. decomposed character -> composed)
    # \u304b + \u3099 (か + ゛) -> \u304c (が)
    decomposed = "\u304b\u3099"
    composed = "\u304c"
    assert normalize_title(decomposed) == composed

    # Remove NUL byte
    assert normalize_title("hello\x00world") == "helloworld"

    # Line endings standardized to \n (except for title which rejects newlines or collapses them)
    # Rule: reject empty, unify line endings, collapse multiple spaces
    # For excerpt, \r\n should be unified to \n
    assert normalize_excerpt("hello\r\nworld") == "hello\nworld"
    assert normalize_excerpt("hello\rworld") == "hello\nworld"

    # Collapse spaces
    assert normalize_excerpt("hello   \t  world") == "hello world"


def test_normalize_title():
    # Empty title rejected
    with pytest.raises(ValueError):
        normalize_title("")
    with pytest.raises(ValueError):
        normalize_title("   \x00   ")

    # Max length clipping
    long_title = "a" * (settings.signal_title_max_chars + 10)
    expected = "a" * settings.signal_title_max_chars
    assert normalize_title(long_title) == expected


def test_normalize_excerpt():
    # None to empty string
    assert normalize_excerpt(None) == ""

    # Max length clipping
    long_excerpt = "a" * (settings.signal_excerpt_max_chars + 10)
    expected = "a" * settings.signal_excerpt_max_chars
    assert normalize_excerpt(long_excerpt) == expected


def test_normalize_optional_text():
    assert normalize_optional_text(None) is None
    assert normalize_optional_text("   hello   ") == "hello"
    assert normalize_optional_text("") is None


def test_normalize_string_list():
    # Strip whitespace, filter empty, case-insensitive deduplication preserving first occurrence and order, limit items
    tags = [
        "  Python  ",
        "python",
        "  ",
        "JavaScript",
        "javascript",
        "PYTHON",
        "TypeScript",
    ]
    cleaned, warnings = normalize_string_list(tags)
    assert cleaned == ("Python", "JavaScript", "TypeScript")
    assert len(warnings) == 0

    # Over 100 characters item should be skipped with warning
    long_tag = "x" * 105
    cleaned, warnings = normalize_string_list(["valid", long_tag])
    assert cleaned == ("valid",)
    assert len(warnings) == 1
    assert "exceeds" in warnings[0]

    # Max 100 items limit
    many_tags = [f"tag{i}" for i in range(150)]
    cleaned, warnings = normalize_string_list(many_tags)
    assert len(cleaned) == 100
    assert cleaned[-1] == "tag99"
    assert len(warnings) == 1
    assert "limit" in warnings[0]
