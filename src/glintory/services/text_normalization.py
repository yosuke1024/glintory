import re
import unicodedata
from collections.abc import Sequence

from glintory.config import settings


def _normalize_common(text: str) -> str:
    # 1. Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)
    # 2. Remove NUL characters
    text = text.replace("\x00", "")
    # 3. Standardize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 4. Collapse multiple consecutive non-newline whitespaces to a single space
    text = re.sub(r"[^\S\n]+", " ", text)
    # 5. Trim leading and trailing whitespace
    text = text.strip()
    return text


def normalize_title(value: str) -> str:
    if not value or not isinstance(value, str):
        raise ValueError("Title cannot be empty")
    cleaned = _normalize_common(value)
    # Replace newlines with spaces for title and collapse spaces
    cleaned = cleaned.replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        raise ValueError("Title cannot be empty after normalization")

    max_len = settings.signal_title_max_chars
    return cleaned[:max_len]


def normalize_excerpt(value: str | None) -> str:
    if value is None:
        return ""
    cleaned = _normalize_common(value)
    max_len = settings.signal_excerpt_max_chars
    return cleaned[:max_len]


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _normalize_common(value)
    if not cleaned:
        return None
    return cleaned


def normalize_string_list(
    items: Sequence[str],
) -> tuple[tuple[str, ...], list[str]]:
    if not items:
        return (), []

    cleaned_items: list[str] = []
    seen_lower: set[str] = set()
    warnings: list[str] = []

    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = _normalize_common(item)
        if not cleaned:
            continue

        # Item length limit (100 characters)
        if len(cleaned) > 100:
            warnings.append(
                f"Skipped item because it exceeds 100 characters: {cleaned[:30]}..."
            )
            continue

        cleaned_lower = cleaned.lower()
        if cleaned_lower not in seen_lower:
            seen_lower.add(cleaned_lower)
            cleaned_items.append(cleaned)

    # Max items limit (100 items)
    if len(cleaned_items) > 100:
        warnings.append(
            f"Truncated list of items because it exceeds max items limit of 100. Total items: {len(cleaned_items)}"
        )
        cleaned_items = cleaned_items[:100]

    return tuple(cleaned_items), warnings
