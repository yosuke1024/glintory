import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    original: str
    normalized: str
    match_expression: str | None
    terms: tuple[str, ...]


def build_safe_fts_query(
    value: str | None,
) -> ParsedSearchQuery:
    """Parses and sanitizes raw user query string for FTS5 queries.

    Normalizes Unicode representation, enforces length limits, filters NUL bytes,
    and escapes raw operators/special characters. Returns a structured
    ParsedSearchQuery suitable for binding to parameterized SQL queries.

    Raises:
        ValueError: If query constraints (overall length, word count, word length) are violated.
    """
    if value is None:
        return ParsedSearchQuery(
            original="",
            normalized="",
            match_expression=None,
            terms=(),
        )

    # 1. Normalize Unicode to NFC
    normalized_val = unicodedata.normalize("NFC", value)

    # 2. Remove NUL characters
    normalized_val = normalized_val.replace("\x00", "")

    # 3. Constraint checking on total query length (limit to 200 chars)
    if len(normalized_val) > 200:
        raise ValueError("Search query cannot exceed 200 characters")

    # 4. Tokenize by splitting on whitespaces and newlines
    words = normalized_val.split()

    # 5. Enforce word count limit (max 10 terms)
    if len(words) > 10:
        raise ValueError("Search query cannot exceed 10 terms")

    terms_list: list[str] = []
    for word in words:
        # Enforce maximum length of individual terms (max 100 chars)
        if len(word) > 100:
            raise ValueError("Each search term cannot exceed 100 characters")
        if word:
            terms_list.append(word)

    if not terms_list:
        return ParsedSearchQuery(
            original=value,
            normalized="",
            match_expression=None,
            terms=(),
        )

    # 6. Escape each term into a quoted FTS5 phrase.
    # In FTS5, double quotes are escaped by doubling them inside a quoted string.
    # e.g. 'self-hosted' -> '"self-hosted"', 'a"b' -> '"a""b"'
    formatted_terms: list[str] = []
    for term in terms_list:
        escaped_term = term.replace('"', '""')
        formatted_terms.append(f'"{escaped_term}"')

    # 7. Join multiple terms using AND
    match_expression = " AND ".join(formatted_terms)

    return ParsedSearchQuery(
        original=value,
        normalized=" ".join(terms_list),
        match_expression=match_expression,
        terms=tuple(terms_list),
    )
