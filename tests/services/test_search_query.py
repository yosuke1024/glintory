import pytest

from glintory.services.search_query import build_safe_fts_query


def test_build_safe_fts_query_empty() -> None:
    """Verifies empty or whitespace-only inputs yield None match expression."""
    assert build_safe_fts_query(None).match_expression is None
    assert build_safe_fts_query("").match_expression is None
    assert build_safe_fts_query("   \n\t   ").match_expression is None


def test_build_safe_fts_query_single_and_multiple() -> None:
    """Verifies single and multiple words are quoted and joined with AND."""
    assert build_safe_fts_query("sqlite").match_expression == '"sqlite"'
    assert build_safe_fts_query("sqlite fts5").match_expression == '"sqlite" AND "fts5"'
    assert (
        build_safe_fts_query("  sqlite   fts5  ").match_expression
        == '"sqlite" AND "fts5"'
    )


def test_build_safe_fts_query_escaping_quotes() -> None:
    """Verifies double quotes are properly escaped by doubling them within the FTS5 phrase."""
    # Input: say "hello"
    # Tokens: ['say', '"hello"'] -> Escaped: ['say', '""hello""'] -> Quoted: ['"say"', '"""hello"""']
    parsed = build_safe_fts_query('say "hello"')
    assert parsed.match_expression == '"say" AND """hello"""'


def test_build_safe_fts_query_operators_neutralized() -> None:
    """Verifies special search operators are treated as literal text and enclosed in quotes."""
    parsed = build_safe_fts_query("self OR hosted NOT alternative NEAR* asterisk*")
    expected = '"self" AND "OR" AND "hosted" AND "NOT" AND "alternative" AND "NEAR*" AND "asterisk*"'
    assert parsed.match_expression == expected


def test_build_safe_fts_query_unicode_and_nul() -> None:
    """Verifies NUL characters are removed and diacritics are normalized to NFC."""
    # NUL char removal
    parsed = build_safe_fts_query("hello\x00world")
    assert parsed.match_expression == '"helloworld"'

    # Unicode NFC normalization: 'a\u0308' (a + dieresis) normalized to '\u00e4' (ä)
    parsed_unicode = build_safe_fts_query("a\u0308")
    assert parsed_unicode.normalized == "\u00e4"


def test_build_safe_fts_query_constraints() -> None:
    """Verifies strict query length and terms limit constraints."""
    # Total query length limits (200 characters)
    build_safe_fts_query(
        " ".join(["a" * 19] * 10)
    )  # 199 chars, 10 words (should succeed)
    with pytest.raises(ValueError, match="Search query cannot exceed 200 characters"):
        build_safe_fts_query(" ".join(["a" * 19] * 10) + " abc")

    # Word count limits (10 words)
    build_safe_fts_query(" ".join(["word"] * 10))  # should succeed
    with pytest.raises(ValueError, match="Search query cannot exceed 10 terms"):
        build_safe_fts_query(" ".join(["word"] * 11))

    # Individual word length limits (100 characters)
    build_safe_fts_query("a" * 100)  # should succeed
    with pytest.raises(
        ValueError, match="Each search term cannot exceed 100 characters"
    ):
        build_safe_fts_query("a" * 101)


def test_build_safe_fts_query_injection_safety() -> None:
    """Verifies SQL injection attempts inside FTS match query are neutralized."""
    parsed = build_safe_fts_query("test' OR 1=1; --")
    # All quotes and semicolons are safely enclosed inside FTS5 quoted phrases
    assert parsed.match_expression is not None
    assert "test'" in parsed.match_expression
    assert "--" in parsed.match_expression
    assert parsed.match_expression == '"test\'" AND "OR" AND "1=1;" AND "--"'
